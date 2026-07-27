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
# The crop 555x588 scaled to 1080 wide is 1080x1144. That is now the *probe* space:
# the subject is measured in it, and the real placement is solved from those
# measurements (see COMPOSITION / solve_placement) rather than fixed here.
#
# Placement used to be a constant, twice. First 1350x1432 pushed to the top of the
# frame, then 1080x1144 centred. Both were a single framing hard-coded for one plate
# and one crop, and both had to be re-tuned by hand when either changed. Solving it
# from the measured silhouette is what lets the composition targets in COMPOSITION
# be stated as intent — headroom, eye line, subject height — instead of as pixels.
PROBE_W, PROBE_H = 1080, 1144

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


# --- composition ---------------------------------------------------------------
# Targets for placing the subject in the frame. The scale and offset are SOLVED per
# clip from the subject's measured proportions rather than hard-coded, because the
# same numbers give a different result on any footage framed differently.
#
# A note on the three classic targets, because they cannot all hold at once and it
# is better to say so than to silently pick one. Measured on this footage (crop
# scaled to canvas width): crown->shoulder is 425px, so crown->eyes is ~180px.
#
#   eyes at 40% + headroom 5-8%  =>  crown->eyes must be ~640px  =>  scale 3.6x
#   subject height 60-70%        =>  scale 1.5-1.7x
#
# A factor of 2.2 apart. At 3.6x the head alone is 79% of frame height and the
# shoulders are cropped off both sides — a beauty close-up, not a talking head. So
# `priority` chooses which pair to satisfy exactly:
#
#   "headroom"  honour subject height + headroom; the eye line lands where anatomy
#               puts it (~22% here). Tight, webcam-like, maximum face size.
#   "eyeline"   honour subject height + eye line; headroom grows to ~25%. Reads as
#               a camera in the room looking at someone at a table, which is what
#               "indistinguishable from footage shot in the room" actually wants.
COMPOSITION = {
    "subject_height_frac": 0.65,    # of frame height; spec range 0.60-0.70
    "headroom_frac": 0.065,         # of frame height; spec range 0.05-0.08
    "eye_line_frac": 0.40,          # of frame height
    "priority": "eyeline",
    "center_x": True,
}

# Where the subject's eyes sit between crown and shoulder line. Anatomically the
# eyes are ~45% down the head; measuring to the shoulder rather than the chin adds
# the neck, so the fraction of that longer span is smaller.
EYE_FRAC_OF_HEAD = 0.42

# --- scene matching -------------------------------------------------------------
# How hard the subject is pushed toward the plate's own light. Full correction looks
# wrong — it grades his skin to the average of a brick wall. These are the fraction
# of the measured difference actually applied.
LIGHT_MATCH = {
    "exposure": 0.55,       # overall level
    "white_balance": 0.45,  # per-channel, i.e. colour temperature
    "contrast": 0.35,       # spread around the mean
    "saturation": 0.30,
}

# Ambient occlusion: the tight, dark core right where the body meets what is behind
# it. Distinct from the cast shadow — much smaller radius, much closer in.
AO_RADIUS = 14
AO_STRENGTH = 0.30

# Edge treatment. The feather is deliberately sub-pixel-ish: RVM's alpha is already
# soft, and blurring it further eats hair before it helps.
EDGE_FEATHER = 0.7              # px, spec asks for 0.5-1
EDGE_ERODE = 0.35               # px equivalent, pulls the matte just inside the halo
SPILL_STRENGTH = 0.75           # how hard edge pixels are pulled to interior colour

# Grain matched across the whole composite. The plate is a clean render and the
# footage carries sensor noise; unifying them is one of the cheapest realism wins.
# Applied to the finished frame, so it lands on subject and room identically.
COMPOSITE_GRAIN = 2.2

# Minimum brightening (0-255) across a candidate table edge. See detect_table_edge.
TABLE_EDGE_MIN_STEP = 15.0


def _load_model():
    import warnings
    warnings.filterwarnings("ignore")
    import torch
    torch.set_grad_enabled(False)
    model = torch.hub.load("PeterL1n/RobustVideoMatting", "mobilenetv3",
                           trust_repo=True).eval()
    return torch, model


def _silhouette_metrics(alpha):
    """Crown, shoulder line and lowest row of one alpha matte, or None.

    The shoulder line is where the silhouette widens fastest below the head, which
    is a far more stable landmark than anything a face detector gives on a moving
    subject — and it needs no model file.
    """
    import cv2
    width = (alpha > 0.5).sum(axis=1)
    solid = np.where(width > 12)[0]
    if len(solid) == 0:
        return None
    crown = int(solid[0])
    prof = cv2.blur(width.astype(np.float32).reshape(-1, 1), (1, 31)).ravel()
    lo = min(crown + 60, len(prof) - 2)
    hi = min(len(prof) - 1, crown + 700)
    d = np.diff(prof[lo:hi])
    shoulder = lo + int(np.argmax(d)) if len(d) else crown + 400
    return crown, shoulder, int(solid[-1])


def measure_subject(video, start, dur, probe_w=PROBE_W, probe_h=PROBE_H,
                    samples=10, infer_w=INFER_W):
    """Measure the subject's proportions, in a space normalised to `probe_w` wide.

    Returns (crown, eye, bottom) as pixel offsets in that space. Costs one short
    matting pass over sampled frames; the spec asks for realism over speed, and
    guessing the placement is precisely what makes a composite look pasted.
    """
    import cv2
    torch, model = _load_model()
    fps = max(0.5, samples / max(dur, 1.0))
    dec = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-ss", "%.3f" % start, "-t", "%.3f" % dur,
         "-i", video, "-vf", "crop=555:588:362:0,scale=%d:%d,fps=%.3f"
         % (probe_w, probe_h, fps),
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL)
    infer_h = int(round(probe_h * infer_w / probe_w / 2) * 2)
    rec, rows = [None] * 4, []
    try:
        while len(rows) < samples:
            raw = dec.stdout.read(probe_w * probe_h * 3)
            if not raw or len(raw) < probe_w * probe_h * 3:
                break
            fg = np.frombuffer(raw, np.uint8).reshape(probe_h, probe_w, 3)
            small = cv2.resize(fg, (infer_w, infer_h), interpolation=cv2.INTER_AREA)
            t = torch.from_numpy(np.ascontiguousarray(small)) \
                     .permute(2, 0, 1).float().div_(255.0)[None]
            _f, pha, *rec = model(t, *rec, downsample_ratio=DOWNSAMPLE_RATIO)
            a = cv2.resize(pha[0, 0].numpy(), (probe_w, probe_h),
                           interpolation=cv2.INTER_LINEAR)
            m = _silhouette_metrics(a)
            if m:
                rows.append(m)
    finally:
        dec.stdout.close()
        dec.wait()
    if not rows:
        raise RuntimeError("could not measure the subject: no usable matte")
    crown, shoulder, bottom = np.array(rows, dtype=np.float32).mean(axis=0)
    return crown, crown + EYE_FRAC_OF_HEAD * (shoulder - crown), bottom


def solve_placement(metrics, canvas_w, canvas_h, probe_w=PROBE_W, probe_h=PROBE_H,
                    comp=None, verbose=True):
    """Solve foreground scale and offset from measured proportions.

    Returns (fg_w, fg_h, fg_x, fg_y). See COMPOSITION for why the three classic
    targets cannot be satisfied simultaneously and how `priority` resolves it.
    """
    c = dict(COMPOSITION, **(comp or {}))
    crown, eye, bottom = metrics

    if c["priority"] == "eyeline":
        # Subject height fixes the scale; the eye line then fixes the offset.
        scale = (c["subject_height_frac"] * canvas_h) / max(bottom - crown, 1.0)
        fg_y = c["eye_line_frac"] * canvas_h - eye * scale
    else:
        scale = (c["subject_height_frac"] * canvas_h) / max(bottom - crown, 1.0)
        fg_y = c["headroom_frac"] * canvas_h - crown * scale

    fg_w = int(round(probe_w * scale / 2) * 2)      # even: yuv420p chroma
    fg_h = int(round(probe_h * scale / 2) * 2)
    fg_x = (canvas_w - fg_w) // 2 if c["center_x"] else 0
    fg_y = int(round(fg_y))

    if verbose:
        head_top = fg_y + crown * scale
        eye_y = fg_y + eye * scale
        print("  composition: scale %.2fx  headroom %.1f%%  eyes %.1f%%  height %.1f%%"
              % (scale, 100.0 * head_top / canvas_h, 100.0 * eye_y / canvas_h,
                 100.0 * (bottom - crown) * scale / canvas_h))
    return fg_w, fg_h, fg_x, fg_y


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


def _refine_alpha(alpha):
    """Tighten, clean and feather the matte.

    Three separate faults, in the order they have to be fixed:

    * A halo. RVM's edge sits a fraction outside the true silhouette, so edge pixels
      carry the ORIGINAL room's bright wall. Eroding sub-pixel pulls it back inside.
    * Speckle. Isolated partial-alpha pixels in the background read as dirt against a
      photograph, where against a dark gradient they were invisible. A small opening
      removes them without touching hair, which is connected.
    * Aliasing. A hard edge on a still background stair-steps visibly.

    Done with a remap rather than a morphological erode so it stays sub-pixel: a
    1px erode is far too much and eats hair outright.
    """
    import cv2
    a = np.clip((alpha - EDGE_ERODE * 0.5) / max(1e-6, 1.0 - EDGE_ERODE * 0.5), 0, 1)
    core = (a > 0.02).astype(np.uint8)
    core = cv2.morphologyEx(core, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    a *= np.maximum(core.astype(np.float32),
                    cv2.GaussianBlur(a, (0, 0), 2.0))     # keep hair, drop specks
    return cv2.GaussianBlur(np.clip(a, 0, 1), (0, 0), EDGE_FEATHER)


def _suppress_spill(subject, alpha):
    """Replace edge colour with interior colour, weighted by how partial the alpha is.

    Semi-transparent edge pixels are a blend of the subject and whatever was behind
    him when the camera rolled — his own bright wall. Composited onto a darker room
    that blend survives as a pale fringe, which is the single most recognisable
    green-screen artefact even when the background was never green.

    Pulling those pixels toward the colour just inside the silhouette removes the
    fringe without touching anything fully opaque.
    """
    import cv2
    k = np.ones((5, 5), np.uint8)
    inner = cv2.erode(subject, k)                      # colour from inside the edge
    edge = (alpha > 0.02) & (alpha < 0.98)
    w = (SPILL_STRENGTH * (1.0 - alpha) * edge)[:, :, None]
    return subject * (1.0 - w) + inner * w


def _scene_stats(rgb, weight):
    """Weighted per-channel mean and spread. `weight` selects which pixels count."""
    tot = float(weight.sum()) + 1e-6
    w = weight[:, :, None]
    mean = (rgb * w).sum(axis=(0, 1)) / tot
    var = ((rgb - mean) ** 2 * w).sum(axis=(0, 1)) / tot
    return mean, np.sqrt(var)


def _match_light(subject, alpha, plate_mean, plate_std, strength=None):
    """Grade the subject toward the room's own light.

    Matches four things, each only partway (see LIGHT_MATCH). Full correction is
    wrong: it grades skin toward the average colour of a brick wall. What sells the
    composite is the *direction* of the correction — if the room is warmer and
    darker than his webcam feed, he has to move warmer and darker too, or he reads
    as lit by a different sun.
    """
    cfg = dict(LIGHT_MATCH, **(strength or {}))
    mean, std = _scene_stats(subject, alpha)
    if mean.mean() < 1e-3:
        return subject

    out = subject
    # Contrast: scale the spread around the subject's own mean.
    ratio = np.clip(plate_std / np.maximum(std, 1e-3), 0.6, 1.6)
    out = mean + (out - mean) * (1.0 + (ratio - 1.0) * cfg["contrast"])
    # White balance: per-channel, relative to the luminance shift, so this moves
    # colour temperature without also moving exposure.
    chan = np.clip((plate_mean / max(plate_mean.mean(), 1e-3))
                   / np.maximum(mean / max(mean.mean(), 1e-3), 1e-3), 0.75, 1.35)
    out = out * (1.0 + (chan - 1.0) * cfg["white_balance"])
    # Exposure: overall level.
    lvl = np.clip(plate_mean.mean() / max(mean.mean(), 1e-3), 0.7, 1.4)
    out = out * (1.0 + (lvl - 1.0) * cfg["exposure"])
    # Saturation.
    if cfg["saturation"]:
        grey = out.mean(axis=2, keepdims=True)
        p_sat = float(plate_std.mean() / max(plate_mean.mean(), 1e-3))
        s_sat = float(std.mean() / max(mean.mean(), 1e-3))
        f = np.clip(p_sat / max(s_sat, 1e-3), 0.7, 1.3)
        out = grey + (out - grey) * (1.0 + (f - 1.0) * cfg["saturation"])
    return out


def detect_table_edge(plate, verbose=True, window=200):
    """Find the FAR edge of a table/desk in the plate, or None.

    The subject sits behind a table, so from that edge downward the table is in
    front of him and must occlude his body. Without it he is pasted over the table
    surface and floats in front of it — the "floating appearance" the brief calls
    out. With the edge in the wrong place he is sliced across the chest, which is
    worse than no occlusion at all, so this errs toward returning None.

    The signal is a positive LUMINANCE STEP, not edge energy. Both obvious
    alternatives were tried on a real office plate and both picked the wrong line:

      * strongest Sobel edge      -> y=1245, a counter behind him
      * largest row-colour change -> y=1727, the table's dark front lip

    Each is a genuinely strong horizontal edge; neither is the one that matters. A
    table's far edge is specifically where the frame gets BRIGHTER going down —
    chair legs and shadow above, a surface catching the room light below. Scoring
    that step directly picks y=1379 on the same plate, which is correct.

    A dark table under flat light has no such step and returns None. That is the
    right failure: no occlusion is a much smaller error than a slice.
    """
    import cv2
    h = plate.shape[0]
    lum = cv2.blur(plate.mean(axis=(1, 2)).reshape(-1, 1), (1, 41)).ravel()
    lo, hi = int(0.55 * h), int(0.92 * h)
    best, best_y = 0.0, None
    for y in range(lo, hi):
        above = lum[max(0, y - window):y]
        below = lum[y:min(h, y + window)]
        if len(above) < window // 2 or len(below) < window // 2:
            continue
        step = float(below.mean() - above.mean())
        if step > best:
            best, best_y = step, y
    if best_y is None or best < TABLE_EDGE_MIN_STEP:
        if verbose:
            print("  table edge: none found (no lit surface below the subject)")
        return None
    if verbose:
        print("  table edge: y=%d (%.0f%% down the frame, +%.0f luma)"
              % (best_y, 100.0 * best_y / h, best))
    return best_y


def table_line(plate, verbose=True, blocks=24, search=150):
    """Per-column y of the table's far edge, following its perspective. Or None.

    A single row is not good enough. A table photographed from a seated camera runs
    at an angle across the frame — on the office plate it drops ~60px from one side
    to the other — so occluding on a horizontal line cuts the subject along an edge
    the table visibly does not have. That reads as a band across his chest, which is
    what the horizontal version produced.

    The global row from detect_table_edge seeds the search; each column block then
    finds its own strongest step nearby, and a robust line is fitted through them.
    Robust matters because blocks landing on a mug, a chair or the subject's own
    body give nonsense answers, and a least-squares fit would happily follow them.
    """
    import cv2
    h, w = plate.shape[:2]
    seed = detect_table_edge(plate, verbose=verbose)
    if seed is None:
        return None

    lum = cv2.blur(plate.mean(axis=2), (41, 41))
    lo, hi = max(0, seed - search), min(h - 1, seed + search)
    xs, ys = [], []
    step = w // blocks
    for b in range(blocks):
        x0, x1 = b * step, min(w, (b + 1) * step)
        col = lum[:, x0:x1].mean(axis=1)
        band = col[lo:hi]
        if len(band) < 21:
            continue
        d = np.diff(cv2.blur(band.reshape(-1, 1), (1, 21)).ravel())
        if len(d) == 0:
            continue
        xs.append((x0 + x1) * 0.5)
        ys.append(lo + int(np.argmax(d)))
    if len(xs) < blocks // 2:
        return None

    xs, ys = np.array(xs, dtype=np.float32), np.array(ys, dtype=np.float32)
    # Robust fit: keep the blocks within one MAD of the median before fitting, so a
    # handful of columns that locked onto a mug cannot tilt the line.
    med = np.median(ys)
    keep = np.abs(ys - med) < max(25.0, 2.5 * np.median(np.abs(ys - med)))
    if keep.sum() < 4:
        keep = np.ones_like(ys, dtype=bool)
    slope, intercept = np.polyfit(xs[keep], ys[keep], 1)
    line = np.clip(slope * np.arange(w, dtype=np.float32) + intercept, 0, h - 1)
    if verbose:
        print("  table line: y %d -> %d across the frame (%d/%d blocks used)"
              % (line[0], line[-1], int(keep.sum()), len(xs)))
    return line


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
                   infer_w=INFER_W, verbose=True, comp=None, table_y=None):
    """Matte `video[start:start+dur]` onto `plate_png` and encode to `out`.

    Video only (no audio) — the caller muxes audio, exactly like the other
    segment encoders in screenshare_vertical.

    Order of operations matters and is not arbitrary:

        measure -> matte -> refine edge -> despill -> match light to the room
        -> cast shadow -> ambient occlusion -> composite -> table occludes body
        -> global grade -> matched grain

    The grade is deliberately LAST and applied to the whole frame. Grading the
    subject in the decode chain (where it used to live) gives the foreground its own
    colour response, so subject and room react differently to the same correction —
    which is exactly the tell that says "two images". Compositing first and grading
    once means both halves go through identical maths.
    """
    import cv2
    from PIL import Image

    torch, model = _load_model()
    plate = np.asarray(Image.open(plate_png).convert("RGB")).astype(np.float32)
    if plate.shape[:2] != (p["h"], p["w"]):
        plate = cv2.resize(plate, (p["w"], p["h"]), interpolation=cv2.INTER_LANCZOS4)

    # Composition is solved from the actual subject, not assumed.
    metrics = measure_subject(video, start, dur, infer_w=infer_w)
    fg_w, fg_h, fg_x, fg_y = solve_placement(metrics, p["w"], p["h"], comp=comp,
                                             verbose=verbose)

    # The table layer is the plate from its far edge down, feathered so the occlusion
    # boundary is not a razor line, and following the table's real perspective rather
    # than cutting straight across.
    table_mask = None
    line = np.full(p["w"], float(table_y)) if table_y is not None \
        else table_line(plate, verbose=verbose)
    if line is not None:
        rows = np.arange(p["h"], dtype=np.float32)[:, None]
        table_mask = np.clip(rows - line[None, :] + 6.0, 0.0, 12.0) / 12.0
        table_mask = cv2.GaussianBlur(table_mask, (0, 0), 4.0)[:, :, None]

    infer_h = int(round(fg_h * infer_w / fg_w / 2) * 2)
    rim_rgb = np.array(RIM_RGB, dtype=np.float32)

    fade = np.ones((fg_h, 1), dtype=np.float32)
    if BOTTOM_FADE_PX > 0:
        ramp = np.linspace(1.0, 0.0, BOTTOM_FADE_PX, dtype=np.float32)
        fade[fg_h - BOTTOM_FADE_PX:, 0] = ramp ** 1.4      # ease out, not linear

    # Overlap rectangle between the (possibly oversized / negatively offset)
    # foreground and the canvas. Computed once; the foreground never moves.
    cx0, cy0 = max(0, fg_x), max(0, fg_y)
    cx1, cy1 = min(p["w"], fg_x + fg_w), min(p["h"], fg_y + fg_h)
    fx0, fy0 = cx0 - fg_x, cy0 - fg_y
    fx1, fy1 = fx0 + (cx1 - cx0), fy0 + (cy1 - cy0)
    if cx1 <= cx0 or cy1 <= cy0:
        raise ValueError("foreground placement leaves nothing on canvas")

    # Same overlap maths again for the offset shadow, which lands on a different
    # rectangle and can clip differently at the canvas edges.
    sdx, sdy = SHADOW_OFFSET
    sx0, sy0 = max(0, fg_x + sdx), max(0, fg_y + sdy)
    sx1, sy1 = min(p["w"], fg_x + sdx + fg_w), min(p["h"], fg_y + sdy + fg_h)
    sfx0, sfy0 = sx0 - (fg_x + sdx), sy0 - (fg_y + sdy)
    sfx1, sfy1 = sfx0 + (sx1 - sx0), sfy0 + (sy1 - sy0)
    quarter = (p["w"] // 4, p["h"] // 4)

    # Light target: the room immediately AROUND where the subject will sit, not the
    # whole plate. Matching him to the average of the entire frame would drag him
    # toward the bright window he is nowhere near.
    near = np.zeros((p["h"], p["w"]), dtype=np.float32)
    near[max(0, cy0 - 120):min(p["h"], cy1 + 120),
         max(0, cx0 - 160):min(p["w"], cx1 + 160)] = 1.0
    plate_mean, plate_std = _scene_stats(plate, near)

    # Only the sharpen stays in the decode chain — it is detail recovery, not
    # colour, and running it on the composite would sharpen the defocused room.
    vf = "crop=555:588:362:0,scale=%d:%d,unsharp=5:5:0.4" % (fg_w, fg_h)
    dec = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-ss", "%.3f" % start, "-t", "%.3f" % dur,
         "-i", video, "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # The measured grade now runs here, on the finished composite.
    enc_vf = ["-vf", grade_vf] if grade_vf else []
    enc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", "%dx%d" % (p["w"], p["h"]), "-r", str(p["fps"]), "-i", "-",
         "-an"] + enc_vf + encode.video_args(p) + [out],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    frame_bytes = fg_w * fg_h * 3
    rec = [None] * 4
    rng = np.random.default_rng(4)
    n = 0
    try:
        while True:
            raw = dec.stdout.read(frame_bytes)
            if not raw or len(raw) < frame_bytes:
                break
            fg = np.frombuffer(raw, np.uint8).reshape(fg_h, fg_w, 3)

            small = cv2.resize(fg, (infer_w, infer_h), interpolation=cv2.INTER_AREA)
            tensor = torch.from_numpy(np.ascontiguousarray(small)) \
                          .permute(2, 0, 1).float().div_(255.0)[None]
            _fgr, pha, *rec = model(tensor, *rec, downsample_ratio=DOWNSAMPLE_RATIO)
            alpha = cv2.resize(pha[0, 0].numpy(), (fg_w, fg_h),
                               interpolation=cv2.INTER_LINEAR)
            alpha = _refine_alpha(alpha) * fade

            subject = _suppress_spill(fg.astype(np.float32), alpha)
            subject = _match_light(subject, alpha, plate_mean, plate_std)
            subject *= SUBJECT_GAIN
            rim = _edge_band(alpha)[:, :, None] * (RIM_STRENGTH * rim_rgb)

            # Cast shadow: offset and very soft, the room light blocked by his body.
            shad = np.zeros((p["h"], p["w"]), dtype=np.float32)
            shad[sy0:sy1, sx0:sx1] = alpha[sfy0:sfy1, sfx0:sfx1]
            shad = _soften(shad, quarter)

            # Ambient occlusion: a much tighter, darker core hugging the silhouette,
            # where almost no bounced light reaches. This is what stops him reading
            # as a decal on the wall; the cast shadow alone is too diffuse to ground
            # anything.
            ao = np.zeros((p["h"], p["w"]), dtype=np.float32)
            ao[cy0:cy1, cx0:cx1] = alpha[fy0:fy1, fx0:fx1]
            ao = cv2.GaussianBlur(ao, (0, 0), AO_RADIUS)

            # The room WITH his shadows on it. Kept as its own layer because the
            # table needs it too: the table occludes his body but must still catch
            # the shadow he casts onto it, and restoring the raw plate on top would
            # wipe that out and leave the one surface he is touching unlit by him.
            shadowed = plate * (1.0 - SHADOW_STRENGTH * shad[:, :, None]) \
                             * (1.0 - AO_STRENGTH * ao)[:, :, None]
            out_frame = shadowed.copy()

            region = out_frame[cy0:cy1, cx0:cx1, :]
            a = alpha[fy0:fy1, fx0:fx1, None]
            region[:] = (subject[fy0:fy1, fx0:fx1] * a
                         + region * (1.0 - a)
                         + rim[fy0:fy1, fx0:fx1])

            # The table is in front of him, so it goes back over the composite —
            # shadows and all.
            if table_mask is not None:
                out_frame = out_frame * (1.0 - table_mask) + shadowed * table_mask

            # Grain last, on the whole frame, so room and subject carry the same
            # noise floor. A clean composite over a clean plate is itself a tell.
            if COMPOSITE_GRAIN:
                out_frame += rng.normal(0.0, COMPOSITE_GRAIN, (p["h"], p["w"], 1))
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
