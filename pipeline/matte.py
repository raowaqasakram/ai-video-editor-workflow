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
# This matches the un-matted styles exactly: crop 555x588 scaled to 1080 wide is
# 1080x1144, centred on the 1080x1920 canvas, so the subject occupies y 388..1532
# with an equal 388px band of room above and below.
#
# An earlier version scaled the subject up 1.25x and pushed it to the top of the
# frame, on the reasoning that removing the room left ~38% of the frame empty above
# his head. That reasoning was sound *for the blue gradient plate* — empty gradient
# is dead space. It stopped being true the moment the plate became a photograph of
# a real room: that space is now the room, and showing it is the entire point. The
# creator asked for the subject to sit in the original band with background visible
# top and bottom, which is what this restores.
FG_W, FG_H = 1080, 1144         # crop 555x588 scaled to canvas width
FG_X = (1080 - FG_W) // 2       # 0 — fits exactly, no horizontal overflow
FG_Y = (1920 - FG_H) // 2       # 388 — equal band of room above and below

INFER_W = 512                   # RVM is happiest 512-1024px; 512 is the speed/qualityknee
DOWNSAMPLE_RATIO = 0.5          # RVM's internal guidance, tuned for ~512px input

# --- realism controls ---------------------------------------------------------
# Exposure match. The 0.90 here was set when the plate was a near-black gradient and
# the subject had to be pulled down to sit in it. A photographed room is nowhere near
# that dark, and darkening him against it made the background the brightest thing in
# frame — the eye goes to the window instead of his face. Left at unity; the plate is
# pulled down slightly instead (backdrop.PHOTO["exposure"]).
SUBJECT_GAIN = 1.0
# Rim light: spill along the alpha edge, as a subject in front of a lit backdrop
# picks up.
#
# This used to be brand blue, which was a mistake worth spelling out. A rim light is
# the *plate's* light landing on the subject, so its colour is dictated by the set,
# not by the brand palette. Blue-edging a warm-lit room put a cyan outline around
# his shoulders and hair that no real light in the scene could have produced, and an
# impossible edge colour reads as "cut out" instantly — it was undoing the matte
# quality it was meant to sell. studio_real is lit by tungsten practicals, so the
# rim is now warm and much weaker. Brand colour belongs in the plate and the
# graphics, never on the subject's edge.
RIM_WIDTH = 9                   # px of edge band
RIM_STRENGTH = 0.22
RIM_RGB = (232, 196, 150)       # warm practical spill, matching the room's lamps

# Contact shadow. Without one the subject sits in front of the plate with no
# relationship to it, which the eye reads as a sticker on a photo however good the
# alpha is. A real body blocks the room light and drops a soft, offset shadow onto
# what is behind it. Offsets are down-and-left because the key sits upper-left.
SHADOW_OFFSET = (-26, 30)       # dx, dy in px
SHADOW_BLUR = 55                # very soft: the backdrop is metres behind him
SHADOW_STRENGTH = 0.45

# Bottom edge of the subject.
#
# The crop ends mid-chest, so once the room is removed the torso simply STOPS at the
# band's lower edge. Against the old near-black plate that hard line looked like the
# speaker had been sliced in half, so the alpha was faded out over the last 220px to
# let him fall away into shadow.
#
# On a photograph of a real room that trade goes the other way. The creator's note
# was explicit — cut at that line, do not dissolve — and he is right: a long fade
# over a *visible* room makes him look semi-transparent, like a ghost standing in
# an office, which is far worse than an honest edge. A short fade is kept only to
# take the aliasing off the cut; it is not enough to read as a dissolve.
BOTTOM_FADE_PX = 18


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


def _soften(mask, quarter):
    """Very wide blur of a full-canvas mask, done at quarter resolution.

    A sigma-55 gaussian at 1080x1920 on every frame is real money; for a shadow this
    soft the downscale is invisible, and it makes the blur roughly an order of
    magnitude cheaper.
    """
    import cv2
    small = cv2.resize(mask, quarter, interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), SHADOW_BLUR / 4.0)
    return cv2.resize(small, (mask.shape[1], mask.shape[0]),
                      interpolation=cv2.INTER_LINEAR)


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

    # Same overlap maths again for the offset shadow, which lands on a different
    # rectangle and can clip differently at the canvas edges.
    sdx, sdy = SHADOW_OFFSET
    sx0, sy0 = max(0, FG_X + sdx), max(0, FG_Y + sdy)
    sx1, sy1 = min(p["w"], FG_X + sdx + FG_W), min(p["h"], FG_Y + sdy + FG_H)
    sfx0, sfy0 = sx0 - (FG_X + sdx), sy0 - (FG_Y + sdy)
    sfx1, sfy1 = sfx0 + (sx1 - sx0), sfy0 + (sy1 - sy0)
    quarter = (p["w"] // 4, p["h"] // 4)

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

            # Shadow first: it darkens the plate, then the subject goes on top of
            # the already-shadowed backdrop.
            shad = np.zeros((p["h"], p["w"]), dtype=np.float32)
            shad[sy0:sy1, sx0:sx1] = alpha[sfy0:sfy1, sfx0:sfx1]
            shad = _soften(shad, quarter)
            out_frame = plate * (1.0 - SHADOW_STRENGTH * shad[:, :, None])

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
    ap.add_argument("--photo", default=None,
                    help="real room photo to use as the plate instead of the "
                         "rendered set")
    args = ap.parse_args()
    prof = encode.profile(args.quality)
    plate = backdrop.plate(args.style, os.path.join(
        os.path.dirname(os.path.abspath(args.out)), "plate_%s.png" % args.style),
        photo=args.photo)
    render_segment(args.video, args.start,
                   args.dur if args.dur else encode.duration(args.video),
                   args.out, prof, "", plate)
