"""Cut one answer out of the livestream — the first step of every question.

This replaces the hand-typed ffmpeg trim in the RUNBOOK, for two reasons.

**Frame accuracy.** `-c copy` can only cut on a keyframe, so a stream-copied trim
lands wherever the nearest keyframe happens to be — up to a couple of seconds
off, which is why the recipe already re-encodes at CRF 16 for precise cuts.

**Cut fades.** Every audio cut needs a ~30ms fade or you hear a click at the
join. This is the correct place to apply them: the fade belongs to the piece being
cut, so it survives untouched through the body render, the overlay composite and
the copy-join all the way to the export. Doing it later is not possible — the
assembly stages deliberately never re-encode audio, and a mid-file `afade` in the
mastering pass would mute the reel (see pipeline/audio_master.py).

Quality: CRF 16, which is the locked trim quality — visually lossless, and the
grain the later CRF 19 passes are given to work with.

Usage:
    python3 pipeline/trim.py <stream.mp4> <out.mp4> --start 1080 --end 1164
    python3 pipeline/trim.py <stream.mp4> <out.mp4> --start 18:00 --dur 84
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import encode  # noqa: E402

TRIM_CRF = "16"
TRIM_PRESET = "medium"
FADE = 0.030
# 320k on this intermediate keeps the one pre-master audio generation inaudible;
# the reel is mastered down to 192k once, at the very end.
INTERMEDIATE_AUDIO_BITRATE = "320k"


def parse_time(value):
    """Accept seconds ("1080.5"), "MM:SS" or "HH:MM:SS"."""
    text = str(value).strip()
    if ":" not in text:
        return float(text)
    parts = [float(p) for p in text.split(":")]
    if len(parts) > 3:
        raise ValueError("cannot parse time %r" % value)
    total = 0.0
    for part in parts:
        total = total * 60 + part
    return total


def trim(src, out, start, end=None, dur=None, crf=TRIM_CRF, fade=FADE):
    """Cut [start, end] out of `src` into `out`, with fades on both audio edges.

    Exactly one of `end` or `dur` is required. Returns the output path.
    """
    if (end is None) == (dur is None):
        raise ValueError("pass exactly one of end= or dur=")
    if dur is None:
        dur = end - start
    if dur <= 0:
        raise ValueError("duration must be positive (got %.3f)" % dur)

    src_dur = encode.duration(src)
    if start + dur > src_dur + 0.5:
        raise ValueError("range %.2f-%.2f exceeds the source (%.2fs)"
                         % (start, start + dur, src_dur))

    af = ("afade=t=in:st=0:d=%.3f,afade=t=out:st=%.3f:d=%.3f"
          % (fade, max(0.0, dur - fade), fade))
    v, _ = encode.streams(src)
    fps = encode._fps_of(v) or encode.FPS

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    # -ss before -i seeks fast; the re-encode is what makes the cut exact.
    encode.run(["ffmpeg", "-y", "-ss", "%.3f" % start, "-i", src,
                "-t", "%.3f" % dur, "-af", af,
                "-c:v", "libx264", "-crf", crf, "-preset", TRIM_PRESET,
                "-pix_fmt", "yuv420p", "-r", "%.4f" % fps,
                "-g", str(encode.GOP), "-keyint_min", str(encode.GOP),
                "-sc_threshold", "0",
                "-c:a", "aac", "-b:a", INTERMEDIATE_AUDIO_BITRATE,
                "-ar", encode.AUDIO_RATE, "-ac", str(encode.AUDIO_CHANNELS),
                "-movflags", "+faststart", "-loglevel", "error", out])
    print("trimmed %.2f-%.2f (%.2fs) -> %s  [crf %s, %.0fms edge fades]"
          % (start, start + dur, encode.duration(out), out, crf, fade * 1000))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("source")
    ap.add_argument("output")
    ap.add_argument("--start", required=True, help="seconds, MM:SS or HH:MM:SS")
    ap.add_argument("--end", default=None, help="seconds, MM:SS or HH:MM:SS")
    ap.add_argument("--dur", default=None, help="duration in seconds")
    ap.add_argument("--crf", default=TRIM_CRF)
    args = ap.parse_args()
    trim(args.source, args.output,
         start=parse_time(args.start),
         end=parse_time(args.end) if args.end else None,
         dur=float(args.dur) if args.dur else None,
         crf=args.crf)
