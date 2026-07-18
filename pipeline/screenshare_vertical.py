"""Screen-share-aware vertical (9:16) builder for StreamYard reels.

StreamYard's screen-share layout is FIXED: the shared screen sits in a constant
rectangle on the right, the speaker's camera is a small PIP on the left. So when
the presenter shares their screen we must NOT crop to the face — we drop the face
entirely and show the *whole* shared screen, zoomed to fit so everything stays
readable (the user's rule). Face-only stretches keep the normal blurred-fit
framing (both shoulders, banner removed).

Pipeline:
  1. detect_share_segments()  -> time ranges where the screen is shared
     (StreamYard paints a cyan/green gradient background in screen-share mode;
      we detect it by the top-left corner going strongly blue-over-red).
  2. render()                 -> encode each segment with the right framing and
     concatenate, then mux the loudnorm'd original audio back in.

Text/branding is baked with Pillow (this ffmpeg has no drawtext/libass).
"""
import os
import sys
import subprocess

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import overlays as ro  # noqa: E402  (font loader + brand constants)

# ---------------------------------------------------------------------------
# Brand / layout constants
# ---------------------------------------------------------------------------
W, H = 1080, 1920
ACCENT = (0, 174, 239, 255)     # #00AEEF  (matches cards/captions)
WHITE = (255, 255, 255, 255)
BG = (11, 15, 20, 255)          # #0B0F14

# StreamYard fixed screen-share rectangle inside the 1280x720 canvas (measured):
#   shared screen  x=248 y=116  1022x410  (bottom is under SY's own banner;
#   height trimmed to 410 so the banner avatar doesn't peek into the corner)
SHARE_CROP = (1022, 410, 248, 116)      # w, h, x, y
#   camera PIP (speaker) box on the LEFT in screen-share mode (measured):
FACE_PIP_CROP = (232, 158, 8, 282)      # w, h, x, y

# Stacked layout: shared screen on TOP, speaker camera BELOW (user's spec), so
# both are visible. Both sit in branded rounded windows.
SCREEN_W = 1010
SCREEN_H = round(SHARE_CROP[1] * SCREEN_W / SHARE_CROP[0])   # keep aspect (~405)
SCREEN_X = (W - SCREEN_W) // 2
SCREEN_Y = 380                       # both wells sit lower (bottom was too empty)

FACE_W = 560
FACE_H = round(FACE_PIP_CROP[1] * FACE_W / FACE_PIP_CROP[0])  # keep aspect (~381)
FACE_X = (W - FACE_W) // 2
FACE_Y = 940                         # camera below the screen; clear of captions (~1520)
RADIUS = 18

# Face-only framing (blurred-fit, from config/settings.yaml -> social.framing)
FACE_FG_CROP = "555:588:362:0"
FACE_BG = "scale=-1:1920,crop=1080:1920,boxblur=26:2,eq=brightness=-0.16:saturation=1.1"
FACE_ENH = "eq=brightness=0.02:contrast=1.05:saturation=1.04,unsharp=5:5:0.4"
LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"

# x264 settings for the big 1080x1920 encodes. `fast` is visually indistinguishable
# from `medium` at crf 19 for this content but ~3-4x quicker.
CRF = "19"
PRESET = "fast"

NAME = "Rao Waqas Akram"
TITLE = "Sr. Software Engineer | Mentor"


# ---------------------------------------------------------------------------
# Screen-share detection
# ---------------------------------------------------------------------------
def _corner_is_share(thumb):
    """True if this small RGB frame is a StreamYard screen-share layout."""
    tl = thumb[0:60, 0:90].astype(np.float32)
    return (tl[:, :, 2].mean() - tl[:, :, 0].mean()) > 25 and tl[:, :, 1].mean() > 120


def detect_share_segments(video, fps=10, min_len=3.0):
    """Return [(start_s, end_s), ...] where the screen is shared.

    Samples the video at `fps`, flags screen-share frames by the corner test,
    and merges contiguous runs (dropping runs shorter than `min_len`).
    """
    import glob
    import tempfile
    tmp = tempfile.mkdtemp(prefix="share_det_")
    subprocess.run(["ffmpeg", "-y", "-i", video, "-vf", f"fps={fps},scale=320:180",
                    os.path.join(tmp, "f_%05d.png"), "-loglevel", "error"], check=True)
    files = sorted(glob.glob(os.path.join(tmp, "f_*.png")))
    flags = [_corner_is_share(np.asarray(Image.open(f).convert("RGB"))) for f in files]
    segs, start = [], None
    for i, f in enumerate(flags + [False]):
        t = i / fps
        if f and start is None:
            start = t
        elif not f and start is not None:
            if t - start >= min_len:
                segs.append((round(start, 2), round(t, 2)))
            start = None
    for f in files:
        os.remove(f)
    os.rmdir(tmp)
    return segs


# ---------------------------------------------------------------------------
# Brand background + rounded-corner mask (Pillow)
# ---------------------------------------------------------------------------
def _rounded_mask(path, w, h):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=RADIUS, fill=255)
    m.save(path)


def _well(img, d, x, y, w, h):
    """Draw a branded rounded 'window' (glow + accent border + dark interior)."""
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).rounded_rectangle(
        [x - 14, y - 14, x + w + 14, y + h + 14], radius=RADIUS + 12,
        fill=(ACCENT[0], ACCENT[1], ACCENT[2], 90))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(30)))
    d.rounded_rectangle([x - 3, y - 3, x + w + 2, y + h + 2],
                        radius=RADIUS + 3, outline=ACCENT, width=3)
    d.rounded_rectangle([x, y, x + w - 1, y + h - 1], radius=RADIUS, fill=(6, 9, 13, 255))


def _brand_bg(path):
    img = Image.new("RGBA", (W, H), BG)
    # subtle vertical gradient (a touch lighter through the middle)
    grad = np.linspace(-8, 10, H, dtype=np.float32)
    arr = np.asarray(img).astype(np.float32)
    arr[..., :3] += grad[:, None, None]
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")
    d = ImageDraw.Draw(img)

    # top accent hairline
    d.rectangle([0, 0, W, 5], fill=ACCENT)

    # two windows: shared screen on top, speaker camera below
    _well(img, d, SCREEN_X, SCREEN_Y, SCREEN_W, SCREEN_H)
    _well(img, d, FACE_X, FACE_Y, FACE_W, FACE_H)

    # name tag (top-left)
    nf = ro.font(46, "Heavy")
    tf = ro.font(30, "Semibold")
    x, y = 60, 110
    nw = d.textlength(NAME, font=nf)
    tw = d.textlength(TITLE, font=tf)
    bw = max(nw, tw) + 120
    d.rounded_rectangle([x, y, x + bw, y + 138], radius=22, fill=(12, 16, 22, 235))
    d.rectangle([x, y + 20, x + 9, y + 118], fill=ACCENT)
    d.text((x + 36, y + 22), NAME, font=nf, fill=WHITE)
    d.text((x + 36, y + 84), TITLE, font=tf, fill=ACCENT)

    # "SCREEN SHARE" pill (top-right)
    pf = ro.font(30, "Heavy")
    label = "SCREEN SHARE"
    lw = d.textlength(label, font=pf)
    pw = lw + 96
    px1 = W - 60
    px0 = px1 - pw
    py0 = 128
    d.rounded_rectangle([px0, py0, px1, py0 + 60], radius=30,
                        fill=(ACCENT[0], ACCENT[1], ACCENT[2], 40), outline=ACCENT, width=2)
    d.ellipse([px0 + 30, py0 + 24, px0 + 42, py0 + 36], fill=ACCENT)
    d.text((px0 + 58, py0 + 15), label, font=pf, fill=WHITE)

    img.convert("RGB").save(path)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def _encode_face(video, start, dur, out):
    vf = (f"[0:v]split[bg0][fg0];"
          f"[bg0]{FACE_BG}[bg];"
          f"[fg0]crop={FACE_FG_CROP},scale=1080:-1,{FACE_ENH}[fg];"
          f"[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[o]")
    subprocess.run(["ffmpeg", "-y", "-ss", f"{start}", "-t", f"{dur}", "-i", video,
                    "-filter_complex", vf, "-map", "[o]", "-r", "30", "-t", f"{dur}",
                    "-c:v", "libx264", "-crf", CRF, "-preset", PRESET,
                    "-pix_fmt", "yuv420p", "-an", out, "-loglevel", "error"], check=True)


def _encode_share(video, start, dur, bg_png, smask_png, fmask_png, out):
    sw, sh, sx, sy = SHARE_CROP
    fw, fh, fx, fy = FACE_PIP_CROP
    # Stacked: shared screen (top) + speaker camera PIP (bottom), both rounded.
    # setpts reset is essential: the seeked video carries a large PTS while the
    # looped PNGs sit at PTS 0, so overlay would never sync them otherwise.
    # The masks are `-loop 1` (infinite) so the OUTPUT `-t {dur}` is what stops it.
    vf = (
        f"[0:v]split[sv][fv];"
        f"[sv]crop={sw}:{sh}:{sx}:{sy},scale={SCREEN_W}:{SCREEN_H},setsar=1,"
        f"setpts=PTS-STARTPTS,format=rgba[s];[2:v]format=gray[smk];[s][smk]alphamerge[sa];"
        f"[fv]crop={fw}:{fh}:{fx}:{fy},scale={FACE_W}:{FACE_H},setsar=1,"
        f"unsharp=5:5:0.6,setpts=PTS-STARTPTS,format=rgba[f];[3:v]format=gray[fmk];[f][fmk]alphamerge[fa];"
        f"[1:v][sa]overlay={SCREEN_X}:{SCREEN_Y}[t1];"
        f"[t1][fa]overlay={FACE_X}:{FACE_Y},format=yuv420p[o]")
    subprocess.run(["ffmpeg", "-y", "-ss", f"{start}", "-t", f"{dur}", "-i", video,
                    "-loop", "1", "-i", bg_png, "-loop", "1", "-i", smask_png,
                    "-loop", "1", "-i", fmask_png,
                    "-filter_complex", vf, "-map", "[o]", "-r", "30", "-t", f"{dur}",
                    "-c:v", "libx264", "-crf", CRF, "-preset", PRESET,
                    "-pix_fmt", "yuv420p", "-an", out, "-loglevel", "error"], check=True)


def render(video, out, shares=None, workdir=None):
    """Build the screen-share-aware vertical for `video` -> `out`."""
    workdir = workdir or os.path.join(os.path.dirname(os.path.abspath(out)), "_ssv_work")
    os.makedirs(workdir, exist_ok=True)
    dur_total = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video],
        capture_output=True, text=True).stdout.strip())

    if shares is None:
        shares = detect_share_segments(video)
    shares = sorted(shares)
    print("screen-share segments:", shares)

    # build a full timeline of (start, end, kind) covering [0, dur_total]
    timeline, cur = [], 0.0
    for s, e in shares:
        if s > cur:
            timeline.append((cur, s, "face"))
        timeline.append((max(s, cur), e, "share"))
        cur = e
    if cur < dur_total:
        timeline.append((cur, dur_total, "face"))

    bg_png = os.path.join(workdir, "share_bg.png")
    smask_png = os.path.join(workdir, "screen_mask.png")
    fmask_png = os.path.join(workdir, "face_mask.png")
    _brand_bg(bg_png)
    _rounded_mask(smask_png, SCREEN_W, SCREEN_H)
    _rounded_mask(fmask_png, FACE_W, FACE_H)

    parts = []
    for i, (s, e, kind) in enumerate(timeline):
        seg = os.path.join(workdir, f"seg_{i:02d}.mp4")
        dur = round(e - s, 3)
        print(f"  [{kind}] {s:.2f}-{e:.2f}s ({dur:.2f}s) -> {os.path.basename(seg)}")
        if kind == "face":
            _encode_face(video, s, dur, seg)
        else:
            _encode_share(video, s, dur, bg_png, smask_png, fmask_png, seg)
        parts.append(seg)

    # concat (video) then mux loudnorm'd original audio
    listf = os.path.join(workdir, "concat.txt")
    with open(listf, "w") as fh:
        for p in parts:
            fh.write(f"file '{p}'\n")
    concat = os.path.join(workdir, "concat.mp4")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listf,
                    "-c", "copy", concat, "-loglevel", "error"], check=True)
    subprocess.run(["ffmpeg", "-y", "-i", concat, "-i", video,
                    "-map", "0:v", "-map", "1:a", "-af", LOUDNORM,
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart", "-shortest", out, "-loglevel", "error"], check=True)
    print("screen-share-aware vertical ->", out)


if __name__ == "__main__":
    vid = sys.argv[1]
    out = sys.argv[2]
    # optional explicit shares: "start:end,start:end"
    sh = None
    if len(sys.argv) > 3:
        sh = [tuple(map(float, p.split(":"))) for p in sys.argv[3].split(",")]
    render(vid, out, shares=sh)
