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
  2. render()                 -> encode each segment with the right framing
     (segments run in parallel), concatenate, then mux the original audio.

Text/branding is baked with Pillow (this ffmpeg has no drawtext/libass).

Two things moved out of this module:

* **Audio is no longer normalised here.** It is copied through untouched and
  mastered once at the end (`pipeline/audio_master.py`), so the reel carries a
  single AAC generation instead of three. A body built here is therefore *not*
  loudness-normalised on its own — that is deliberate, and the reel builder
  handles it.
* **Colour correction is measured, not hardcoded** (`pipeline/grade.py`). The
  measured correction is applied to camera footage only; the shared screen is
  never graded, because nudging contrast on someone's code or slides makes it
  harder to read, not easier.
"""
import os
import sys
import subprocess

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backdrop  # noqa: E402  (designed studio plates)
import matte  # noqa: E402     (RVM background replacement)
import encode  # noqa: E402  (canonical profiles + ffmpeg plumbing)
import grade  # noqa: E402   (measured auto colour correction)
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
#
# Both wells sit HIGHER than they originally did. Captions moved up to clear the
# platform UI band (overlays.CAPTION_CENTER_Y = 1330, so a two-line bar starts at
# y=1212); at the old FACE_Y=940 the camera well ran to 1321 and the caption would
# have covered its bottom third. The vertical budget is therefore:
#
#     name tag ends 248 | gap | screen (~405) | gap | camera (~381) | caption 1212
#
# with the ~188px of slack split into three gaps. Do not lower these without
# also lowering the caption, and vice versa — the constants are coupled and
# tests/test_pipeline_quality.py asserts they do not overlap.
SCREEN_W = 1010
SCREEN_H = round(SHARE_CROP[1] * SCREEN_W / SHARE_CROP[0])   # keep aspect (~405)
SCREEN_X = (W - SCREEN_W) // 2
SCREEN_Y = 308                       # 60px below the name tag

FACE_W = 560
FACE_H = round(FACE_PIP_CROP[1] * FACE_W / FACE_PIP_CROP[0])  # keep aspect (~381)
FACE_X = (W - FACE_W) // 2
FACE_Y = 781                         # ends ~1162, clear of the caption bar at 1212
RADIUS = 18

# Face-only framing (blurred-fit, from config/settings.yaml -> social.framing)
FACE_FG_CROP = "555:588:362:0"
FACE_BG = "scale=-1:1920,crop=1080:1920,boxblur=26:2,eq=brightness=-0.16:saturation=1.1"
# Cheap stand-in for the shipping blur, used by the draft/preview rungs only.
# Blurring a 135x240 thumbnail and scaling it back up costs a fraction of a
# full-resolution boxblur, and the background is out of focus either way — but it
# is NOT pixel-identical, so `final` never uses it.
FACE_BG_CHEAP = ("scale=-1:240,crop=135:240,boxblur=4:1,scale=1080:1920,"
                 "eq=brightness=-0.16:saturation=1.1")
FACE_SHARPEN = "unsharp=5:5:0.4"    # the colour half of the old FACE_ENH is now measured

# Colour is measured through the shipping crop, so the analysis sees the same
# pixels the viewer will (not the StreamYard banner we crop away).
GRADE_PRE_FILTER = "crop=" + FACE_FG_CROP
GRADE_SAMPLE_SECONDS = 20.0         # enough to characterise the lighting, cheap to read

# Framing styles for non-share (camera) segments.
#
#   blur          the original: bands filled with a blurred copy of the room.
#   studio_bands  bands replaced by a designed studio plate; footage stays FULL
#                 width, so the speaker's face keeps its current size.
#   studio_set    footage inset in a rounded window on the plate, so more of the
#                 set shows. More designed, smaller face.
#
# The studio styles composite over a static PNG (pipeline/backdrop.py) instead of
# running a full-resolution boxblur, so they are also cheaper than the default.
STYLES = ("blur", "studio_bands", "studio_set", "studio_real")

# studio_real replaces the background for real (RVM matting, pipeline/matte.py).
# It is inherently sequential — RVM carries recurrent state between frames — and
# heavy, so it runs one segment at a time regardless of `workers`.
MATTE_STYLE = "studio_real"
MATTE_PLATE_STYLE = "studio_real"    # its own plate: no panels, more depth cues

# studio_set window geometry. 900 wide keeps the window's bottom at ~1194, clear of
# the caption bar at 1212 (see overlays.CAPTION_CENTER_Y).
#
# BOTH dimensions are explicit, and that is deliberate. FACE_FG_CROP is 555 wide —
# an ODD number — and yuv420p needs even chroma dimensions, so ffmpeg silently
# crops 554 instead. A Python-side height derived from 555 therefore disagrees with
# what ffmpeg actually produces from `scale=900:-1`, and `alphamerge` then fails
# with "input frame sizes do not match" (900x955 vs 900x954). Pinning the height
# makes the mask and the scaled footage agree by construction; the 0.13% aspect
# change against the true 554:588 is invisible.
SET_WIN_W = 900
SET_WIN_H = 954
SET_WIN_Y = 240
SET_RADIUS = 26

# Lens character for the studio styles only, never for `blur` (which must keep the
# shipped look byte-for-byte). Real lenses vignette, so the composite gets one.
#
# Grain is NOT applied here as an ffmpeg filter. `noise` re-randomises every frame,
# which destroys inter-frame compression: a 26s sample came out at 18.7 Mbps / 72 MB
# instead of ~2 Mbps. Static grain is baked into the backdrop plate instead (free —
# it does not change between frames) and the footage already carries its own sensor
# grain, so the composite still reads as shot rather than rendered.
STUDIO_VIGNETTE = "vignette"

# How many segment encodes run at once. x264 already threads internally, so this
# buys per-process ramp-up on multi-share clips rather than linear scaling.
WORKERS = 3

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
def _rounded_mask(path, w, h, radius=None):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1],
                                       radius=RADIUS if radius is None else radius,
                                       fill=255)
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
def _out_scale(p):
    """Trailing scale filter when the profile canvas differs from the layout canvas.

    Every Pillow layer and every layout constant in this module is authored at
    1080x1920, so a profile on a different canvas composes at the layout size and
    rescales as the very last step. No current vertical rung needs this (the
    quality ladder keeps one canvas on purpose) — it is the hook a horizontal or
    square profile renders through. See RUNBOOK "Horizontal output (planned)".
    """
    return "" if (p["w"], p["h"]) == (W, H) else ",scale=%d:%d" % (p["w"], p["h"])


def _encode_face(video, start, dur, out, p, grade_vf):
    """Blurred-fit framing: sharp centre crop over a blurred, darkened fill."""
    enhance = ",".join(f for f in (grade_vf, FACE_SHARPEN) if f)
    background = FACE_BG_CHEAP if p["cheap_filters"] else FACE_BG
    vf = (f"[0:v]split[bg0][fg0];"
          f"[bg0]{background}[bg];"
          f"[fg0]crop={FACE_FG_CROP},scale={W}:-1,{enhance}[fg];"
          f"[bg][fg]overlay=(W-w)/2:(H-h)/2{_out_scale(p)},format=yuv420p[o]")
    encode.run(["ffmpeg", "-y", "-ss", f"{start}", "-t", f"{dur}", "-i", video,
                "-filter_complex", vf, "-map", "[o]", "-t", f"{dur}", "-an"]
               + encode.video_args(p) + ["-loglevel", "error", out])
    return out


def _encode_studio(video, start, dur, out, p, grade_vf, plate_png, inset,
                   mask_png=None):
    """Composite the camera footage onto a static studio plate.

    Args:
        plate_png: backdrop plate from pipeline/backdrop.py.
        inset: False -> footage at full width, centred (studio_bands).
               True  -> footage in a rounded window (studio_set), which needs
               `mask_png` for the corners.

    `setpts=PTS-STARTPTS` is essential: the seeked video carries a large PTS while
    the looped plate sits at PTS 0, so overlay would never sync them. The plate is
    `-loop 1` (infinite), so the OUTPUT `-t` is what stops the encode.
    """
    enhance = ",".join(f for f in (grade_vf, FACE_SHARPEN) if f)
    lens = STUDIO_VIGNETTE
    inputs = ["-loop", "1", "-i", plate_png]

    if not inset:
        vf = (f"[0:v]crop={FACE_FG_CROP},scale={W}:-1,{enhance},"
              f"setpts=PTS-STARTPTS[fg];"
              f"[1:v][fg]overlay=(W-w)/2:(H-h)/2{_out_scale(p)},"
              f"{lens},format=yuv420p[o]")
    else:
        inputs += ["-loop", "1", "-i", mask_png]
        x = (W - SET_WIN_W) // 2
        vf = (f"[0:v]crop={FACE_FG_CROP},scale={SET_WIN_W}:{SET_WIN_H},{enhance},setsar=1,"
              f"setpts=PTS-STARTPTS,format=rgba[fg];"
              f"[2:v]format=gray[mk];[fg][mk]alphamerge[fa];"
              f"[1:v][fa]overlay={x}:{SET_WIN_Y}{_out_scale(p)},"
              f"{lens},format=yuv420p[o]")

    encode.run(["ffmpeg", "-y", "-ss", f"{start}", "-t", f"{dur}", "-i", video]
               + inputs
               + ["-filter_complex", vf, "-map", "[o]", "-t", f"{dur}", "-an"]
               + encode.video_args(p) + ["-loglevel", "error", out])
    return out


def _encode_share(video, start, dur, bg_png, smask_png, fmask_png, out, p, grade_vf):
    """Stacked framing: whole shared screen on top, camera PIP below.

    The measured grade is applied to the camera PIP only — the shared screen is
    left exactly as captured so code and slides stay legible.
    """
    sw, sh, sx, sy = SHARE_CROP
    fw, fh, fx, fy = FACE_PIP_CROP
    pip_enhance = ",".join(f for f in (grade_vf, "unsharp=5:5:0.6") if f)
    # setpts reset is essential: the seeked video carries a large PTS while the
    # looped PNGs sit at PTS 0, so overlay would never sync them otherwise.
    # The masks are `-loop 1` (infinite) so the OUTPUT `-t {dur}` is what stops it.
    vf = (
        f"[0:v]split[sv][fv];"
        f"[sv]crop={sw}:{sh}:{sx}:{sy},scale={SCREEN_W}:{SCREEN_H},setsar=1,"
        f"setpts=PTS-STARTPTS,format=rgba[s];[2:v]format=gray[smk];[s][smk]alphamerge[sa];"
        f"[fv]crop={fw}:{fh}:{fx}:{fy},scale={FACE_W}:{FACE_H},setsar=1,"
        f"{pip_enhance},setpts=PTS-STARTPTS,format=rgba[f];[3:v]format=gray[fmk];[f][fmk]alphamerge[fa];"
        f"[1:v][sa]overlay={SCREEN_X}:{SCREEN_Y}[t1];"
        f"[t1][fa]overlay={FACE_X}:{FACE_Y}{_out_scale(p)},format=yuv420p[o]")
    encode.run(["ffmpeg", "-y", "-ss", f"{start}", "-t", f"{dur}", "-i", video,
                "-loop", "1", "-i", bg_png, "-loop", "1", "-i", smask_png,
                "-loop", "1", "-i", fmask_png,
                "-filter_complex", vf, "-map", "[o]", "-t", f"{dur}", "-an"]
               + encode.video_args(p) + ["-loglevel", "error", out])
    return out


def _build_timeline(shares, dur_total):
    """Cover [0, dur_total] with contiguous (start, end, kind) spans."""
    timeline, cur = [], 0.0
    for s, e in sorted(shares):
        if s > cur:
            timeline.append((cur, s, "face"))
        timeline.append((max(s, cur), e, "share"))
        cur = e
    if cur < dur_total:
        timeline.append((cur, dur_total, "face"))
    return timeline


def _measure_grade(video, timeline, workdir, enabled):
    """Measure the colour correction once, on camera footage, for the whole body.

    Measuring per segment would make the grade drift visibly across cuts, so we
    characterise the recording once and apply the same correction everywhere.
    Prefers a face span (full-frame camera); falls back to the camera PIP crop
    when the clip is share-only.
    """
    if not enabled:
        return ""
    face = next((sp for sp in timeline if sp[2] == "face"), None)
    if face is not None:
        start, end = face[0], face[1]
        pre = GRADE_PRE_FILTER
    else:
        start, end = timeline[0][0], timeline[0][1]
        fw, fh, fx, fy = FACE_PIP_CROP
        pre = "crop=%d:%d:%d:%d" % (fw, fh, fx, fy)
    window = min(GRADE_SAMPLE_SECONDS, max(1.0, end - start))
    return grade.auto_filter(video, start=start, dur=window, pre_filter=pre,
                             cache_dir=workdir)


def render(video, out, shares=None, workdir=None, quality="final",
           orientation="vertical", auto_grade=True, workers=WORKERS,
           style="blur", backdrop_photo=None):
    """Build the screen-share-aware vertical for `video` -> `out`.

    Args:
        video: source clip (already trimmed to one answer).
        out: destination body mp4.
        shares: explicit [(start, end), ...] share ranges, or None to auto-detect.
        quality: "final" | "preview" | "draft" (see pipeline/encode.py).
        orientation: delivery canvas; only "vertical" is wired up for now.
        auto_grade: measure and apply the bounded colour correction.
        backdrop_photo: path to a real room photo to use as the backdrop plate
            instead of the rendered one. Only meaningful for the studio styles.
        workers: parallel segment encodes.
        style: camera-segment framing — see STYLES. Screen-share segments always
            use the stacked layout regardless, because the shared screen has to
            stay legible and a decorative plate would only steal room from it.

    The audio is muxed through **unprocessed** — mastering happens once, later.
    """
    if style not in STYLES:
        raise KeyError("unknown style %r (have: %s)" % (style, ", ".join(STYLES)))
    workdir = workdir or os.path.join(os.path.dirname(os.path.abspath(out)), "_ssv_work")
    os.makedirs(workdir, exist_ok=True)
    p = encode.profile(quality, orientation)
    dur_total = encode.duration(video)

    if shares is None:
        shares = detect_share_segments(video)
    print("screen-share segments:", sorted(shares))

    timeline = _build_timeline(shares, dur_total)
    grade_vf = _measure_grade(video, timeline, workdir, auto_grade)

    bg_png = os.path.join(workdir, "share_bg.png")
    smask_png = os.path.join(workdir, "screen_mask.png")
    fmask_png = os.path.join(workdir, "face_mask.png")
    _brand_bg(bg_png)
    _rounded_mask(smask_png, SCREEN_W, SCREEN_H)
    _rounded_mask(fmask_png, FACE_W, FACE_H)

    # Studio styles need their backdrop plate (and, when inset, a corner mask).
    plate_png = set_mask = None
    if style != "blur":
        plate_style = MATTE_PLATE_STYLE if style == MATTE_STYLE else style
        # A supplied photo makes the plate a real room instead of a rendered one.
        # It is part of the plate filename so switching photos rebuilds the cache
        # rather than silently reusing the previous look.
        tag = plate_style
        if backdrop_photo:
            tag += "_" + os.path.splitext(os.path.basename(backdrop_photo))[0]
        plate_png = backdrop.plate(plate_style,
                                   os.path.join(workdir, f"plate_{tag}.png"),
                                   photo=backdrop_photo)
        if style == "studio_set":
            set_mask = os.path.join(workdir, "set_mask.png")
            _rounded_mask(set_mask, SET_WIN_W, SET_WIN_H, radius=SET_RADIUS)

    # Queue every segment, then encode them concurrently.
    jobs, parts = [], []
    for i, (s, e, kind) in enumerate(timeline):
        seg = os.path.join(workdir, f"seg_{i:02d}.mp4")
        dur = round(e - s, 3)
        print(f"  [{kind}] {s:.2f}-{e:.2f}s ({dur:.2f}s) -> {os.path.basename(seg)}")
        if kind == "face":
            if style == "blur":
                jobs.append((video, s, dur, seg, p, grade_vf))
            elif style == MATTE_STYLE:
                jobs.append((video, s, dur, seg, p, grade_vf, plate_png))
            else:
                jobs.append((video, s, dur, seg, p, grade_vf, plate_png,
                             style == "studio_set", set_mask))
        else:
            jobs.append((video, s, dur, bg_png, smask_png, fmask_png, seg, p, grade_vf))
        parts.append(seg)

    face_jobs = [j for j, sp in zip(jobs, timeline) if sp[2] == "face"]
    share_jobs = [j for j, sp in zip(jobs, timeline) if sp[2] == "share"]
    print(f"  encoding {len(parts)} segment(s), {min(workers, len(parts))} at a time"
          f" [{p['quality']} {p['w']}x{p['h']} crf{p['crf']} style={style}]")
    if style == "blur":
        face_fn, face_workers = _encode_face, workers
    elif style == MATTE_STYLE:
        face_fn, face_workers = matte.render_segment, 1
    else:
        face_fn, face_workers = _encode_studio, workers
    encode.parallel(face_fn, face_jobs, workers=face_workers, label="face segment")
    encode.parallel(_encode_share, share_jobs, workers=workers, label="share segment")

    # Concat the silent video parts, then mux the source audio back in.
    listf = os.path.join(workdir, "concat.txt")
    with open(listf, "w") as fh:
        for part in parts:
            fh.write("file '%s'\n" % os.path.abspath(part))
    concat = os.path.join(workdir, "concat.mp4")
    encode.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listf,
                "-c", "copy", "-loglevel", "error", concat])

    # Copy the audio when the source is already canonical (StreamYard is), so it
    # reaches the final mastering pass without a single intermediate re-encode.
    passthrough = encode.audio_passthrough_ok(video)
    audio = ["-c:a", "copy"] if passthrough else encode.audio_args()
    print("  audio: %s" % ("copied from source (0 generations)" if passthrough
                           else "re-encoded to canonical AAC (source differs)"))
    encode.run(["ffmpeg", "-y", "-i", concat, "-i", video,
                "-map", "0:v", "-map", "1:a", "-c:v", "copy"] + audio
               + ["-movflags", "+faststart", "-shortest", "-loglevel", "error", out])
    print("screen-share-aware vertical ->", out)
    return out


if __name__ == "__main__":
    vid = sys.argv[1]
    out = sys.argv[2]
    # optional explicit shares: "start:end,start:end"
    sh = None
    if len(sys.argv) > 3:
        sh = [tuple(map(float, p.split(":"))) for p in sys.argv[3].split(",")]
    render(vid, out, shares=sh)
