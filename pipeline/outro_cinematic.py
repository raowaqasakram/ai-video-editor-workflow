"""Cinematic brand outro (storyboard spec): 1080x1920, 2.5s, 60fps, H.264.

Three timed scenes, professionally eased and glow-lit (no flat cuts):
  SCENE 1  0.00-0.70s  terminal boot text + light-streak / particle warp
  SCENE 2  0.70-1.80s  glowing gradient ring -> RWA logo + name reveal
  SCENE 3  1.80-2.50s  "FOLLOW FOR MORE" + social icons pop-in

Frame-based with Pillow because this ffmpeg build has no drawtext/libass.
Glow is real (Gaussian-blurred copies composited under the sharp art); the
particle field and star-warp are numpy-driven for smooth sub-pixel motion.

Palette (storyboard):  bg #080808  blue #4F8DFF  purple #8B5CF6  white #FFFFFF
"""
import os
import sys
import math
import subprocess

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
W, H = 1080, 1920
FPS, DUR = 60, 2.5
CX, CY = W // 2, 700          # optical centre for the ring / warp

BG = (8, 8, 8)
BLUE = (79, 141, 255)         # #4F8DFF
PURPLE = (139, 92, 246)       # #8B5CF6
WHITE = (255, 255, 255)
GREEN = (86, 224, 128)        # terminal prompt
GRAY = (150, 160, 175)

SF = "/System/Library/Fonts/SFNS.ttf"
MONO = "/System/Library/Fonts/Menlo.ttc"
ARIAL_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

HERE = os.path.dirname(os.path.abspath(__file__))
FRAMES = os.environ.get("OUTRO_FRAMES",
                        "/private/tmp/claude-502/-Users-elon-Youtube-Automation-weekly-live/"
                        "d0c7d583-5313-48b8-9ae9-7e0bfa01dc38/scratchpad/outro_frames")
OUT = os.path.join(HERE, "outro_cinematic.mp4")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def smooth(a, b, x):
    """Smoothstep ramp: 0 below a, 1 above b, eased in between."""
    t = clamp((x - a) / (b - a) if b != a else 1.0)
    return t * t * (3 - 2 * t)


def ease_out_cubic(p):
    return 1 - (1 - p) ** 3


def ease_in_cubic(p):
    return p ** 3


def ease_out_expo(p):
    return 1.0 if p >= 1 else 1 - 2 ** (-10 * p)


def ease_out_back(p, s=1.9):
    c3 = s + 1
    return 1 + c3 * (p - 1) ** 3 + s * (p - 1) ** 2


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(len(a)))


def rgba(c, a=255):
    return (c[0], c[1], c[2], int(a))


_FONT_CACHE = {}


def font(size, weight="Bold", mono=False):
    key = (size, weight, mono)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    try:
        f = ImageFont.truetype(MONO if mono else SF, size)
        if not mono:
            try:
                f.set_variation_by_name(weight)
            except Exception:
                pass
    except Exception:
        f = ImageFont.truetype(ARIAL_BOLD, size)
    _FONT_CACHE[key] = f
    return f


def new_layer():
    return Image.new("RGBA", (W, H), (0, 0, 0, 0))


def glow(layer, radius, gain=1.0):
    """Return a blurred copy of a layer to sit under the sharp art."""
    b = layer.filter(ImageFilter.GaussianBlur(radius))
    if gain != 1.0:
        a = b.split()[3].point(lambda p: min(255, int(p * gain)))
        b.putalpha(a)
    return b


def draw_text_spaced(d, xy, text, fnt, fill, tracking=0, anchor_center=None):
    """Draw text with per-character letter-spacing (tracking in px)."""
    x, y = xy
    if anchor_center is not None:
        total = sum(d.textlength(ch, font=fnt) + tracking for ch in text) - tracking
        x = anchor_center - total / 2
    for ch in text:
        d.text((x, y), ch, font=fnt, fill=fill)
        x += d.textlength(ch, font=fnt) + tracking
    return x


def spaced_width(d, text, fnt, tracking=0):
    return sum(d.textlength(ch, font=fnt) + tracking for ch in text) - tracking


def scale_alpha(layer, alpha):
    if alpha >= 1.0:
        return layer
    a = layer.split()[3].point(lambda p: int(p * alpha))
    layer.putalpha(a)
    return layer


# ---------------------------------------------------------------------------
# Precomputed particle systems (deterministic)
# ---------------------------------------------------------------------------
_rng = np.random.default_rng(7)

# Ambient drifting star/particle field (present through the whole outro)
N_STARS = 130
_star_x = _rng.uniform(0, W, N_STARS)
_star_y = _rng.uniform(0, H, N_STARS)
_star_r = _rng.uniform(1.0, 2.8, N_STARS)
_star_ph = _rng.uniform(0, math.tau, N_STARS)
_star_sp = _rng.uniform(1.5, 4.0, N_STARS)
_star_dy = _rng.uniform(6, 26, N_STARS)          # upward drift px/sec
_star_col = [BLUE, PURPLE, WHITE, BLUE, PURPLE][:1] * 0
_star_pick = _rng.integers(0, 3, N_STARS)
_PALETTE3 = [BLUE, PURPLE, WHITE]

# Scene-1 star-warp: particles streaking outward from the optical centre
N_WARP = 90
_w_ang = _rng.uniform(0, math.tau, N_WARP)
_w_spd = _rng.uniform(650, 1600, N_WARP)
_w_r0 = _rng.uniform(10, 90, N_WARP)
_w_pick = _rng.integers(0, 3, N_WARP)
_w_len = _rng.uniform(60, 200, N_WARP)


def draw_ambient(base, t, dim=1.0):
    d = ImageDraw.Draw(base)
    for i in range(N_STARS):
        tw = 0.45 + 0.55 * math.sin(_star_ph[i] + t * _star_sp[i])
        a = int(150 * tw * dim)
        if a <= 3:
            continue
        y = (_star_y[i] - _star_dy[i] * t) % H
        r = _star_r[i]
        c = _PALETTE3[_star_pick[i]]
        d.ellipse([_star_x[i] - r, y - r, _star_x[i] + r, y + r], fill=rgba(c, a))


def draw_warp(base, t, p1):
    """Scene-1 outward light streaks + a central flare that resolves to the ring."""
    lyr = new_layer()
    d = ImageDraw.Draw(lyr)
    ep = ease_in_cubic(p1)                     # accelerate outward
    fade = smooth(0.0, 0.12, p1) * (1 - smooth(0.55, 0.9, p1))
    for i in range(N_WARP):
        rad = _w_r0[i] + _w_spd[i] * ep
        c = math.cos(_w_ang[i]); s = math.sin(_w_ang[i])
        hx, hy = CX + c * rad, CY + s * rad
        tx, ty = CX + c * (rad - _w_len[i]), CY + s * (rad - _w_len[i])
        col = _PALETTE3[_w_pick[i]]
        a = int(210 * fade)
        if a <= 4:
            continue
        d.line([(tx, ty), (hx, hy)], fill=rgba(col, a), width=2)
        d.ellipse([hx - 2.2, hy - 2.2, hx + 2.2, hy + 2.2], fill=rgba(WHITE, a))
    # central flare: grows, then collapses as the ring takes over
    flare = new_layer()
    fd = ImageDraw.Draw(flare)
    fr = 30 + 150 * ease_out_cubic(clamp(p1 / 0.7))
    fa = int(230 * (smooth(0.0, 0.25, p1) * (1 - smooth(0.5, 0.85, p1))))
    fd.ellipse([CX - fr, CY - fr, CX + fr, CY + fr], fill=rgba(lerp(WHITE, BLUE, 0.3), fa))
    flare = glow(flare, 55, 1.2)
    base.alpha_composite(glow(lyr, 6, 1.1))
    base.alpha_composite(flare)
    base.alpha_composite(lyr)


# ---------------------------------------------------------------------------
# SCENE 1 — terminal boot text
# ---------------------------------------------------------------------------
_BOOT = ["Initializing...", "Loading assets", "Building experience", "Please wait..."]
_BOOT_START = [0.04, 0.17, 0.30, 0.43]
_TYPE_DUR = 0.11


def scene1_terminal(base, t, alpha):
    lyr = new_layer()
    d = ImageDraw.Draw(lyr)
    mf = font(34, mono=True)
    x0, y0, lh = 70, 300, 58
    for i, line in enumerate(_BOOT):
        st = _BOOT_START[i]
        if t < st:
            break
        p = clamp((t - st) / _TYPE_DUR)
        n = max(1, int(round(p * len(line))))
        y = y0 + i * lh
        d.text((x0, y), ">", font=mf, fill=rgba(GREEN, 255))
        shown = line[:n]
        d.text((x0 + 40, y), shown, font=mf, fill=rgba((225, 232, 240), 255))
        # blinking cursor block on the line currently typing / last line
        typing = p < 1.0
        last = i == len(_BOOT) - 1
        if typing or last:
            blink = (math.sin(t * 18) > 0)
            if typing or blink:
                cw = d.textlength(shown, font=mf)
                cx = x0 + 40 + cw + 4
                d.rectangle([cx, y + 6, cx + 16, y + 40], fill=rgba(GREEN, 230))
    scale_alpha(lyr, alpha)
    base.alpha_composite(lyr)


# ---------------------------------------------------------------------------
# SCENE 2 — gradient ring + RWA logo + name
# ---------------------------------------------------------------------------
def ring_color(frac):
    """Blue -> purple -> blue around the ring for a symmetric neon gradient."""
    m = 0.5 - 0.5 * math.cos(frac * math.tau)
    return lerp(BLUE, PURPLE, m)


def draw_ring(cx, cy, r, width, reveal):
    lyr = new_layer()
    d = ImageDraw.Draw(lyr)
    nseg = 160
    end = reveal * 360
    box = [cx - r, cy - r, cx + r, cy + r]
    for i in range(nseg):
        f0 = i / nseg
        a0 = -90 + f0 * 360
        a1 = -90 + (i + 1) / nseg * 360
        if a0 + 90 > end:
            break
        col = ring_color(f0)
        d.arc(box, a0 - 0.6, a1 + 0.6, fill=rgba(col, 255), width=width)
    return lyr


def scene2_logo(base, t, alpha):
    # ring geometry
    r = 210
    reveal = ease_out_expo(clamp((t - 0.72) / 0.55))
    scl = 0.82 + 0.18 * ease_out_back(clamp((t - 0.72) / 0.6))
    rr = int(r * clamp(scl, 0.5, 1.15))
    ring = draw_ring(CX, CY, rr, 12, reveal)
    base.alpha_composite(scale_alpha(glow(ring, 26, 1.3), alpha * 0.9))
    base.alpha_composite(scale_alpha(glow(ring, 10, 1.1), alpha))
    base.alpha_composite(scale_alpha(ring.copy(), alpha))

    # logo + text appear a beat after the ring
    la = alpha * smooth(0.92, 1.15, t)
    if la <= 0.01:
        return
    lyr = new_layer()
    d = ImageDraw.Draw(lyr)

    # RWA wordmark inside the ring
    lf = font(150, "Black")
    draw_text_spaced(d, (0, CY - 118), "RWA", lf, rgba(WHITE, 255),
                     tracking=6, anchor_center=CX)
    tf = font(26, "Semibold")
    draw_text_spaced(d, (0, CY + 55), "CODE. SOLVE. ELEVATE.", tf, rgba(GRAY, 255),
                     tracking=8, anchor_center=CX)

    # Name: RAO WAQAS AKRAM  (WAQAS in accent)
    nf = font(72, "Heavy")
    parts = [("RAO ", WHITE), ("WAQAS", BLUE), (" AKRAM", WHITE)]
    total = sum(d.textlength(p, font=nf) for p, _ in parts)
    x = CX - total / 2
    ny = 990
    for txt, col in parts:
        d.text((x, ny), txt, font=nf, fill=rgba(col, 255))
        x += d.textlength(txt, font=nf)

    # role line with flanking rules
    rf = font(34, "Semibold")
    role = "SOFTWARE ENGINEERING MENTOR"
    rw = spaced_width(d, role, rf, 6)
    ry = 1110
    draw_text_spaced(d, (0, ry), role, rf, rgba((200, 208, 218), 255),
                     tracking=6, anchor_center=CX)
    ruley = ry + 22
    d.line([(CX - rw / 2 - 70, ruley), (CX - rw / 2 - 24, ruley)], fill=rgba(BLUE, 220), width=3)
    d.line([(CX + rw / 2 + 24, ruley), (CX + rw / 2 + 70, ruley)], fill=rgba(BLUE, 220), width=3)

    # code glyph accent
    cf = font(60, "Heavy")
    cw = d.textlength("</>", font=cf)
    d.text((CX - cw / 2, 1190), "</>", font=cf, fill=rgba(BLUE, 255))

    # gentle rise-up on entrance
    dy = int((1 - smooth(0.92, 1.25, t)) * 40)
    if dy:
        from PIL import ImageChops
        lyr = ImageChops.offset(lyr, 0, dy)
    base.alpha_composite(scale_alpha(glow(lyr, 14, 0.5), la * 0.5))
    base.alpha_composite(scale_alpha(lyr, la))


# ---------------------------------------------------------------------------
# Social icons (rebuilt as clean tiles, storyboard set: YouTube / IG / LinkedIn)
# ---------------------------------------------------------------------------
def _rounded_tile(s, radius, fill):
    tile = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=radius, fill=fill)
    return tile, d


def icon_youtube(s=150):
    tile, d = _rounded_tile(s, int(s * 0.26), (237, 28, 36, 255))
    # white play triangle
    t = s * 0.20
    cx, cy = s / 2, s / 2
    d.polygon([(cx - t * 0.55, cy - t), (cx - t * 0.55, cy + t), (cx + t, cy)], fill=WHITE)
    return tile


def icon_linkedin(s=150):
    tile, d = _rounded_tile(s, int(s * 0.26), (14, 118, 200, 255))
    lf = font(int(s * 0.5), "Black")
    tw = d.textlength("in", font=lf)
    d.text((s / 2 - tw / 2, s * 0.24), "in", font=lf, fill=WHITE)
    return tile


def icon_instagram(s=150):
    # diagonal purple->pink->orange gradient masked to a rounded square
    yy, xx = np.mgrid[0:s, 0:s].astype(np.float32)
    g = (xx + (s - yy)) / (2 * s)                # 0 (top-left) .. 1 (bottom-right)
    stops = [(0.0, (64, 93, 230)), (0.35, (131, 58, 180)),
             (0.7, (225, 48, 108)), (1.0, (253, 176, 34))]
    arr = np.zeros((s, s, 3), np.float32)
    for k in range(len(stops) - 1):
        (p0, c0), (p1, c1) = stops[k], stops[k + 1]
        m = (g >= p0) & (g <= p1)
        f = ((g - p0) / (p1 - p0))[m][:, None]
        arr[m] = np.array(c0) * (1 - f) + np.array(c1) * f
    grad = Image.fromarray(arr.astype(np.uint8), "RGB").convert("RGBA")
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.26), fill=255)
    tile = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    tile.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(tile)
    m = s * 0.20
    lw = max(4, int(s * 0.055))
    d.rounded_rectangle([m, m, s - m, s - m], radius=int(s * 0.22), outline=WHITE, width=lw)
    lr = s * 0.15
    d.ellipse([s / 2 - lr, s / 2 - lr, s / 2 + lr, s / 2 + lr], outline=WHITE, width=lw)
    dr = s * 0.045
    d.ellipse([s - m - dr * 2.2, m + dr * 0.4, s - m - dr * 0.2, m + dr * 2.6], fill=WHITE)
    return tile


def draw_rocket(d, cx, cy, s):
    """Simple stylised rocket pointing up-right (vector, on-brand)."""
    # body
    d.polygon([(cx, cy - s), (cx + 0.32 * s, cy - 0.2 * s),
               (cx + 0.20 * s, cy + 0.45 * s), (cx - 0.20 * s, cy + 0.45 * s),
               (cx - 0.32 * s, cy - 0.2 * s)], fill=WHITE)
    d.ellipse([cx - 0.14 * s, cy - 0.35 * s, cx + 0.14 * s, cy - 0.07 * s], fill=BLUE)
    # fins
    d.polygon([(cx - 0.20 * s, cy + 0.15 * s), (cx - 0.42 * s, cy + 0.5 * s),
               (cx - 0.20 * s, cy + 0.45 * s)], fill=lerp(BLUE, PURPLE, 0.5))
    d.polygon([(cx + 0.20 * s, cy + 0.15 * s), (cx + 0.42 * s, cy + 0.5 * s),
               (cx + 0.20 * s, cy + 0.45 * s)], fill=lerp(BLUE, PURPLE, 0.5))
    # flame
    d.polygon([(cx - 0.12 * s, cy + 0.45 * s), (cx, cy + 0.9 * s),
               (cx + 0.12 * s, cy + 0.45 * s)], fill=(255, 170, 40, 255))


_IG = _YT = _LI = None


def _ensure_icons():
    global _IG, _YT, _LI
    if _IG is None:
        _YT, _IG, _LI = icon_youtube(), icon_instagram(), icon_linkedin()


# ---------------------------------------------------------------------------
# SCENE 3 — FOLLOW FOR MORE + socials
# ---------------------------------------------------------------------------
_SOCIALS = [("YOUTUBE",), ("INSTAGRAM",), ("LINKEDIN",)]


def scene3_cta(base, t, alpha):
    _ensure_icons()
    lyr = new_layer()
    d = ImageDraw.Draw(lyr)

    # Headline "FOLLOW" (FO white + LLOW blue) then "FOR MORE" purple
    hf = font(120, "Black")
    fo_w = d.textlength("FO", font=hf)
    llow_w = d.textlength("LLOW", font=hf)
    total = fo_w + llow_w
    hx = CX - total / 2 - 30
    hy = 470
    rise = (1 - smooth(1.82, 2.02, t)) * 50
    d.text((hx, hy - rise), "FO", font=hf, fill=rgba(WHITE, 255))
    d.text((hx + fo_w, hy - rise), "LLOW", font=hf, fill=rgba(BLUE, 255))
    draw_rocket(d, hx + total + 78, hy + 60 - rise, 78)

    f2 = font(104, "Heavy")
    fm_w = d.textlength("FOR MORE", font=f2)
    d.text((CX - fm_w / 2, hy + 120 - rise * 0.6), "FOR MORE", font=f2, fill=rgba(PURPLE, 255))

    sf = font(38, "Semibold")
    sub = "LET'S GROW TOGETHER"
    draw_text_spaced(d, (0, hy + 270), sub, sf, rgba((215, 222, 232), 255),
                     tracking=7, anchor_center=CX)

    scale_alpha(lyr, alpha)
    base.alpha_composite(scale_alpha(glow(lyr, 16, 0.4), alpha * 0.4))
    base.alpha_composite(lyr)

    # Social icons pop-in (staggered, overshoot) + labels
    tiles = [_YT, _IG, _LI]
    labels = ["YOUTUBE", "INSTAGRAM", "LINKEDIN"]
    base_s = 150
    gap = 90
    n = 3
    span = n * base_s + (n - 1) * gap
    x0 = (W - span) // 2 + base_s // 2
    icy = 1120
    lblf = font(26, "Semibold")
    for i, tile in enumerate(tiles):
        st = 1.92 + i * 0.10
        p = clamp((t - st) / 0.42)
        if p <= 0:
            continue
        sc = clamp(ease_out_back(p, 2.2), 0.05, 1.15)
        ia = clamp(p * 1.6)
        icx = x0 + i * (base_s + gap)
        sz = max(6, int(base_s * sc))
        rs = tile.resize((sz, sz), Image.LANCZOS)
        if ia < 1.0:
            a = rs.split()[3].point(lambda q: int(q * ia))
            rs.putalpha(a)
        # icon glow
        gl = glow(rs, 16, 0.7)
        base.alpha_composite(gl, (int(icx - sz / 2), int(icy - sz / 2)))
        base.alpha_composite(rs, (int(icx - sz / 2), int(icy - sz / 2)))
        # label
        if p > 0.5:
            lbl = labels[i]
            lw = d.textlength(lbl, font=lblf)
            ld = ImageDraw.Draw(base)
            la = int(200 * clamp((p - 0.5) / 0.5))
            ld.text((icx - lw / 2, icy + base_s / 2 + 22), lbl, font=lblf,
                    fill=rgba((170, 180, 195), la))

    # three progress dots
    dd = ImageDraw.Draw(base)
    dots = [BLUE, PURPLE, BLUE]
    dgap = 44
    dx0 = CX - dgap
    for i, c in enumerate(dots):
        pa = smooth(2.02 + i * 0.06, 2.14 + i * 0.06, t)
        rr = 8
        cxp = dx0 + i * dgap
        dd.ellipse([cxp - rr, 1420 - rr, cxp + rr, 1420 + rr], fill=rgba(c, int(255 * pa)))


# ---------------------------------------------------------------------------
# Post: vignette
# ---------------------------------------------------------------------------
def _vignette():
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    dx = (xx - W / 2) / (W / 2)
    dy = (yy - H / 2) / (H / 2)
    dist = np.sqrt(dx * dx + dy * dy)
    v = np.clip(1.0 - 0.55 * np.clip(dist - 0.5, 0, 1.2), 0.35, 1.0)
    return v[..., None]


_VIG = _vignette()


# ---------------------------------------------------------------------------
# Frame compositor
# ---------------------------------------------------------------------------
def frame(t):
    base = Image.new("RGBA", (W, H), rgba(BG, 255))

    # scene alphas / cross-fades
    a1 = 1.0 - smooth(0.55, 0.75, t)
    a2 = smooth(0.68, 0.9, t) * (1 - smooth(1.68, 1.86, t))
    a3 = smooth(1.8, 1.98, t)

    p1 = clamp(t / 0.7)

    # ambient field brightens under scene 1 warp, settles afterwards
    draw_ambient(base, t, dim=0.75 + 0.25 * (1 - a1))

    if a1 > 0.01:
        draw_warp(base, t, p1)
        scene1_terminal(base, t, a1)

    if a2 > 0.01:
        scene2_logo(base, t, a2)

    if a3 > 0.01:
        scene3_cta(base, t, a3)

    # transition light-streak sweeps at scene boundaries
    for center, width, peak in ((0.7, 260, 0.72), (1.8, 220, 1.82)):
        sa = (smooth(peak - 0.12, peak, t) * (1 - smooth(peak, peak + 0.16, t)))
        if sa > 0.02:
            sl = new_layer()
            sd = ImageDraw.Draw(sl)
            yb = CY if center < 1.0 else 560
            sd.rectangle([0, yb - width // 2, W, yb + width // 2],
                         fill=rgba(lerp(WHITE, BLUE, 0.25), int(200 * sa)))
            sl = glow(sl, 60, 1.0)
            base.alpha_composite(sl)

    rgb = np.asarray(base.convert("RGB"), np.float32)
    rgb = np.clip(rgb * _VIG, 0, 255).astype(np.uint8)
    return Image.fromarray(rgb, "RGB")


def main():
    os.makedirs(FRAMES, exist_ok=True)
    n = int(round(DUR * FPS))
    for i in range(n):
        frame(i / FPS).save(os.path.join(FRAMES, f"f_{i:04d}.png"))
        if i % 15 == 0:
            print(f"  frame {i+1}/{n}")
    subprocess.run(
        ["ffmpeg", "-y", "-framerate", str(FPS), "-i", os.path.join(FRAMES, "f_%04d.png"),
         "-f", "lavfi", "-t", str(DUR),
         "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
         "-vf", "format=yuv420p", "-c:v", "libx264", "-crf", "18", "-preset", "slow",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart",
         "-c:a", "aac", "-b:a", "192k", "-shortest", OUT, "-loglevel", "error"],
        check=True)
    print("cinematic outro ->", OUT, f"({n} frames @ {FPS}fps)")


if __name__ == "__main__":
    main()
