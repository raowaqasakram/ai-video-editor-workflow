"""Designed studio backdrops for the vertical frame (Pillow -> static PNG plate).

The default framing fills the top and bottom bands with a blurred, darkened copy
of the speaker's own room. It works, but it reads as "a blurry copy of my wall".
These plates replace that fill with a deliberately designed studio environment.

Nothing here cuts the speaker out of his room, so nothing here can produce the
halo or flicker edges that make a composite look synthetic. What changes is the
environment *around* the camera feed. (For actual background replacement, that is
matting — a separate, heavier job.)

Two ingredients do most of the work and are easy to under-use:

* **Bokeh** — out-of-focus practical lights. This is what makes a backdrop read as
  a real room with lamps in it rather than a flat gradient.
* **Vignette + grain** — applied later in ffmpeg, not here. Real lenses vignette
  and real footage has grain; a perfectly clean composite is the thing that looks
  computer-generated.

Styles:
    studio_bands  cool charcoal set, brand-blue bokeh. Pairs with full-width
                  footage, so the speaker's face keeps its current size.
    studio_set    warm set with amber practicals. Pairs with the footage inset in
                  a framed window, so more of the set is visible.

Usage:
    python3 pipeline/backdrop.py studio_bands out.png
"""
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

W, H = 1080, 1920
ACCENT = (0, 174, 239)          # brand #00AEEF

# Per-style recipe. Kept as data so a new look is a dict entry, not a new function.
STYLES = {
    "studio_bands": {
        "gradient": ((26, 31, 40), (10, 13, 18)),
        "panels": ((255, 255, 255), 9, 10),
        "pools": [(W // 2, 210, 620, 300, (44, 78, 104), 120)],
        "bokeh_rgb": ACCENT,
        "bokeh_blur": 16,
        # (x, y, radius, alpha) — kept out of the centre, where the footage sits
        "bokeh": [(150, 250, 46, 120), (930, 190, 34, 105), (250, 1760, 52, 95),
                  (860, 1700, 40, 110), (540, 1840, 30, 80)],
    },
    # For the matted style. No panels: with the room removed there is a lot of
    # visible backdrop, and repeating vertical strips read as wallpaper rather than
    # a room. Depth instead comes from a wide overhead light pool, a soft floor
    # gradient, and bokeh at mixed sizes/softness so the lights sit at different
    # apparent distances.
    "studio_real": {
        "gradient": ((34, 40, 51), (7, 9, 13)),
        "panels": None,
        "pools": [(W // 2, 120, 900, 520, (48, 84, 112), 130),
                  (W // 2, 1900, 1000, 360, (26, 44, 60), 90)],
        "bokeh_rgb": ACCENT,
        "bokeh_blur": 26,
        "bokeh": [(120, 430, 70, 105), (960, 330, 52, 95), (250, 1300, 44, 80),
                  (880, 1180, 62, 90), (150, 1650, 84, 70), (930, 1720, 48, 85),
                  (540, 250, 34, 60)],
    },
    "studio_set": {
        "gradient": ((30, 24, 20), (12, 10, 9)),
        "panels": ((255, 226, 190), 7, 12),
        "pools": [(250, 300, 560, 380, (96, 62, 34), 120),
                  (880, 1500, 520, 420, (70, 48, 30), 95)],
        "bokeh_rgb": (255, 176, 92),
        "bokeh_blur": 20,
        "bokeh": [(170, 1520, 58, 130), (300, 1660, 40, 110), (900, 1420, 48, 120),
                  (990, 1610, 32, 95), (120, 1330, 30, 85)],
    },
}


def _gradient(top_rgb, bottom_rgb):
    """Vertical linear gradient. Never a flat fill — flat reads as a slide."""
    ramp = np.linspace(0.0, 1.0, H, dtype=np.float32)[:, None, None]
    top = np.array(top_rgb, dtype=np.float32)[None, None, :]
    bot = np.array(bottom_rgb, dtype=np.float32)[None, None, :]
    arr = top * (1.0 - ramp) + bot * ramp
    return Image.fromarray(np.repeat(arr.astype(np.uint8), W, axis=1))


def _panels(img, rgb, count, alpha):
    """Faint vertical strips — acoustic panels on the back wall. Keep it subtle."""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    step = W // count
    for i in range(0, count, 2):
        d.rectangle([i * step, 0, i * step + step // 2, H], fill=rgb + (alpha,))
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


def _pool(img, cx, cy, rx, ry, rgb, strength):
    """Soft elliptical glow: a lamp throwing light onto the backdrop."""
    glow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(glow).ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=strength)
    img.paste(Image.new("RGB", (W, H), rgb), (0, 0),
              glow.filter(ImageFilter.GaussianBlur(rx * 0.45)))
    return img


def _bokeh(img, spots, rgb, blur):
    """Out-of-focus practicals. The main cue that this is a lit space."""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for cx, cy, r, a in spots:
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=rgb + (a,))
    return Image.alpha_composite(img.convert("RGBA"),
                                 layer.filter(ImageFilter.GaussianBlur(blur))).convert("RGB")


def _grain(img, amount=3.5, seed=7):
    """Bake fine static grain into the plate.

    Deliberately baked here rather than applied as ffmpeg's `noise` filter. `noise`
    re-randomises every frame, so inter-frame compression collapses — a 26s test
    came out at 18.7 Mbps / 72 MB instead of ~2 Mbps. Baked grain is identical in
    every frame, so it costs essentially nothing, and the footage itself already
    supplies real (moving) sensor grain where the eye actually looks.
    """
    rng = np.random.default_rng(seed)
    arr = np.asarray(img).astype(np.float32)
    arr += rng.normal(0.0, amount, (H, W, 1))
    return Image.fromarray(arr.clip(0, 255).astype(np.uint8))


def plate(style, out_path, hairline=True):
    """Render the backdrop plate for `style` to `out_path`. Cached by mtime.

    The plate is a single static PNG reused for every frame of every video in this
    style, so building it is a one-off cost of well under a second.
    """
    if style not in STYLES:
        raise KeyError("unknown backdrop style %r (have: %s)"
                       % (style, ", ".join(sorted(STYLES))))
    if os.path.exists(out_path):
        return out_path

    spec = STYLES[style]
    img = _gradient(*spec["gradient"])
    if spec["panels"]:
        img = _panels(img, *spec["panels"])
    for pool in spec["pools"]:
        img = _pool(img, *pool)
    img = _bokeh(img, spec["bokeh"], spec["bokeh_rgb"], spec["bokeh_blur"])
    img = _grain(img)
    if hairline:
        ImageDraw.Draw(img).rectangle([0, 0, W, 5], fill=ACCENT)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    img.save(out_path)
    print("  backdrop plate (%s) -> %s" % (style, os.path.basename(out_path)))
    return out_path


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: backdrop.py <%s> <out.png>" % "|".join(sorted(STYLES)))
        sys.exit(1)
    plate(sys.argv[1], sys.argv[2])
