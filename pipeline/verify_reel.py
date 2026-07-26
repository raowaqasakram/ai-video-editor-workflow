"""Automated QC on a finished reel — the checks, without the slow agent loop.

Everything here is a mechanical assertion about the exported file, run in a few
seconds after each build. It exists because the failures this pipeline actually
hits are silent ones: a frozen tail with no audio (the `overlay` longest-input
bug), a duration that does not match the parts, loudness off target, a caption
track that stops halfway through the answer. None of those raise an error during
rendering — you only notice them after uploading.

Checks:
    container    video/audio streams present, `+faststart` for web playback
    format       h264 / yuv420p / expected canvas / 30fps / aac 48k stereo
    duration     within tolerance of card + body + outro
    loudness     integrated LUFS near the -14 social target
    frozen tail  no long freeze at the end (the historical failure mode)
    captions     how much of the answer body is actually captioned

Exit status is 1 if any check FAILs, so this can gate a release. Warnings do not
fail the run.

Usage:
    python3 pipeline/verify_reel.py <reel.mp4> [--expect-duration S] [--captions f]
"""
import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audio_master  # noqa: E402
import encode  # noqa: E402

DURATION_TOLERANCE = 0.5      # seconds
LOUDNESS_TOLERANCE = 1.5      # LU either side of the target
FREEZE_LIMIT = 1.5            # a freeze longer than this at the tail is a defect
CAPTION_COVERAGE_MIN = 0.80   # brand rule is full caption coverage


class Report(object):
    """Collects PASS / WARN / FAIL lines and prints them as one block."""

    def __init__(self, path):
        self.path = path
        self.rows = []

    def ok(self, name, detail=""):
        self.rows.append(("PASS", name, detail))

    def warn(self, name, detail=""):
        self.rows.append(("WARN", name, detail))

    def fail(self, name, detail=""):
        self.rows.append(("FAIL", name, detail))

    @property
    def failed(self):
        return any(r[0] == "FAIL" for r in self.rows)

    def print(self):
        print("\nQC — %s" % os.path.basename(self.path))
        for status, name, detail in self.rows:
            mark = {"PASS": "  ok  ", "WARN": " warn ", "FAIL": " FAIL "}[status]
            print("[%s] %-16s %s" % (mark, name, detail))
        bad = sum(1 for r in self.rows if r[0] == "FAIL")
        warns = sum(1 for r in self.rows if r[0] == "WARN")
        print("%d check(s): %d failed, %d warning(s)\n" % (len(self.rows), bad, warns))


def _has_faststart(path):
    """True if `moov` precedes `mdat` — i.e. the file starts playing immediately."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(262144)
    except OSError:
        return False
    moov, mdat = head.find(b"moov"), head.find(b"mdat")
    return moov != -1 and (mdat == -1 or moov < mdat)


def measure_loudness(path):
    """Integrated LUFS of the finished file, via ebur128. None if unavailable."""
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", path,
         "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    match = re.findall(r"I:\s*(-?\d+\.?\d*)\s*LUFS",
                       res.stderr.decode("utf-8", "replace"))
    return float(match[-1]) if match else None


def frozen_tail(path, total, window=4.0):
    """Longest freeze detected in the final `window` seconds, in seconds.

    Targets the specific bug this pipeline has hit before: an alpha overlay layer
    longer than the body makes ffmpeg hold the last frame with no audio.
    """
    start = max(0.0, total - window)
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-ss", "%.3f" % start, "-i", path,
         "-vf", "freezedetect=n=-60dB:d=0.8", "-map", "0:v", "-f", "null", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    spans = re.findall(r"freeze_duration:\s*(\d+\.?\d*)",
                       res.stderr.decode("utf-8", "replace"))
    return max((float(s) for s in spans), default=0.0)


def caption_coverage(caps, body_dur):
    """Fraction of the answer body covered by a caption."""
    if not caps or body_dur <= 0:
        return 0.0
    covered = sum(max(0.0, min(float(c[1]), body_dur) - max(0.0, float(c[0])))
                  for c in caps)
    return min(1.0, covered / body_dur)


def verify(path, expect_duration=None, caps=None, body_dur=None,
           quality="final", orientation="vertical", check_loudness=True):
    """Run every check and return the Report."""
    rep = Report(path)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        rep.fail("exists", "missing or empty")
        return rep

    size_mb = os.path.getsize(path) / (1024.0 * 1024.0)
    total = encode.duration(path)
    p = encode.profile(quality, orientation)
    v, a = encode.streams(path)

    rep.ok("exists", "%.1f MB, %.2fs" % (size_mb, total))

    # ---- format ----------------------------------------------------------
    if v is None:
        rep.fail("video stream", "none found")
    else:
        fps = encode._fps_of(v)
        detail = "%s %dx%d %s @%.2ffps" % (v.get("codec_name"), v.get("width") or 0,
                                           v.get("height") or 0, v.get("pix_fmt"), fps)
        if (v.get("codec_name") == "h264" and v.get("pix_fmt") == "yuv420p"
                and (v.get("width"), v.get("height")) == (p["w"], p["h"])
                and abs(fps - p["fps"]) < 0.05):
            rep.ok("video format", detail)
        else:
            rep.fail("video format", "%s (expected h264 %dx%d yuv420p @%dfps)"
                     % (detail, p["w"], p["h"], p["fps"]))

    if a is None:
        rep.fail("audio stream", "none found — reels must carry audio")
    else:
        detail = "%s %sHz %dch" % (a.get("codec_name"), a.get("sample_rate"),
                                   a.get("channels") or 0)
        if (a.get("codec_name") == encode.AUDIO_CODEC
                and a.get("sample_rate") == encode.AUDIO_RATE
                and a.get("channels") == encode.AUDIO_CHANNELS):
            rep.ok("audio format", detail)
        else:
            rep.warn("audio format", "%s (expected aac %sHz %dch)"
                     % (detail, encode.AUDIO_RATE, encode.AUDIO_CHANNELS))

    if _has_faststart(path):
        rep.ok("faststart", "moov before mdat")
    else:
        rep.warn("faststart", "not web-optimised; add -movflags +faststart")

    # ---- duration --------------------------------------------------------
    if expect_duration is not None:
        delta = total - expect_duration
        detail = "%.2fs vs %.2fs expected (%+.2fs)" % (total, expect_duration, delta)
        if abs(delta) <= DURATION_TOLERANCE:
            rep.ok("duration", detail)
        else:
            rep.fail("duration", detail)

    # ---- loudness --------------------------------------------------------
    if check_loudness and a is not None:
        lufs = measure_loudness(path)
        if lufs is None:
            rep.warn("loudness", "could not measure")
        else:
            delta = lufs - audio_master.TARGET_I
            detail = "%.1f LUFS (target %.0f, %+.1f LU)" % (
                lufs, audio_master.TARGET_I, delta)
            if abs(delta) <= LOUDNESS_TOLERANCE:
                rep.ok("loudness", detail)
            else:
                rep.warn("loudness", detail)

    # ---- frozen tail -----------------------------------------------------
    freeze = frozen_tail(path, total)
    if freeze >= FREEZE_LIMIT:
        rep.fail("frozen tail", "%.1fs freeze near the end" % freeze)
    else:
        rep.ok("frozen tail", "none (longest %.1fs)" % freeze)

    # ---- captions --------------------------------------------------------
    if caps is not None and body_dur:
        cov = caption_coverage(caps, body_dur)
        detail = "%.0f%% of the %.1fs body, %d blocks" % (cov * 100, body_dur, len(caps))
        if cov >= CAPTION_COVERAGE_MIN:
            rep.ok("captions", detail)
        else:
            rep.warn("captions", detail + " — brand rule is full coverage")

    return rep


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("reel")
    ap.add_argument("--expect-duration", type=float, default=None)
    ap.add_argument("--captions", default=None)
    ap.add_argument("--body-duration", type=float, default=None)
    ap.add_argument("--quality", default="final")
    args = ap.parse_args()

    caps = None
    if args.captions and os.path.exists(args.captions):
        with open(args.captions) as fh:
            caps = json.load(fh)

    report = verify(args.reel, expect_duration=args.expect_duration, caps=caps,
                    body_dur=args.body_duration, quality=args.quality)
    report.print()
    sys.exit(1 if report.failed else 0)
