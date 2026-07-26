"""Final audio mastering: one pass, at the end, done properly.

What changed and why
--------------------
The old pipeline normalised audio in the *middle* (inside the vertical body
build) with a single-pass `loudnorm=I=-16:TP=-1.5`, then re-encoded that AAC two
more times during compositing and joining. Three problems:

1. **Single-pass loudnorm is dynamic.** It rides the gain as it goes, so the
   loudness wanders across the clip. Two-pass measures the whole file first and
   then applies one constant gain (`linear=true`) — transparent and even.
2. **-16 LUFS is the wrong target.** YouTube / TikTok / Instagram / LinkedIn all
   normalise to about **-14 LUFS**, so shipping at -16 just means our videos
   play quieter than everything around them in the feed.
3. **Three AAC generations.** Now the body carries the source audio untouched
   (`-c:a copy`) and this module is the only place audio is ever encoded.

Where the cut fades live
------------------------
A 30ms fade belongs at every audio *cut* — otherwise you hear a click. It cannot
be applied here, and the reason is worth writing down because it is an easy trap:
`afade=t=in:st=T` does not "fade in at T", it **mutes everything before T**.
Chaining one per junction therefore silences the whole reel rather than smoothing
its joins. (Verified the hard way: `pipeline/verify_reel.py` caught the resulting
-91 dB export.)

So cut fades are applied where the cut is actually made — at trim time, by
`pipeline/trim.py` and `pipeline/silence.py`, which fade each piece's own edges
before anything is joined. This module only fades the head and tail of the
finished reel, which is the one case `afade` handles correctly.

Noise reduction is available but **off by default**. StreamYard audio is usually
clean, and `afftdn` on already-clean speech dulls consonants more than it helps.
Turn it on per clip (`"denoise": true`) when a recording genuinely needs it.
"""
import json
import os
import re
import subprocess

import encode

# Social delivery target — matches what the platforms normalise to.
TARGET_I = -14.0        # integrated loudness, LUFS
TARGET_TP = -1.0        # true peak ceiling, dBTP
TARGET_LRA = 11.0       # loudness range, LU

FADE = 0.030            # 30ms — long enough to kill a click, short enough to be inaudible
DENOISE_FILTER = "afftdn=nf=-25"    # gentle; -25dB noise floor, not a scrub


def _fade_filters(total):
    """Head and tail fades for the finished reel.

    Only these two are safe with `afade` — see the module docstring on why a
    mid-file `afade=t=in` mutes everything before its start time.
    """
    out = ["afade=t=in:st=0:d=%.3f" % FADE]
    if total > 2 * FADE:
        out.append("afade=t=out:st=%.3f:d=%.3f" % (total - FADE, FADE))
    return out


def _chain(loudnorm, denoise, total):
    """Assemble the full audio filter chain.

    Order matters: denoise first (it works on the raw signal), then loudness
    normalisation, then fades last so the fade shape is applied to the final
    levels rather than being re-scaled by loudnorm afterwards.
    """
    parts = []
    if denoise:
        parts.append(DENOISE_FILTER)
    parts.append(loudnorm)
    parts += _fade_filters(total)
    return ",".join(parts)


def measure(src, denoise=False, total=None):
    """loudnorm pass 1 — analyse the file. Returns the measurement dict or None.

    The measurement must run through the same preceding filters that pass 2 will
    use (notably denoise, which changes level), otherwise pass 2 corrects against
    numbers that no longer describe its input.
    """
    total = total if total is not None else encode.duration(src)
    probe = "loudnorm=I=%s:TP=%s:LRA=%s:print_format=json" % (
        TARGET_I, TARGET_TP, TARGET_LRA)
    res = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-nostats", "-i", src,
         "-af", _chain(probe, denoise, total), "-vn", "-f", "null", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stderr = res.stderr.decode("utf-8", "replace")
    # loudnorm prints its JSON block to stderr at the very end of the run.
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    needed = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    return data if all(k in data for k in needed) else None


def master(src, out, denoise=False, two_pass=True, verbose=True):
    """Master `src` -> `out`: loudness-normalise + fade, video copied untouched.

    Args:
        src: joined reel with its original audio.
        out: destination path.
        denoise: apply gentle FFT noise reduction before normalising.
        two_pass: measure first (accurate, constant gain). Set False for draft
            renders, where the single-pass approximation is close enough and
            saves a full analysis read of the file.

    Returns the output path. Falls back to single-pass if measurement fails, so
    a render never dies on an audio analysis hiccup.
    """
    total = encode.duration(src)
    stats = None
    if two_pass:
        if verbose:
            print("  audio: measuring loudness (pass 1)")
        stats = measure(src, denoise=denoise, total=total)
        if stats is None and verbose:
            print("  audio: measurement failed — using single-pass loudnorm")

    if stats:
        loudnorm = (
            "loudnorm=I=%s:TP=%s:LRA=%s"
            ":measured_I=%s:measured_TP=%s:measured_LRA=%s:measured_thresh=%s"
            ":offset=%s:linear=true"
            % (TARGET_I, TARGET_TP, TARGET_LRA,
               stats["input_i"], stats["input_tp"], stats["input_lra"],
               stats["input_thresh"], stats["target_offset"]))
        if verbose:
            print("  audio: measured I=%s LUFS TP=%s LRA=%s -> %s LUFS (linear)"
                  % (stats["input_i"], stats["input_tp"], stats["input_lra"], TARGET_I))
    else:
        loudnorm = "loudnorm=I=%s:TP=%s:LRA=%s" % (TARGET_I, TARGET_TP, TARGET_LRA)

    chain = _chain(loudnorm, denoise, total)
    encode.run(["ffmpeg", "-y", "-i", src, "-af", chain,
                "-c:v", "copy"] + encode.audio_args()
               + ["-movflags", "+faststart", "-loglevel", "error", out])
    if verbose:
        print("  audio mastered -> %s%s"
              % (os.path.basename(out), " (denoised)" if denoise else ""))
    return out


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("usage: audio_master.py <in.mp4> <out.mp4> [--denoise]")
        sys.exit(1)
    master(sys.argv[1], sys.argv[2], denoise="--denoise" in sys.argv)
