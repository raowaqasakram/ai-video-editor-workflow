"""Measured, bounded auto colour correction (replaces the hardcoded eq guess).

The old framing chain carried a fixed correction:

    eq=brightness=0.02:contrast=1.05:saturation=1.04

That is a guess tuned on one recording. Room lighting, camera exposure and
StreamYard's own encoding drift week to week, so the same offset is sometimes
right and sometimes makes a clip worse.

Instead we *measure* the clip: sample frames through ffmpeg's `signalstats`
filter, read the luma average, luma range and saturation average, then emit a
correction that only ever fixes three specific faults:

    under/over-exposure  -> gamma
    a flat, low-contrast image -> contrast
    a washed-out image   -> saturation

Every adjustment is clamped to +/-8% and no hue/colour shift is ever applied.
The goal is "looks clean", never "looks graded" — CLAUDE.md's Video Enhancement
rule is explicit that we do not create artificial effects.

Measurement runs on the *region that actually ships* (pass the framing crop as
`pre_filter`), because the raw 1280x720 frame includes the StreamYard banner and
letterboxing that we crop away — measuring those would skew the result.

Usage:
    python3 pipeline/grade.py --analyze <video> [--start S] [--dur D]
"""
import argparse
import json
import math
import os
import subprocess
import tempfile

# Calibration
# -----------
# These bands were measured on this setup, not copied from a generic grader, and
# that distinction matters. We measure a *face crop*, not a whole scene: a lit
# face correctly sits well above mid-grey, so a scene-oriented target of ~0.48
# would read healthy footage as over-exposed and quietly darken every video.
# Sampling four Q-clips from the 17 Jul stream gives luma 0.653-0.665,
# range 0.82-0.83, saturation 0.023-0.025 — all of which should be treated as
# correct and left alone.
#
# Saturation is on ffmpeg's SATAVG scale normalised by the full-range maximum, so
# its numbers are an order of magnitude smaller than the luma ones. Do not
# compare the two, and re-measure before changing these bands.
LUMA_OK = (0.50, 0.72)      # inside this, exposure is fine — emit no gamma change
LUMA_FLOOR = 0.32           # at or below this, apply the maximum lift
RANGE_FLAT = 0.65           # below this the image is flat and wants contrast
RANGE_VERY_FLAT = 0.50      # at or below this, apply the maximum contrast boost
SAT_WASHED = 0.035          # below this, a small saturation lift is warranted
SAT_PUNCHY = 0.120          # above this, pull back slightly

# Hard clamps. Nothing this module emits may exceed these, ever.
CLAMP = {"contrast": (0.94, 1.08), "gamma": (0.94, 1.10), "saturation": (0.94, 1.06)}

# Subject mode — measuring the FACE instead of the shipping crop
# ---------------------------------------------------------------
# The bands above describe the *shipping crop*, which on this setup is mostly
# wall. That is fine while the wall and the speaker are lit alike, and wrong the
# moment they are not: on 25 Jul Q7 the camera metered for a large white wall and
# a mirror, so the crop measured luma 0.576 — comfortably "correct" — while his
# face sat at 0.358 and shipped looking almost black. The wall outvoted him.
#
# When `config.json -> grade_crop` names a tight box around the speaker, we
# measure THAT and switch to these constants instead. They are a different
# calibration for a different region, which is why they cannot share the bands
# above: measured on the same tight face box, the accepted 17 Jul look reads
# 0.421, not 0.66.
#
#   17 Jul Q6 face box  luma 0.421   <- the look already shipped and accepted
#   25 Jul Q7 face box  luma 0.358   <- rejected: "why am I black colour"
#
# The creator picked gamma 1.35 on the Q7 ladder (2026-08-04), which lands his
# face at 0.467 — so that, not the old accepted look, is the target.
SUBJECT_TARGET_LUMA = 0.47
SUBJECT_DEADZONE = 0.02     # within this of target, emit nothing

# Correcting a genuinely backlit subject needs more headroom than the +/-8%
# the crop-wide bands are allowed. Gamma is the safe lever for it: it pins both
# ends of the scale, so lifting the face by 35% moves a 0.95 wall to 0.96 and
# cannot blow the highlights the way brightness would.
SUBJECT_CLAMP = {"contrast": (0.94, 1.08), "gamma": (0.90, 1.60),
                 "saturation": (0.94, 1.10)}

# Fallback when analysis fails (no frames decoded, ffmpeg too old, etc.). This is
# the old "safe floor": a barely perceptible cleanup.
FALLBACK = "eq=contrast=1.03:saturation=0.98"

_CACHE_NAME = "grade_cache.json"


def _clamp(name, value, clamp=None):
    lo, hi = (clamp or CLAMP)[name]
    return max(lo, min(hi, value))


def _lerp(value, lo, hi, at_lo, at_hi):
    """Map `value` from range [lo, hi] onto [at_lo, at_hi], clamped at the ends."""
    if hi == lo:
        return at_hi
    t = max(0.0, min(1.0, (value - lo) / (hi - lo)))
    return at_lo + (at_hi - at_lo) * t


def stats_from_metadata(meta_path):
    """Parse an ffmpeg `metadata=print` dump of signalstats into 0..1 stats.

    Split out of `measure` so the parsing — which is where the bit-depth trap
    lives — is testable without running ffmpeg.
    """
    acc = {"YAVG": [], "YMIN": [], "YMAX": [], "SATAVG": []}
    depth = 8
    with open(meta_path) as fh:
        for line in fh:
            if "lavfi.signalstats." not in line:
                continue
            key, _, raw = line.strip().rpartition("=")
            key = key.rsplit(".", 1)[-1]
            try:
                val = float(raw)
            except ValueError:
                continue
            if key == "YBITDEPTH":
                depth = max(depth, int(val))
            elif key in acc:
                acc[key].append(val)

    if not acc["YAVG"]:
        return None
    full = float((2 ** depth) - 1)
    mean = lambda xs: sum(xs) / len(xs)                           # noqa: E731
    luma = mean(acc["YAVG"]) / full
    # Fall back to the "nothing to fix" value for any plane signalstats did not
    # report, so a missing stat can never itself provoke a correction.
    spread = ((mean(acc["YMAX"]) - mean(acc["YMIN"])) / full
              if acc["YMAX"] and acc["YMIN"] else RANGE_FLAT)
    sat = (mean(acc["SATAVG"]) / full if acc["SATAVG"]
           else (SAT_WASHED + SAT_PUNCHY) / 2)
    return {"luma": round(luma, 4), "range": round(spread, 4),
            "saturation": round(sat, 4), "frames": len(acc["YAVG"])}


def measure(video, start=0.0, dur=None, samples=10, pre_filter=None):
    """Sample frames and return {'luma', 'range', 'saturation'} normalised to 0..1.

    signalstats reports in the frame's native bit depth, so we divide by
    2**YBITDEPTH - 1 rather than assuming 8-bit.

    ⚠️ YBITDEPTH is measured **per frame** — it is the number of bits that frame
    actually uses, not the format's depth. A single low-contrast frame reports 7,
    and taking the last value read (as this did until 2026-08-06) halves `full`
    and **doubles every statistic**: on 1 Aug Q1, 99 frames said 8 and the last
    said 7, so a face measuring 0.455 was reported as 0.906 and the grade came
    back `gamma=0.900` — darkening the very video that was asked to be brighter.
    The scale is a property of the footage, so take the maximum.
    """
    if dur is None:
        dur = 10.0
    # Spread `samples` frames across the window, but never ask for more than
    # 10fps (pointless work) or less than 0.5fps (would miss short windows).
    fps = max(0.5, min(samples / max(dur, 0.1), 10.0))

    chain = []
    if pre_filter:
        chain.append(pre_filter)
    chain += ["fps=%.2f" % fps, "signalstats"]

    fd, meta_path = tempfile.mkstemp(suffix=".txt", prefix="signalstats_")
    os.close(fd)
    try:
        chain.append("metadata=print:file=%s" % meta_path)
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-nostats",
             "-ss", "%.3f" % start, "-i", video, "-t", "%.3f" % dur,
             "-vf", ",".join(chain), "-an", "-f", "null", "-"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        return stats_from_metadata(meta_path)
    except subprocess.CalledProcessError:
        return None
    finally:
        if os.path.exists(meta_path):
            os.remove(meta_path)


def subject_filter_from_stats(stats, target=None):
    """Turn a measurement of the SPEAKER into a bounded `eq=` filter string.

    Used when a `grade_crop` names a tight box around the speaker. Unlike the
    crop-wide path this solves for an explicit exposure target rather than a
    "leave it alone" band, because the whole point is that the surroundings are
    lit differently from the face and the automatic reading is therefore wrong.

    The lever is gamma: `out = in ** (1/gamma)`, so a lift that opens the face
    by a third leaves white at white. Contrast and saturation stay as timid as
    they are everywhere else — brightening a backlit face is a fix, restyling
    it is not.

    `target` overrides `SUBJECT_TARGET_LUMA` for one video (`config.json ->
    grade_target`). The default is the exposure he accepted on 25 Jul Q7; when
    he asks for a brighter render, aim higher rather than hardcoding a gamma —
    a gamma is only right for the clip it was eyeballed on, an exposure target
    carries to the next room.
    """
    if not stats:
        return FALLBACK

    aim = SUBJECT_TARGET_LUMA if target is None else float(target)
    luma = stats["luma"]
    if luma <= 0.0 or luma >= 1.0:                # degenerate; nothing sane to do
        gamma = 1.0
    elif abs(luma - aim) <= SUBJECT_DEADZONE:
        gamma = 1.0
    else:
        gamma = math.log(luma) / math.log(aim)

    contrast = (_lerp(stats["range"], RANGE_VERY_FLAT, RANGE_FLAT, 1.08, 1.03)
                if stats["range"] < RANGE_FLAT else 1.03)

    # A lifted face desaturates slightly, so pair a real lift with a touch of
    # saturation — enough to keep skin from going grey, not enough to tint it.
    if stats["saturation"] < SAT_WASHED:
        saturation = 1.06
    elif stats["saturation"] > SAT_PUNCHY:
        saturation = 0.96
    else:
        saturation = 1.04 if gamma > 1.15 else 1.0

    parts = []
    for name, value in (("contrast", contrast), ("gamma", gamma),
                        ("saturation", saturation)):
        value = _clamp(name, value, SUBJECT_CLAMP)
        if abs(value - 1.0) > 0.005:
            parts.append("%s=%.3f" % (name, value))
    return "eq=" + ":".join(parts) if parts else ""


def filter_from_stats(stats):
    """Turn measurements into a bounded `eq=` filter string (may be empty)."""
    if not stats:
        return FALLBACK

    # Contrast: only ever lifted, never reduced. A flat image gets up to +8%;
    # anything with a healthy spread gets the barely-there baseline.
    if stats["range"] < RANGE_FLAT:
        contrast = _lerp(stats["range"], RANGE_VERY_FLAT, RANGE_FLAT, 1.08, 1.03)
    else:
        contrast = 1.03

    # Gamma: lift a dark image, pull back a genuinely hot one, and — importantly —
    # do nothing at all when exposure is already inside LUMA_OK. Gamma rather than
    # brightness because it protects the black point instead of grey-washing the
    # shadows.
    if stats["luma"] < LUMA_OK[0]:
        gamma = _lerp(stats["luma"], LUMA_FLOOR, LUMA_OK[0], 1.10, 1.02)
    elif stats["luma"] > LUMA_OK[1]:
        gamma = 0.97
    else:
        gamma = 1.0

    # Saturation: a small lift when the image is washed out, a small pull-back
    # when it is already punchy, nothing in between.
    if stats["saturation"] < SAT_WASHED:
        saturation = 1.04
    elif stats["saturation"] > SAT_PUNCHY:
        saturation = 0.96
    else:
        saturation = 1.0

    parts = []
    for name, value in (("contrast", contrast), ("gamma", gamma),
                        ("saturation", saturation)):
        value = _clamp(name, value)
        if abs(value - 1.0) > 0.005:
            parts.append("%s=%.3f" % (name, value))
    return "eq=" + ":".join(parts) if parts else ""


def auto_filter(video, start=0.0, dur=None, pre_filter=None, cache_dir=None,
                verbose=True, subject=False, subject_target=None):
    """Measure `video` over [start, start+dur] and return the eq filter to apply.

    `subject=True` means `pre_filter` isolates the SPEAKER rather than the whole
    shipping crop, which selects the exposure-target calibration in
    `subject_filter_from_stats` — see the constants above for why the two cannot
    share one set of bands.

    Results are cached per (file, mtime, window, pre_filter, mode) in
    `cache_dir`, so re-running a question — the common case while tweaking
    captions — costs nothing. Never raises: an analysis failure degrades to
    FALLBACK, because a colour correction is not worth failing a render over.
    """
    key = None
    cache_path = None
    cache = {}
    if cache_dir:
        try:
            key = "%s|%d|%.2f|%.2f|%s|%s" % (os.path.abspath(video),
                                             os.path.getmtime(video),
                                             start, dur or -1, pre_filter or "",
                                             "subject@%s" % (subject_target,)
                                             if subject else "crop")
            cache_path = os.path.join(cache_dir, _CACHE_NAME)
            if os.path.exists(cache_path):
                with open(cache_path) as fh:
                    cache = json.load(fh)
            if key in cache:
                entry = cache[key]
                if verbose:
                    print("  grade (cached): %s" % (entry["filter"] or "(none)"))
                return entry["filter"]
        except (OSError, ValueError):
            cache, cache_path = {}, None      # cache is a nicety, never fatal

    try:
        stats = measure(video, start=start, dur=dur, pre_filter=pre_filter)
    except Exception as exc:                                     # noqa: BLE001
        print("  grade analysis failed (%s) — using safe floor" % exc)
        stats = None

    vf = (subject_filter_from_stats(stats, subject_target) if subject
          else filter_from_stats(stats))
    if verbose:
        if stats:
            print("  grade%s: luma=%.3f range=%.3f sat=%.3f -> %s"
                  % (" (subject)" if subject else "",
                     stats["luma"], stats["range"], stats["saturation"],
                     vf or "(none)"))
        else:
            print("  grade: analysis unavailable -> %s" % vf)

    if cache_path and key:
        try:
            cache[key] = {"filter": vf, "stats": stats}
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "w") as fh:
                json.dump(cache, fh, indent=2)
        except OSError:
            pass
    return vf


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--analyze", required=True, help="video to measure")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--dur", type=float, default=None)
    ap.add_argument("--crop", default=None,
                    help="framing crop to measure through, e.g. 555:588:362:0")
    ap.add_argument("--subject", action="store_true",
                    help="--crop isolates the speaker, not the shipping crop: "
                         "solve for SUBJECT_TARGET_LUMA instead of the "
                         "crop-wide bands")
    ap.add_argument("--subject-target", type=float, default=None,
                    help="exposure to aim the speaker at in --subject mode "
                         "(default %.2f). Raise it for a brighter render."
                         % SUBJECT_TARGET_LUMA)
    args = ap.parse_args()
    pre = ("crop=" + args.crop) if args.crop else None
    stats = measure(args.analyze, start=args.start, dur=args.dur, pre_filter=pre)
    print(json.dumps(stats, indent=2))
    vf = (subject_filter_from_stats(stats, args.subject_target) if args.subject
          else filter_from_stats(stats))
    print("filter: %s" % (vf or "(none)"))
