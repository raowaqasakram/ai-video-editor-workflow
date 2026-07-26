"""Real background replacement: cut the speaker out and put him on a studio set.

Uses Robust Video Matting (RVM), which is *recurrent* — it carries hidden state
from frame to frame, so the alpha edge stays put instead of crawling. That matters
more than raw per-frame accuracy: per-frame matting tools (rembg, MediaPipe) jitter
along the hairline, and that jitter is exactly what makes a composite look
computer-generated. RVM runs on the torch that whisper already installed, so this
needs nothing extra.

Measured on this footage (554x588 crop, inference at 512px): the matte holds
individual hair strands and the earbud, and frame-to-frame alpha change is
0.012-0.021 — most of which is the speaker actually moving.

Streaming design
----------------
    ffmpeg decode -> raw RGB on a pipe -> RVM + composite in numpy -> ffmpeg encode

Nothing is written to disk between the two ffmpeg processes. A 198s body is ~5940
frames; dumping those as PNGs would be tens of gigabytes, and holding them in RAM
is worse.

Making it look shot, not composited
-----------------------------------
Edge quality is the easy half. The giveaway is *light*: a face lit by flat room
light pasted onto a dark set reads as fake however clean the cutout is. So the
subject is exposure-matched down toward the plate, and a rim light is drawn from
the alpha edge — a real subject in front of a lit backdrop always picks up some
spill. Both are deliberately subtle; overdone, they look worse than nothing.
"""
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import encode  # noqa: E402

# Foreground geometry. Pinned even numbers: FACE_FG_CROP is 555 wide (odd), and
# yuv420p forces ffmpeg to crop 554, so a derived height disagrees with what ffmpeg
# produces. See screenshare_vertical.SET_WIN_H for the same trap.
#
# The subject is scaled UP and placed higher than the un-matted styles, and that is
# a composition fix rather than a preference. At the framing the other styles use
# (1080x1146 centred), removing the room left ~740px of empty backdrop above his
# head — 38% of the frame — so he read as a small cut-out floating on a gradient.
# Filling the frame the way a real studio shot would means overflowing the canvas
# on three sides and letting it crop, which is why the compositor below works in
# overlap rectangles instead of assuming the foreground fits.
FG_W, FG_H = 1350, 1432         # 1.25x the shipping crop
FG_X = (1080 - FG_W) // 2       # negative: overflows left/right, cropped
FG_Y = -16                      # head lands just below the name-tag band

INFER_W = 512                   # RVM is happiest 512-1024px; 512 is the speed/qualityknee
DOWNSAMPLE_RATIO = 0.5          # RVM's internal guidance, tuned for ~512px input

# --- realism controls ---------------------------------------------------------
# Exposure match: the plate is dark, the room light is flat and bright.
SUBJECT_GAIN = 0.90
# Rim light: accent spill along the alpha edge, as a backlit subject would pick up.
RIM_WIDTH = 9                   # px of edge band
RIM_STRENGTH = 0.38
RIM_RGB = (0, 174, 239)         # brand accent, matching the studio_bands plate

# Bottom fade — not cosmetic, it fixes a real artefact.
#
# The footage region is 1146px tall, so once the background is removed the torso
# simply STOPS at its bottom edge: a hard horizontal line across the chest that
# reads as the speaker having been sliced in half. The crop cannot be extended
# (there is no more picture below it), and scaling up until the torso reaches the
# frame bottom would push the face down into the captions and cut the shoulders,
# which the locked recipe forbids.
#
# Fading the alpha out over the last stretch instead lets him fall away into the
# set. Against a dark plate that reads as "lit from above, falling into shadow",
# which is what a real subject on a dim set does. The caption bar sits over most
# of this band anyway.
BOTTOM_FADE_PX = 220


def _load_model():
    import warnings
    warnings.filterwarnings("ignore")
    import torch
    torch.set_grad_enabled(False)
    model = torch.hub.load("PeterL1n/RobustVideoMatting", "mobilenetv3",
                           trust_repo=True).eval()
    return torch, model


def _edge_band(alpha):
    """Soft band just inside the alpha edge, for the rim light.

    Uses a box blur difference rather than a morphological dilate so it needs no
    extra dependency and comes out already feathered.
    """
    import cv2
    k = RIM_WIDTH | 1                       # box blur needs an odd kernel
    blurred = cv2.blur(alpha, (k, k))
    band = np.clip(blurred - alpha, 0.0, 1.0)       # outside-ish halo
    inner = np.clip(alpha - cv2.erode(alpha, np.ones((3, 3), np.uint8)), 0.0, 1.0)
    return cv2.blur(np.maximum(band, inner), (k, k))


def render_segment(video, start, dur, out, p, grade_vf, plate_png,
                   infer_w=INFER_W, verbose=True):
    """Matte `video[start:start+dur]` onto `plate_png` and encode to `out`.

    Video only (no audio) — the caller muxes audio, exactly like the other
    segment encoders in screenshare_vertical.
    """
    import cv2
    from PIL import Image

    torch, model = _load_model()
    plate = np.asarray(Image.open(plate_png).convert("RGB")).astype(np.float32)
    if plate.shape[:2] != (p["h"], p["w"]):
        plate = cv2.resize(plate, (p["w"], p["h"]), interpolation=cv2.INTER_LANCZOS4)

    infer_h = int(round(FG_H * infer_w / FG_W / 2) * 2)
    rim_rgb = np.array(RIM_RGB, dtype=np.float32)

    # Vertical ramp applied to every frame's alpha, so the torso dissolves into the
    # set instead of ending on a hard line. Built once.
    fade = np.ones((FG_H, 1), dtype=np.float32)
    if BOTTOM_FADE_PX > 0:
        ramp = np.linspace(1.0, 0.0, BOTTOM_FADE_PX, dtype=np.float32)
        fade[FG_H - BOTTOM_FADE_PX:, 0] = ramp ** 1.4      # ease out, not linear

    # Overlap rectangle between the (oversized, possibly negatively offset)
    # foreground and the canvas. Computed once; the foreground never moves.
    cx0, cy0 = max(0, FG_X), max(0, FG_Y)
    cx1, cy1 = min(p["w"], FG_X + FG_W), min(p["h"], FG_Y + FG_H)
    fx0, fy0 = cx0 - FG_X, cy0 - FG_Y
    fx1, fy1 = fx0 + (cx1 - cx0), fy0 + (cy1 - cy0)
    if cx1 <= cx0 or cy1 <= cy0:
        raise ValueError("foreground placement leaves nothing on canvas")

    # The grade and sharpen happen in the decode chain, so the matte sees exactly
    # the pixels that will be composited.
    enhance = ",".join(f for f in (grade_vf, "unsharp=5:5:0.4") if f)
    vf = "crop=555:588:362:0,scale=%d:%d%s" % (FG_W, FG_H,
                                               ("," + enhance) if enhance else "")
    dec = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-ss", "%.3f" % start, "-t", "%.3f" % dur,
         "-i", video, "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    enc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", "%dx%d" % (p["w"], p["h"]), "-r", str(p["fps"]), "-i", "-",
         "-an"] + encode.video_args(p) + [out],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    frame_bytes = FG_W * FG_H * 3
    rec = [None] * 4
    n = 0
    try:
        while True:
            raw = dec.stdout.read(frame_bytes)
            if not raw or len(raw) < frame_bytes:
                break
            fg = np.frombuffer(raw, np.uint8).reshape(FG_H, FG_W, 3)

            small = cv2.resize(fg, (infer_w, infer_h), interpolation=cv2.INTER_AREA)
            tensor = torch.from_numpy(np.ascontiguousarray(small)) \
                          .permute(2, 0, 1).float().div_(255.0)[None]
            _fgr, pha, *rec = model(tensor, *rec, downsample_ratio=DOWNSAMPLE_RATIO)
            alpha = cv2.resize(pha[0, 0].numpy(), (FG_W, FG_H),
                               interpolation=cv2.INTER_LINEAR) * fade

            subject = fg.astype(np.float32) * SUBJECT_GAIN
            rim = _edge_band(alpha)[:, :, None] * (RIM_STRENGTH * rim_rgb)

            out_frame = plate.copy()
            region = out_frame[cy0:cy1, cx0:cx1, :]
            a = alpha[fy0:fy1, fx0:fx1, None]
            region[:] = (subject[fy0:fy1, fx0:fx1] * a
                         + region * (1.0 - a)
                         + rim[fy0:fy1, fx0:fx1])
            np.clip(out_frame, 0, 255, out=out_frame)

            enc.stdin.write(out_frame.astype(np.uint8).tobytes())
            n += 1
            if verbose and n % 300 == 0:
                print("    matted %d frames (%.1fs)" % (n, n / float(p["fps"])),
                      flush=True)
    finally:
        if enc.stdin:
            enc.stdin.close()
        dec.stdout.close()
        dec.wait()
        enc_err = enc.stderr.read().decode("utf-8", "replace")
        enc.wait()

    if enc.returncode != 0 or n == 0:
        raise RuntimeError("matte encode failed after %d frames:\n%s"
                           % (n, "\n".join(enc_err.strip().splitlines()[-8:])))
    if verbose:
        print("    matted %d frames -> %s" % (n, os.path.basename(out)))
    return out


if __name__ == "__main__":
    import argparse
    import backdrop
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("video")
    ap.add_argument("out")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--dur", type=float, default=None)
    ap.add_argument("--style", default="studio_bands")
    ap.add_argument("--quality", default="final")
    args = ap.parse_args()
    prof = encode.profile(args.quality)
    plate = backdrop.plate(args.style, os.path.join(
        os.path.dirname(os.path.abspath(args.out)), "plate_%s.png" % args.style))
    render_segment(args.video, args.start,
                   args.dur if args.dur else encode.duration(args.video),
                   args.out, prof, "", plate)
