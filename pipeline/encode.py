"""Canonical encode profiles + the ffmpeg plumbing every other stage shares.

Why this module exists
----------------------
Each stage used to pick its own ffmpeg flags, and the final assembly joined the
parts with the concat *filter* — which re-encodes everything it touches. The
answer body therefore went through **three** lossy generations:

    segment encode  ->  overlay composite  ->  concat filter

Standardising every part on ONE profile lets the final join use the concat
*demuxer* with ``-c copy`` instead, so the body is encoded **twice** and the join
is near-instant rather than a full 1080x1920 render.

Concat-copy is only safe when every part agrees on codec, resolution, frame rate,
pixel format and audio layout, so those values live here and nowhere else. We
also pin a fixed, closed GOP (``-g`` / ``-keyint_min`` / ``-sc_threshold 0``): it
makes the encoder deterministic across parts and guarantees each part opens on a
keyframe, which is what the demuxer needs to stitch streams without re-encoding.

Quality ladder
--------------
``final`` is what ships. ``preview`` and ``draft`` exist so that iterating on
caption timing or chip placement does not cost a full-quality render. Measured on
an 84s question: the body render drops from ~44s to ~18s and the whole run from
~90s to ~47s. A draft is only ever used to *look at* timing — its output filename
carries the quality so it can never be mistaken for a shipping file.

Orientation
-----------
Profiles carry their own ``w``/``h``, so a future horizontal (or square) render
is a profile choice rather than a rewrite. Only ``vertical`` is wired into the
reel builder today; see RUNBOOK "Horizontal output (planned)".
"""
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

# Delivery canvases. Vertical is the shipping format; the others are the
# extension points for horizontal/square work (see module docstring).
ORIENTATIONS = {
    "vertical": (1080, 1920),
    "horizontal": (1920, 1080),
    "square": (1080, 1080),
}

# Quality ladder: (crf, x264 preset, cheap_filters).
#
# The canvas size deliberately does NOT change between rungs. Every text layer
# (captions, tech chips, name tag) is a Pillow PNG authored at the delivery
# canvas, so shrinking a draft would mean re-deriving every layout constant for
# the cheap path — a whole class of alignment bugs for no real gain.
#
# `cheap_filters` is what actually makes the cheap rungs cheap, and it was worth
# measuring rather than assuming. On a 20s body: the codec settings alone move a
# render from 10.0s to 9.6s (4% — the blurred-fit background is the bottleneck,
# not x264), but swapping in a cheap approximation of that blur as well takes it
# to 3.8s. So a draft changes BOTH, and `final` keeps the shipping look exactly.
LADDER = {
    "final": ("19", "fast", False),
    "preview": ("22", "fast", True),
    "draft": ("28", "ultrafast", True),
}

FPS = 30
GOP = 60                 # 2s at 30fps — fixed so every part's GOP structure matches
AUDIO_CODEC = "aac"
AUDIO_BITRATE = "192k"
AUDIO_RATE = "48000"
AUDIO_CHANNELS = 2


def profile(quality="final", orientation="vertical"):
    """Return the encode profile dict used by every stage.

    Args:
        quality: one of LADDER — "final" (ships), "preview", "draft".
        orientation: one of ORIENTATIONS.

    Raises:
        KeyError: on an unknown quality or orientation, so a typo fails loudly
            instead of silently shipping the wrong format.
    """
    if quality not in LADDER:
        raise KeyError("unknown quality %r (have: %s)" % (quality, ", ".join(LADDER)))
    if orientation not in ORIENTATIONS:
        raise KeyError("unknown orientation %r (have: %s)"
                       % (orientation, ", ".join(ORIENTATIONS)))
    crf, preset, cheap = LADDER[quality]
    w, h = ORIENTATIONS[orientation]
    return {"quality": quality, "orientation": orientation,
            "w": w, "h": h, "crf": crf, "preset": preset, "fps": FPS,
            "cheap_filters": cheap}


def scale_filter(p):
    """`scale=WxH` for this profile, for use inside a filter chain."""
    return "scale=%d:%d" % (p["w"], p["h"])


def video_args(p):
    """Canonical libx264 output args. Every part of a reel MUST use these."""
    return [
        "-c:v", "libx264", "-crf", p["crf"], "-preset", p["preset"],
        "-pix_fmt", "yuv420p", "-r", str(p["fps"]),
        # Fixed closed GOP: deterministic across parts, and required for the
        # `-c copy` join in join() to be reliable.
        "-g", str(GOP), "-keyint_min", str(GOP), "-sc_threshold", "0",
    ]


def audio_args():
    """Canonical AAC output args (only used when we cannot pass audio through)."""
    return ["-c:a", AUDIO_CODEC, "-b:a", AUDIO_BITRATE,
            "-ar", AUDIO_RATE, "-ac", str(AUDIO_CHANNELS)]


# ---------------------------------------------------------------------------
# Process plumbing
# ---------------------------------------------------------------------------
def run(cmd, quiet=True):
    """Run an ffmpeg/ffprobe command, raising with the *useful* part of stderr.

    ffmpeg failures are otherwise near-impossible to debug from a traceback:
    check=True alone reports the exit code and throws the diagnostics away.
    """
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0:
        tail = res.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(
            "ffmpeg failed (exit %d):\n  %s\n%s"
            % (res.returncode, " ".join(cmd[:14]) + " ...",
               "\n".join("  " + ln for ln in tail[-12:])))
    if not quiet:
        print(res.stderr.decode("utf-8", "replace").strip())
    return res


def parallel(fn, jobs, workers=3, label="job"):
    """Run `fn(*job)` over `jobs` concurrently, preserving input order.

    x264 already threads internally, so the win here is per-process ramp-up on
    multi-segment clips, not linear scaling — 3 workers is the sweet spot on a
    10-core machine. Raises the first exception once all workers have settled,
    so a failure can never be silently swallowed mid-render.
    """
    if len(jobs) <= 1:
        return [fn(*j) for j in jobs]
    results = [None] * len(jobs)
    errors = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, *j): i for i, j in enumerate(jobs)}
        for fut, i in futures.items():
            try:
                results[i] = fut.result()
            except Exception as exc:                      # noqa: BLE001
                errors.append("%s %d: %s" % (label, i, exc))
    if errors:
        raise RuntimeError("%d parallel %s(s) failed:\n%s"
                           % (len(errors), label, "\n".join(errors)))
    return results


# ---------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------
def duration(path):
    """Container duration in seconds."""
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path]).decode().strip()
    return float(out)


def streams(path):
    """Return (video_stream_dict, audio_stream_dict_or_None) via one ffprobe."""
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", path]).decode()
    v = a = None
    for s in json.loads(out).get("streams", []):
        if s.get("codec_type") == "video" and v is None:
            v = s
        elif s.get("codec_type") == "audio" and a is None:
            a = s
    return v, a


def has_audio(path):
    return streams(path)[1] is not None


def _fps_of(vstream):
    """Decode ffprobe's `30/1` rational frame rate into a float."""
    raw = vstream.get("r_frame_rate") or "0/0"
    try:
        num, den = raw.split("/")
        return float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def matches_profile(path, p):
    """True if `path` is already encoded exactly as `p` describes.

    Used to skip needless re-encodes (e.g. a cached, already-normalised outro).
    """
    try:
        v, a = streams(path)
    except (subprocess.CalledProcessError, ValueError):
        return False
    if v is None or a is None:
        return False
    return (v.get("codec_name") == "h264"
            and v.get("width") == p["w"] and v.get("height") == p["h"]
            and v.get("pix_fmt") == "yuv420p"
            and abs(_fps_of(v) - p["fps"]) < 0.01
            and a.get("codec_name") == AUDIO_CODEC
            and a.get("sample_rate") == AUDIO_RATE
            and a.get("channels") == AUDIO_CHANNELS)


def audio_passthrough_ok(path):
    """True if this file's audio can be `-c:a copy`'d into a canonical part.

    When the source already carries AAC/48k/stereo — which StreamYard recordings
    do — copying it means the audio survives the whole pipeline untouched and is
    only ever encoded once, in the final mastering pass.
    """
    a = streams(path)[1]
    return (a is not None
            and a.get("codec_name") == AUDIO_CODEC
            and a.get("sample_rate") == AUDIO_RATE
            and a.get("channels") == AUDIO_CHANNELS)


# ---------------------------------------------------------------------------
# Building blocks: stills, normalisation, joining
# ---------------------------------------------------------------------------
def still_clip(png, seconds, out, p):
    """A PNG -> `seconds`-long canonical clip with a silent stereo track.

    The silent track is not optional: the concat demuxer requires every part to
    carry the same stream layout, so a video-only card would break the join.
    """
    run(["ffmpeg", "-y",
         "-loop", "1", "-t", "%.3f" % seconds, "-i", png,
         "-f", "lavfi", "-t", "%.3f" % seconds,
         "-i", "anullsrc=channel_layout=stereo:sample_rate=%s" % AUDIO_RATE,
         "-vf", "%s,format=yuv420p" % scale_filter(p)]
        + video_args(p) + audio_args()
        + ["-shortest", "-loglevel", "error", out])
    return out


def normalize(src, out, p, cache=True):
    """Re-encode `src` to the canonical profile so it can be concat-copied.

    Skips the work when `src` already matches the profile, and (with `cache`)
    skips it when a valid `out` newer than `src` is already on disk. That matters
    for the brand outro, which is a static file previously re-encoded on every
    run of every question.
    """
    if matches_profile(src, p):
        if os.path.abspath(src) != os.path.abspath(out):
            run(["ffmpeg", "-y", "-i", src, "-c", "copy",
                 "-loglevel", "error", out])
        return out
    if (cache and os.path.exists(out)
            and os.path.getmtime(out) >= os.path.getmtime(src)
            and matches_profile(out, p)):
        print("  normalized part cached -> %s" % os.path.basename(out))
        return out

    silent = not has_audio(src)
    cmd = ["ffmpeg", "-y", "-i", src]
    if silent:
        cmd += ["-f", "lavfi",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=%s" % AUDIO_RATE]
    cmd += ["-vf", "%s,format=yuv420p" % scale_filter(p)]
    cmd += video_args(p) + audio_args()
    if silent:
        cmd += ["-map", "0:v", "-map", "1:a", "-shortest"]
    cmd += ["-movflags", "+faststart", "-loglevel", "error", out]
    run(cmd)
    return out


def concat_copy(parts, out, workdir):
    """Join canonical parts with the concat demuxer — no re-encode.

    Returns True on success. Absolute paths are required: the demuxer resolves
    `file` entries relative to the list file, and our clip directories contain
    spaces.
    """
    listf = os.path.join(workdir, "_join_concat.txt")
    with open(listf, "w") as fh:
        for part in parts:
            fh.write("file '%s'\n" % os.path.abspath(part))
    try:
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listf,
             "-c", "copy", "-movflags", "+faststart", "-loglevel", "error", out])
        return True
    except RuntimeError as exc:
        print("  concat-copy failed, falling back to re-encode: %s"
              % str(exc).splitlines()[0])
        return False


def concat_reencode(parts, out, p):
    """Fallback join via the concat filter. Costs a full extra generation."""
    cmd = ["ffmpeg", "-y"]
    for part in parts:
        cmd += ["-i", part]
    streams_expr = "".join("[%d:v][%d:a]" % (i, i) for i in range(len(parts)))
    cmd += ["-filter_complex",
            "%sconcat=n=%d:v=1:a=1[v][a]" % (streams_expr, len(parts)),
            "-map", "[v]", "-map", "[a]"]
    cmd += video_args(p) + audio_args()
    cmd += ["-movflags", "+faststart", "-loglevel", "error", out]
    run(cmd)
    return out


def join(parts, out, p, workdir, tolerance=0.35):
    """Join parts losslessly if we can, re-encoding only if we must.

    The copy path is verified before it is trusted: if the joined duration drifts
    from the sum of the parts by more than `tolerance` seconds, the copy is
    discarded and we fall back to the (lossy but always-correct) filter concat.
    That fallback is what keeps this safe to drop into an existing workflow —
    a copy-concat quirk degrades quality, it never produces a broken reel.
    """
    expected = sum(duration(part) for part in parts)
    if concat_copy(parts, out, workdir):
        got = duration(out)
        if abs(got - expected) <= tolerance:
            print("  joined %d parts by copy (no re-encode, %.1fs)" % (len(parts), got))
            return out
        print("  copy join drifted (%.2fs vs %.2fs expected) — re-encoding"
              % (got, expected))
    concat_reencode(parts, out, p)
    print("  joined %d parts by re-encode (%.1fs)" % (len(parts), duration(out)))
    return out
