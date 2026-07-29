"""Find and remove dead air, so the answer is paced like a reel.

Short-form video lives or dies on pacing. A live answer contains thinking pauses,
"umm"s and dead space that are completely natural on a stream and deadly in a
90-second reel. Word timings (from pipeline/transcribe.py) tell us exactly where
those gaps are.

This module is deliberately **two steps, not one**:

1. `report()` — read-only. Shows every gap and what the clip would be trimmed to.
   Run this first; a "gap" can be a dramatic beat you want to keep.
2. `apply()` — cuts the source clip down to the kept ranges.

Why it works at the *trim* stage and not later: cutting the body after captions
are authored would invalidate every caption timestamp. The trim step already
re-encodes at CRF 16 (locked recipe), so tightening there costs nothing extra and
captions get authored once, against the final body. If you tighten a clip that
already has captions, convert their times with `remap_captions()`.

Every cut carries a 30ms audio fade, or you hear a click at each join.

Usage:
    python3 pipeline/silence.py <clip.mp4> <words.json>                 # report
    python3 pipeline/silence.py <clip.mp4> <words.json> --apply out.mp4
    python3 pipeline/silence.py <clip.mp4> <words.json> --min-gap 0.8
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import encode  # noqa: E402

MIN_GAP = 0.70      # gaps shorter than this are natural speech rhythm — keep them
PAD = 0.12          # breathing room left on each side of a cut
TRIM_CRF = "16"     # matches the locked recipe's trim quality
FADE = 0.030        # 30ms audio fade at every cut edge


def find_gaps(words, min_gap=MIN_GAP, clip_dur=None):
    """Return [(start, end), ...] spans of silence at least `min_gap` long.

    Includes the lead-in before the first word and the tail after the last, which
    are usually the biggest single wins.
    """
    gaps = []
    if not words:
        return gaps
    if words[0]["start"] >= min_gap:
        gaps.append((0.0, words[0]["start"]))
    prev = words[0]["end"]
    for w in words[1:]:
        if w["start"] - prev >= min_gap:
            gaps.append((prev, w["start"]))
        prev = max(prev, w["end"])
    if clip_dur and clip_dur - prev >= min_gap:
        gaps.append((prev, clip_dur))
    return gaps


def keep_ranges(words, clip_dur, min_gap=MIN_GAP, pad=PAD):
    """Invert the gaps into the ranges worth keeping, with `pad` on each side.

    Ranges are merged when padding makes them touch, so we never emit a cut so
    short that it reads as a glitch.
    """
    gaps = find_gaps(words, min_gap=min_gap, clip_dur=clip_dur)
    keeps, cursor = [], 0.0
    for gs, ge in gaps:
        start, end = cursor, gs + pad
        if end - start > 0.05:
            keeps.append([max(0.0, start), min(clip_dur, end)])
        cursor = max(0.0, ge - pad)
    if cursor < clip_dur:
        keeps.append([cursor, clip_dur])

    merged = []
    for start, end in keeps:
        if merged and start - merged[-1][1] < 0.08:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    return [[round(a, 3), round(b, 3)] for a, b in merged if b - a > 0.15]


def remap_time(t, keeps):
    """Map a time in the ORIGINAL clip to its time in the tightened clip.

    A timestamp inside a removed gap snaps to the start of the next kept range,
    which is the only sensible answer — that moment no longer exists.
    """
    elapsed = 0.0
    for start, end in keeps:
        if t < start:
            return round(elapsed, 2)
        if t <= end:
            return round(elapsed + (t - start), 2)
        elapsed += end - start
    return round(elapsed, 2)


def remap_captions(caps, keeps):
    """Rewrite a caption timeline for a tightened clip, dropping emptied blocks."""
    out = []
    for cap in caps:
        start, end = remap_time(float(cap[0]), keeps), remap_time(float(cap[1]), keeps)
        if end - start < 0.3:        # the words behind this caption were cut away
            continue
        out.append([start, end] + list(cap[2:]))
    return out


def report(clip, words, min_gap=MIN_GAP, pad=PAD):
    """Print what would be trimmed. Returns the keep ranges without touching disk."""
    clip_dur = encode.duration(clip)
    gaps = find_gaps(words, min_gap=min_gap, clip_dur=clip_dur)
    keeps = keep_ranges(words, clip_dur, min_gap=min_gap, pad=pad)
    kept = sum(e - s for s, e in keeps)

    print("%s — %.2fs, %d word timings" % (os.path.basename(clip), clip_dur, len(words)))
    print("dead air >= %.2fs (%d span%s):" % (min_gap, len(gaps), "" if len(gaps) == 1 else "s"))
    for gs, ge in gaps:
        print("  %7.2f - %7.2f  (%.2fs)" % (gs, ge, ge - gs))
    print("keep %d range(s), %.2fs -> %.2fs (%.0f%% of original, %.2fs removed)"
          % (len(keeps), clip_dur, kept, 100.0 * kept / clip_dur, clip_dur - kept))
    return keeps


def apply(clip, keeps, out, crf=TRIM_CRF, workdir=None, speed=1.0):
    """Cut `clip` down to `keeps` and write `out`, with a 30ms fade at each edge.

    Re-encodes at CRF 16 — the same quality the manual trim step uses — because
    stream-copying cannot cut on a non-keyframe accurately.

    `speed` > 1 also plays the result faster (`setpts` + pitch-preserving
    `atempo`). It belongs in THIS pass rather than a separate one: tightening and
    speeding are both "how long is this clip", and doing them together costs one
    encode generation instead of two. Times returned by remap_time/remap_captions
    are in the tightened clip's own timebase, so divide them by `speed` afterwards.
    """
    if not 0.5 <= speed <= 2.0:
        raise ValueError("speed %r outside atempo's single-pass range 0.5-2.0" % speed)
    workdir = workdir or os.path.join(os.path.dirname(os.path.abspath(out)), "_tighten_work")
    os.makedirs(workdir, exist_ok=True)
    v, _ = encode.streams(clip)
    fps = encode._fps_of(v) or encode.FPS

    parts = []
    for i, (start, end) in enumerate(keeps):
        dur = round(end - start, 3)
        out_dur = dur / speed
        part = os.path.join(workdir, "keep_%02d.mp4" % i)
        # atempo first, so the fades stay 30ms in the OUTPUT and still kill the
        # click at the join rather than being squeezed to 30/speed ms.
        af = ("atempo=%.6f," % speed if speed != 1.0 else "") + (
            "afade=t=in:st=0:d=%.3f,afade=t=out:st=%.3f:d=%.3f"
            % (FADE, max(0.0, out_dur - FADE), FADE))
        vf = ["-vf", "setpts=PTS/%.6f" % speed] if speed != 1.0 else []
        # `-t` goes BEFORE `-i` so it bounds the INPUT. After `-i` it bounds the
        # OUTPUT, and with setpts speeding the video ffmpeg simply reads
        # dur*speed seconds of input to fill dur seconds of output — every part
        # came out full-length and holding the wrong footage.
        encode.run(["ffmpeg", "-y", "-ss", "%.3f" % start, "-t", "%.3f" % dur,
                    "-i", clip] + vf + ["-af", af,
                    "-c:v", "libx264", "-crf", crf, "-preset", "medium",
                    "-pix_fmt", "yuv420p", "-r", "%.4f" % fps,
                    "-g", str(encode.GOP), "-keyint_min", str(encode.GOP),
                    "-sc_threshold", "0",
                    # 320k keeps this intermediate generation inaudible; the reel
                    # is mastered to 192k once, at the end.
                    "-c:a", "aac", "-b:a", "320k", "-ar", encode.AUDIO_RATE,
                    "-ac", str(encode.AUDIO_CHANNELS),
                    "-loglevel", "error", part])
        parts.append(part)
        print("  keep %.2f-%.2f (%.2fs -> %.2fs) -> %s"
              % (start, end, dur, out_dur, os.path.basename(part)))

    if len(parts) == 1:
        encode.run(["ffmpeg", "-y", "-i", parts[0], "-c", "copy",
                    "-movflags", "+faststart", "-loglevel", "error", out])
    else:
        encode.concat_copy(parts, out, workdir) or encode.concat_reencode(
            parts, out, encode.profile("final"))
    print("tightened -> %s (%.2fs)" % (out, encode.duration(out)))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("clip")
    ap.add_argument("words", help="words.json from pipeline/transcribe.py")
    ap.add_argument("--apply", metavar="OUT", default=None,
                    help="write the tightened clip instead of only reporting")
    ap.add_argument("--min-gap", type=float, default=MIN_GAP)
    ap.add_argument("--pad", type=float, default=PAD)
    ap.add_argument("--captions", default=None,
                    help="captions.json to remap alongside --apply")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="also play the result faster, e.g. 1.4 (0.5-2.0)")
    args = ap.parse_args()

    with open(args.words) as fh:
        words = json.load(fh)
    keeps = report(args.clip, words, min_gap=args.min_gap, pad=args.pad)

    if args.apply:
        apply(args.clip, keeps, args.apply, speed=args.speed)
        if args.captions:
            with open(args.captions) as fh:
                caps = json.load(fh)
            remapped = remap_captions(caps, keeps)
            if args.speed != 1.0:
                remapped = [[round(c[0] / args.speed, 2), round(c[1] / args.speed, 2)]
                            + list(c[2:]) for c in remapped]
            dest = os.path.splitext(args.captions)[0] + ".tightened.json"
            with open(dest, "w") as fh:
                json.dump(remapped, fh, ensure_ascii=False, indent=2)
            print("remapped %d/%d captions -> %s" % (len(remapped), len(caps), dest))
