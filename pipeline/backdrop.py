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
    studio_real   a rendered office/studio for the matted style, where the plate
                  IS the visible background. See _studio_room().

Any style can instead be built from a real photograph of a room, which beats
anything rendered — see _photo_room().

Usage:
    python3 pipeline/backdrop.py studio_bands out.png
    python3 pipeline/backdrop.py studio_real out.png assets/backdrops/office.jpg
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
    # studio_real is NOT in this table — it is a rendered room, not a recipe of
    # blobs. See _studio_room() below for why.
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


# Every style plate() can build. STYLES holds only the recipe-driven ones, so
# studio_real (a rendered room, not a recipe) has to be added explicitly — keeping
# the full list in one name stops it drifting out of sync with
# screenshare_vertical.STYLES.
PLATE_STYLES = tuple(sorted(STYLES)) + ("studio_real",)


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


# ---------------------------------------------------------------------------
# studio_real — a rendered room, not a pattern
# ---------------------------------------------------------------------------
# The first version of this plate was a blue gradient with soft blue circles on it.
# With the room matted away it filled most of the frame, and it read as a slide
# background: the speaker looked cut out and pasted onto a title card. Two things
# were wrong, and neither was fixable by moving the circles around.
#
# 1. COLOUR. It was saturated brand blue everywhere. Real rooms are near-neutral —
#    warm grey walls, amber practicals, and at most one small cool source. A wash
#    of one saturated hue is the single loudest "this is synthetic" signal, so the
#    accent now survives only as one dim monitor glow, well off-centre.
#
# 2. STRUCTURE. Circles on a gradient have no geometry. A real background has a
#    wall/floor junction, a corner where two wall planes meet, and objects at
#    different distances. Those edges are what the eye reads as depth, even when
#    everything is thrown far out of focus.
#
# The method is to draw the room with hard edges and then defocus the whole thing
# hard. That ordering matters: shapes blurred by 30px behave like real bokeh, while
# shapes *drawn* soft just look like airbrush. Practical lights are added after the
# blur so they keep a clean falloff rather than being smeared twice.
#
# Layout is driven by what is actually visible behind the subject. He fills the
# centre of the frame, so the backdrop only shows in the top band, the left/right
# strips, and the bottom (mostly under the caption bar). The shelf and the corner
# are therefore placed where they will be seen — upper right and right strip — and
# the area directly behind his head is left as clean, softly lit wall.
ROOM = {
    "wall_top": (74, 69, 63),        # warm grey, lit from above
    "wall_bot": (22, 21, 20),
    "floor_y": 1500,                 # wall/floor junction
    "floor_rgb": (15, 14, 13),
    "corner_x": 812,                 # where the two wall planes meet
    "corner_gain": 1.11,             # right plane catches slightly more of the key
    "key": (250, 150, 820, (1.34, 1.28, 1.16)),   # cx, cy, radius, per-channel gain
    # Enough to read as background, not so much that structure dissolves. At 34 the
    # shelf and corner turned to soup and the plate went back to looking like a
    # gradient; 20 keeps their edges legible as shapes.
    "defocus": 20,
    "practicals": [
        # cx, cy, rx, ry, rgb, strength — warm lamps, one dim cool monitor spill
        (905, 980, 210, 260, (196, 132, 62), 78),     # shelf lamp, warm
        (150, 1610, 300, 330, (168, 108, 52), 52),    # floor lamp wash, warm
        (72, 640, 190, 420, (150, 146, 138), 30),     # soft daylight from left
        (1010, 1500, 240, 260, (46, 96, 126), 34),    # monitor glow, the only cool
    ],
    "bokeh": [
        # cx, cy, r, alpha, rgb — practical highlights at mixed size = mixed distance
        (938, 934, 30, 150, (255, 196, 120)),
        (884, 1052, 19, 110, (255, 186, 112)),
        (996, 1128, 14, 86, (255, 204, 140)),
        (96, 1548, 34, 96, (255, 178, 104)),
        (188, 1672, 21, 74, (255, 190, 120)),
        (1032, 268, 17, 58, (208, 214, 220)),
    ],
}


def _noise_field(seed, cell, blur, shape=(H, W)):
    """Smooth large-scale luminance variation in [0,1].

    Real walls are never evenly lit — there is always some broad mottling from the
    paint, the ceiling bounce, and the room's own geometry. Leaving it out is what
    makes a rendered wall look like a fill, so this is multiplied over the base.
    """
    rng = np.random.default_rng(seed)
    small = rng.random((shape[0] // cell + 2, shape[1] // cell + 2)).astype(np.float32)
    img = Image.fromarray((small * 255).astype(np.uint8)) \
               .resize((shape[1], shape[0]), Image.BICUBIC) \
               .filter(ImageFilter.GaussianBlur(blur))
    return np.asarray(img).astype(np.float32) / 255.0


def _shelf(draw):
    """Defocused shelving unit, upper right — the main depth cue in the frame.

    Drawn with hard edges on purpose; the global defocus turns these into the soft
    slabs a real shelf becomes at f/1.8. Colours stay muted and dark: anything
    bright here competes with the speaker's face.
    """
    x0, x1 = 838, 1080
    draw.rectangle([x0, 596, x1, 1470], fill=(31, 28, 26, 235))
    for y in (596, 782, 968, 1154, 1340):                    # shelf boards
        draw.rectangle([x0, y, x1, y + 13], fill=(58, 52, 46, 240))
    books = [(852, 800, 22, (74, 46, 38)), (878, 806, 16, (58, 62, 54)),
             (900, 796, 25, (96, 74, 44)), (932, 810, 14, (48, 44, 48)),
             (854, 1174, 20, (62, 56, 66)), (880, 1168, 27, (88, 62, 40)),
             (914, 1180, 15, (52, 58, 52))]
    for bx, by, bw, rgb in books:
        draw.rectangle([bx, by, bx + bw, by + 154], fill=rgb + (225,))


def _plant(draw):
    """Defocused foliage, lower left. Breaks up the empty wall on that side."""
    for cx, cy, r in [(112, 1418, 96), (196, 1352, 74), (58, 1310, 62),
                      (168, 1490, 84), (240, 1452, 52), (96, 1214, 44)]:
        draw.ellipse([cx - r, cy - r * 1.35, cx + r, cy + r * 1.35],
                     fill=(22, 30, 23, 215))


def _studio_room():
    """Render the studio_real plate: a lit room, thrown out of focus."""
    yy = np.linspace(0.0, 1.0, H, dtype=np.float32)[:, None, None]
    top = np.array(ROOM["wall_top"], dtype=np.float32)[None, None, :]
    bot = np.array(ROOM["wall_bot"], dtype=np.float32)[None, None, :]
    arr = np.repeat(top * (1.0 - yy) + bot * yy, W, axis=1)

    # Two wall planes meeting at a corner. The right plane faces the key, so it sits
    # brighter; the seam between them is a straight vertical edge, which is exactly
    # the kind of structure a gradient can never supply.
    arr[:, ROOM["corner_x"]:, :] *= ROOM["corner_gain"]

    # Key light falloff. Multiplicative and per-channel, so the lit side warms up
    # the way tungsten does instead of just getting brighter.
    kx, ky, kr, kgain = ROOM["key"]
    gy, gx = np.mgrid[0:H, 0:W].astype(np.float32)
    fall = np.exp(-(((gx - kx) ** 2 + (gy - ky) ** 2) / (2.0 * kr * kr)))[:, :, None]
    arr *= 1.0 + fall * (np.array(kgain, dtype=np.float32)[None, None, :] - 1.0)

    # Floor: darker, and separated from the wall by a real junction line rather than
    # a fade, so the room has a ground plane.
    fy = ROOM["floor_y"]
    floor = np.array(ROOM["floor_rgb"], dtype=np.float32)[None, None, :]
    drop = np.linspace(0.0, 1.0, H - fy, dtype=np.float32)[:, None, None] ** 0.6
    arr[fy:, :, :] = arr[fy:, :, :] * (1.0 - drop) + floor * drop
    arr[fy:fy + 3, :, :] *= 1.45                      # light catching the skirting

    arr *= (0.86 + 0.28 * _noise_field(11, 90, 70))[:, :, None]   # wall mottling

    img = Image.fromarray(arr.clip(0, 255).astype(np.uint8))
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    _shelf(d)
    _plant(d)
    img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")

    # Everything above is in focus, and in focus it looks drawn. This is the step
    # that turns it into a background.
    img = img.filter(ImageFilter.GaussianBlur(ROOM["defocus"]))

    # Practicals and their highlights go on AFTER the defocus, so they keep a clean
    # falloff instead of being blurred twice into mush.
    for cx, cy, rx, ry, rgb, strength in ROOM["practicals"]:
        img = _pool(img, cx, cy, rx, ry, rgb, strength)
    for cx, cy, r, a, rgb in ROOM["bokeh"]:
        img = _bokeh(img, [(cx, cy, r, a)], rgb, max(6, r // 2))
    return img


def _photo_room(photo_path):
    """Build the plate from a real photograph of a room.

    Nothing rendered will ever beat an actual photo, so this is the preferred path
    when one is available. The photo is treated the way a camera would treat that
    room in the background of a portrait: cropped to the vertical frame, thrown far
    out of focus, pulled down in exposure and saturation so it sits behind the
    speaker instead of competing with him, and darkened at the edges.
    """
    src = Image.open(photo_path).convert("RGB")
    # Cover-crop to 9:16 — never letterbox, never squash.
    scale = max(W / src.width, H / src.height)
    src = src.resize((max(W, int(round(src.width * scale))),
                      max(H, int(round(src.height * scale)))), Image.LANCZOS)
    left, top = (src.width - W) // 2, (src.height - H) // 2
    img = src.crop((left, top, left + W, top + H))

    img = img.filter(ImageFilter.GaussianBlur(ROOM["defocus"]))
    arr = np.asarray(img).astype(np.float32)
    grey = arr.mean(axis=2, keepdims=True)
    arr = (grey + (arr - grey) * 0.72) * 0.52         # desaturate, then underexpose
    # Radial falloff: a real background lit for a portrait is brightest behind the
    # subject and falls away, which also stops the corners pulling the eye outward.
    gy, gx = np.mgrid[0:H, 0:W].astype(np.float32)
    r = np.sqrt(((gx - W / 2) / (W / 2)) ** 2 + ((gy - H / 2.4) / (H / 2)) ** 2)
    arr *= np.clip(1.06 - 0.34 * r, 0.35, 1.0)[:, :, None]
    return Image.fromarray(arr.clip(0, 255).astype(np.uint8))


def plate(style, out_path, hairline=True, photo=None):
    """Render the backdrop plate for `style` to `out_path`. Cached by mtime.

    The plate is a single static PNG reused for every frame of every video in this
    style, so building it is a one-off cost of well under a second.
    """
    if style not in PLATE_STYLES:
        raise KeyError("unknown backdrop style %r (have: %s)"
                       % (style, ", ".join(PLATE_STYLES)))
    if os.path.exists(out_path):
        return out_path

    if photo:
        img = _photo_room(photo)
    elif style == "studio_real":
        img = _studio_room()
    else:
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
        print("usage: backdrop.py <%s> <out.png> [room-photo.jpg]"
              % "|".join(PLATE_STYLES))
        sys.exit(1)
    plate(sys.argv[1], sys.argv[2], photo=sys.argv[3] if len(sys.argv) > 3 else None)
