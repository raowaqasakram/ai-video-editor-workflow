"""PIL overlay renderer for the AI Content Studio social treatment.

Produces transparent PNGs composited later with ffmpeg `overlay`:
  - question card (opaque 1080x1920 intro)
  - lower-third branding (transparent)
  - captions (transparent, keyword-highlighted)  [used in the caption pass]

Uses SF (San Francisco) for a premium/Apple feel, falling back to Arial.
"""
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 1920
ACCENT = (0, 174, 239, 255)      # #00AEEF
WHITE = (255, 255, 255, 255)
SECONDARY = (204, 204, 204, 255)
BG = (11, 15, 20, 255)           # near-black premium

SF = "/System/Library/Fonts/SFNS.ttf"
ARIAL_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
ARIAL = "/System/Library/Fonts/Supplemental/Arial.ttf"

# Alternate caption typefaces (see build_reel's `caption_font`). SF is a
# variable font (weight set via set_variation_by_name); the others are TTCs
# with each weight baked into its own face index — no variation axis to set.
AVENIR_NEXT = "/System/Library/Fonts/Avenir Next.ttc"
AVENIR_NEXT_WEIGHTS = {"Heavy": 8, "Bold": 0, "Semibold": 2, "Regular": 7}
HELVETICA_NEUE = "/System/Library/Fonts/HelveticaNeue.ttc"
# No Heavy/Black face in this ttc — Bold is the heaviest non-condensed weight.
HELVETICA_NEUE_WEIGHTS = {"Heavy": 1, "Bold": 1, "Semibold": 10, "Regular": 0}
FONT_FAMILIES = {"avenir": (AVENIR_NEXT, AVENIR_NEXT_WEIGHTS),
                  "helvetica_neue": (HELVETICA_NEUE, HELVETICA_NEUE_WEIGHTS)}


def font(size, weight="Bold", family=None):
    """Load a font at a given size/weight, falling back to Arial.

    `family`: None for the default SF; or a key into FONT_FAMILIES to render
    with a different caption typeface (a per-video creative choice, not a
    brand-wide default — see config.json's `caption_font`).
    """
    if family:
        path, weights = FONT_FAMILIES[family]
        try:
            return ImageFont.truetype(path, size, index=weights.get(weight, 0))
        except Exception:
            pass
    try:
        f = ImageFont.truetype(SF, size)
        try:
            f.set_variation_by_name(weight)
        except Exception:
            pass
        return f
    except Exception:
        return ImageFont.truetype(ARIAL_BOLD if weight in ("Bold", "Heavy", "Semibold") else ARIAL, size)


def wrap(draw, text, fnt, max_w):
    """Word-wrap text to fit max_w pixels; returns list of lines."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=fnt) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def rounded(draw, box, r, fill):
    draw.rounded_rectangle(box, radius=r, fill=fill)


FOOTER = "Rao Waqas Akram  •  Sr. Software Engineer | Mentor"
CARD_LABEL = "VIEWER QUESTION"

# Intro-card colour schemes. The five CARD_DESIGNS are layouts; this is the
# palette they are drawn in, so a design choice and a light/dark choice stay
# independent. "dark" is the original near-black card — every card shipped before
# 2026-08-06 is that one, and it re-renders identically.
#
# "light" exists because the reel body ships on a WHITE fill (see
# feedback-white-fill-equal-borders): a near-black card in front of a white body
# reads as two different videos joined together. The white here is 253/253/253,
# the same broadcast white drawbox writes behind the footage, so the card and the
# body's bands are the same white on the export.
CARD_THEMES = {
    "dark": {
        "bg": BG,
        "ink": WHITE,                      # question text
        "sub": SECONDARY,                  # handle
        "footer": (150, 160, 170, 255),
        "label_ink": (8, 12, 16, 255),     # text on an accent pill
        "rule": (255, 255, 255, 60),
        "hairline": (255, 255, 255, 34),
        "pill": (255, 255, 255, 26),
        "watermark": (255, 255, 255, 11),
        "quote": ACCENT[:3] + (90,),
        "quote_soft": ACCENT[:3] + (80,),   # the banner's glyph sits on a lighter field
        "glow": 120,
        "ground": ((13, 18, 25, 255), (8, 11, 15, 255)),
        "panel": (24, 31, 40, 255),
        "panel_edge": None,
        "role": (200, 208, 218, 255),
    },
    "light": {
        "bg": (253, 253, 253, 255),
        "ink": (11, 15, 20, 255),
        "sub": (92, 102, 114, 255),
        "footer": (128, 138, 150, 255),
        "label_ink": (6, 20, 28, 255),
        "rule": (11, 15, 20, 70),
        "hairline": (11, 15, 20, 40),
        "pill": (11, 15, 20, 18),
        "watermark": (11, 15, 20, 14),
        "quote": ACCENT[:3] + (110,),
        "quote_soft": ACCENT[:3] + (100,),
        "glow": 46,                        # a soft wash; 120 on white is a bruise
        "ground": ((247, 249, 251, 255), (236, 240, 245, 255)),
        "panel": (255, 255, 255, 255),
        "panel_edge": (11, 15, 20, 26),    # a white panel on near-white needs one
        "role": (86, 96, 108, 255),
    },
}


def fit_lines(d, text, max_w, max_lines=5, sizes=(76, 70, 64, 58, 52, 46), weight="Bold"):
    """Largest font from `sizes` whose wrap fits in `max_lines`. Returns (font, lines).

    Question length varies a lot between viewers — a fixed size either overflows
    the card on a long question or wastes the canvas on a short one.
    """
    for s in sizes:
        f = font(s, weight)
        lines = wrap(d, text, f, max_w)
        if len(lines) <= max_lines:
            return f, lines, s
    f = font(sizes[-1], weight)
    return f, wrap(d, text, f, max_w)[:max_lines], sizes[-1]


def _gradient(size, top, bottom, horizontal=False):
    """Vertical (or horizontal) two-stop gradient as an RGBA image."""
    w, h = size
    n = w if horizontal else h
    ramp = Image.new("RGBA", (1, n))
    px = ramp.load()
    for i in range(n):
        t = i / max(1, n - 1)
        px[0, i] = tuple(int(top[c] + (bottom[c] - top[c]) * t) for c in range(4))
    if horizontal:
        ramp = ramp.rotate(-90, expand=True)
    return ramp.resize((w, h))


def _glow(img, cx, cy, r, colour, alpha=110):
    """Soft radial accent bloom, composited under the text."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse([cx - r, cy - r, cx + r, cy + r],
                                  fill=colour[:3] + (alpha,))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(r * 0.45)))


def _footer(d, colour=(150, 160, 170, 255), y=H - 150):
    f = font(34, "Semibold")
    d.text(((W - d.textlength(FOOTER, font=f)) / 2, y), FOOTER, font=f, fill=colour)


def _card_classic(img, d, question, handle, pal):
    """D0 — the original: accent rules, big quote glyph, left-aligned question."""
    d.rectangle([0, 0, W, 10], fill=ACCENT)
    d.rectangle([0, H - 10, W, H], fill=(ACCENT[0], ACCENT[1], ACCENT[2], 120))

    margin = 110
    lf = font(40, "Heavy")
    lw = d.textlength(CARD_LABEL, font=lf)
    rounded(d, [margin, 470, margin + lw + 70, 470 + 78], 39, ACCENT)
    d.text((margin + 35, 470 + 18), CARD_LABEL, font=lf, fill=pal["label_ink"])

    d.text((margin - 12, 560), "“", font=font(220, "Heavy"), fill=pal["quote"])

    tf, lines, size = fit_lines(d, question, W - 2 * margin, max_lines=6)
    y = 720
    for ln in lines:
        d.text((margin, y), ln, font=tf, fill=pal["ink"])
        y += int(size * 1.31)
    d.text((margin, y + 40), handle, font=font(42, "Semibold"), fill=pal["sub"])
    _footer(d, pal["footer"])
    return len(lines)


def _card_spotlight(img, d, question, handle, pal):
    """D1 — centred, with an accent bloom behind the text."""
    _glow(img, W // 2, 900, 560, ACCENT, alpha=pal["glow"])

    lf = font(38, "Heavy")
    lw = d.textlength(CARD_LABEL, font=lf)
    lx = (W - lw) / 2
    d.text((lx, 520), CARD_LABEL, font=lf, fill=ACCENT)
    rule_y = 520 + 22
    d.rectangle([lx - 130, rule_y, lx - 40, rule_y + 3], fill=(ACCENT[0], ACCENT[1], ACCENT[2], 140))
    d.rectangle([lx + lw + 40, rule_y, lx + lw + 130, rule_y + 3], fill=(ACCENT[0], ACCENT[1], ACCENT[2], 140))

    margin = 120
    tf, lines, size = fit_lines(d, question, W - 2 * margin, max_lines=6)
    step = int(size * 1.30)
    y = 900 - (len(lines) * step) // 2
    for ln in lines:
        d.text(((W - d.textlength(ln, font=tf)) / 2, y), ln, font=tf, fill=pal["ink"])
        y += step

    hf = font(40, "Semibold")
    hw = d.textlength(handle, font=hf)
    pill = [(W - hw) / 2 - 38, y + 56, (W + hw) / 2 + 38, y + 56 + 76]
    rounded(d, pill, 38, pal["pill"])
    d.text(((W - hw) / 2, y + 74), handle, font=hf, fill=pal["sub"])
    _footer(d, pal["footer"])
    return len(lines)


def _card_panel(img, d, question, handle, pal):
    """D2 — a raised panel floating on a gradient ground."""
    img.alpha_composite(_gradient((W, H), *pal["ground"]))

    margin, top, bottom = 84, 430, 1420
    panel = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pd = ImageDraw.Draw(panel)
    pd.rounded_rectangle([margin, top, W - margin, bottom], radius=44,
                         fill=pal["panel"], outline=pal["panel_edge"],
                         width=2 if pal["panel_edge"] else 0)
    img.alpha_composite(panel)
    # accent cap on the panel's top edge
    cap = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(cap).rounded_rectangle([margin, top, W - margin, top + 44],
                                          radius=44, fill=ACCENT)
    ImageDraw.Draw(cap).rectangle([margin, top + 22, W - margin, top + 44], fill=ACCENT)
    img.alpha_composite(cap)

    inner = margin + 56
    lf = font(36, "Heavy")
    d.text((inner, top + 96), CARD_LABEL, font=lf, fill=ACCENT)

    tf, lines, size = fit_lines(d, question, W - 2 * inner, max_lines=6)
    y = top + 200
    for ln in lines:
        d.text((inner, y), ln, font=tf, fill=pal["ink"])
        y += int(size * 1.30)

    d.rectangle([inner, bottom - 150, W - inner, bottom - 148], fill=pal["hairline"])
    d.text((inner, bottom - 116), handle, font=font(40, "Semibold"), fill=pal["sub"])
    _footer(d, pal["footer"])
    return len(lines)


def _card_editorial(img, d, question, handle, pal):
    """D3 — left accent rule, oversized type, watermark glyph."""
    # Watermark sits above the footer band — at full height it collided with it.
    d.text((W - 320, 1120), "?", font=font(420, "Heavy"), fill=pal["watermark"])

    margin = 132
    tf, lines, size = fit_lines(d, question, W - margin - 110, max_lines=6)
    step = int(size * 1.28)
    block_h = len(lines) * step
    y0 = 880 - block_h // 2

    d.rectangle([margin - 42, y0 - 96, margin - 28, y0 + block_h + 30], fill=ACCENT)
    d.text((margin, y0 - 92), CARD_LABEL, font=font(36, "Heavy"), fill=ACCENT)

    y = y0
    for ln in lines:
        d.text((margin, y), ln, font=tf, fill=pal["ink"])
        y += step

    d.rectangle([margin, y + 60, margin + 90, y + 63], fill=pal["rule"])
    d.text((margin, y + 96), handle, font=font(40, "Semibold"), fill=pal["sub"])
    _footer(d, pal["footer"])
    return len(lines)


def _card_banner(img, d, question, handle, pal):
    """D4 — accent masthead across the top, question on the field below."""
    band_h = 470
    img.alpha_composite(_gradient((W, band_h), (0, 174, 239, 255), (0, 132, 190, 255)))

    lf = font(44, "Heavy")
    d.text(((W - d.textlength(CARD_LABEL, font=lf)) / 2, 208), CARD_LABEL,
           font=lf, fill=(6, 20, 28, 255))
    hf = font(38, "Semibold")
    d.text(((W - d.textlength(handle, font=hf)) / 2, 288), handle,
           font=hf, fill=(9, 46, 64, 230))

    d.text((92, band_h + 34), "“", font=font(200, "Heavy"), fill=pal["quote_soft"])

    margin = 118
    tf, lines, size = fit_lines(d, question, W - 2 * margin, max_lines=6)
    y = band_h + 250
    for ln in lines:
        d.text((margin, y), ln, font=tf, fill=pal["ink"])
        y += int(size * 1.30)

    d.rectangle([0, H - 10, W, H], fill=ACCENT)
    _footer(d, pal["footer"])
    return len(lines)


# Round-robin pool. Index 0 is the original card, so anything already shipped
# re-renders identically; each following question picks the next design.
CARD_DESIGNS = [
    ("classic", _card_classic),
    ("spotlight", _card_spotlight),
    ("panel", _card_panel),
    ("editorial", _card_editorial),
    ("banner", _card_banner),
]


def card_design_for(n):
    """Round-robin design index for question number `n` (Q1 -> 0, Q6 -> 0)."""
    return (int(n) - 1) % len(CARD_DESIGNS)


def make_question_card(question, handle, out, design=0, theme="dark"):
    """Render the 'Viewer Question' intro card in one of CARD_DESIGNS.

    `design` is an index or a name; out of range wraps, so a question number can
    be passed straight through card_design_for(). `theme` selects the palette
    (CARD_THEMES): "dark" is the original near-black card, "light" is the white
    card that matches a white-fill body.
    """
    if isinstance(design, str):
        names = [n for n, _ in CARD_DESIGNS]
        if design not in names:
            raise KeyError("unknown card design %r (have %s)" % (design, ", ".join(names)))
        idx = names.index(design)
    else:
        idx = int(design or 0) % len(CARD_DESIGNS)
    name, render = CARD_DESIGNS[idx]
    if theme not in CARD_THEMES:
        raise KeyError("unknown card theme %r (have %s)"
                       % (theme, ", ".join(CARD_THEMES)))
    pal = CARD_THEMES[theme]

    img = Image.new("RGBA", (W, H), pal["bg"])
    d = ImageDraw.Draw(img)
    n_lines = render(img, d, question, handle, pal)

    # ImageDraw REPLACES pixels rather than blending them, so every element drawn
    # with alpha < 255 (watermarks, hairline rules, the quote glyph) leaves a
    # translucent hole instead of a tint — and ffmpeg then drops the alpha, which
    # renders those elements at FULL strength. Flattening onto the card's own
    # background resolves them at the intended opacity, and shipping an opaque RGB
    # PNG means nothing downstream has to interpret alpha at all.
    img = Image.alpha_composite(Image.new("RGBA", (W, H), pal["bg"]), img)
    img.convert("RGB").save(out)
    print("card ->", out, f"({name} design, {theme} theme, {n_lines} lines)")


def make_lower_third(name, title, out):
    """Render a transparent lower-third name/title pill (bottom-left)."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    nf = font(52, "Heavy")
    tf = font(34, "Semibold")
    # Top-left name tag during the intro so it never collides with captions.
    x, y = 60, 120
    nw = d.textlength(name, font=nf)
    tw = d.textlength(title, font=tf)
    bw = max(nw, tw) + 130
    # accent left bar + dark rounded panel
    rounded(d, [x, y, x + bw, y + 150], 24, (12, 16, 22, 230))
    d.rectangle([x, y + 22, x + 10, y + 128], fill=ACCENT)
    d.text((x + 40, y + 26), name, font=nf, fill=WHITE)
    d.text((x + 40, y + 92), title, font=tf, fill=(ACCENT[0], ACCENT[1], ACCENT[2], 255))
    img.save(out)
    print("lower-third ->", out)


# Vertical centre of the caption block — a platform safe-zone rule, not taste.
#
# TikTok, Reels, Shorts and Facebook Reels paint their own username, description,
# audio row and right-hand action rail over the bottom of the frame. Published
# safe zones for 1080x1920 put that at ~320px from the bottom for organic posts,
# and ~480px under TikTok's strictest guidance (which also reserves room for a
# CTA button).
#
# The original 1600 put the caption bar's bottom edge only ~212px up — i.e.
# entirely inside the band the app writes over, so captions could be partly
# covered in-feed on a phone.
#
# 1400 is the shipped value, chosen after looking at a real render: 1330 cleared
# TikTok's strictest published zone (480px) but sat visibly high on the speaker's
# chest. 1400 puts a two-line bar at 1282..1508, i.e. 412px of clearance:
#
#     organic username/description band  ~320px   -> clear by ~90px  ✅
#     TikTok's strictest ad-safe zone    ~480px   -> inside by ~70px  ⚠️
#
# That is the deliberate trade-off. The 480px figure reserves room for a CTA
# button that organic posts do not have, so 412px is safe for normal posts on
# TikTok, Reels, Shorts and Facebook Reels. If a clip is ever used as a paid ad,
# set "caption_y": 1330 for that one.
#
# The value is driven by the two-line case (the maximum make_caption renders):
#     bar_bottom = center + line_h + 24
#
# Anything moved here must also clear the screen-share camera well (see
# screenshare_vertical.FACE_Y/FACE_H) — the two constants are coupled, and
# tests/test_pipeline_quality.py asserts both bounds.
CAPTION_CENTER_Y = 1400

# The pre-2026-07-26 position, kept only to document what changed. Do not ship it.
CAPTION_CENTER_Y_LEGACY = 1600

# Bottom band the platform UI can occupy. The organic figure is what we hold to;
# ORGANIC is the assertion in tests, AD_SAFE is documented for paid use.
PLATFORM_UI_RESERVED_PX = 320
PLATFORM_UI_AD_SAFE_PX = 480

# Caption bar geometry, in one place. Three things depend on it: make_caption
# draws the bar, the vertical builder aligns the sharp footage band's bottom edge
# to it (screenshare_vertical.FACE_SEAM_Y), and the tests assert the safe zone.
# It used to be re-derived in each, which is exactly how a caption move and the
# framing drift apart without anything failing.
CAPTION_LINE_H = 84
CAPTION_PAD_Y = 34


def caption_bar_y(lines=2, center_y=None):
    """(top, bottom) of the rendered caption bar for a block of `lines` lines.

    Two lines is the maximum make_caption renders, so `caption_bar_y(2)[0]` is
    the highest a caption can ever reach — the value framing must clear.
    """
    center = CAPTION_CENTER_Y if center_y is None else int(center_y)
    block_h = CAPTION_LINE_H * lines
    top = center - block_h // 2
    return top - CAPTION_PAD_Y, top + block_h + CAPTION_PAD_Y - 10


# Caption colour schemes. "dark" is the original: white text on a near-opaque
# dark plate, which is what makes text readable over *footage*. "light" is for the
# white-fill framing (screenshare_vertical.FACE_BG_WHITE), where the caption sits
# on a solid white band — there a dark plate would read as a black box floating on
# white, so the text is inked directly onto the white instead.
CAPTION_INK = (10, 12, 16, 255)          # near-black, matches BG
CAPTION_THEMES = {
    "dark":  {"text": WHITE, "plate": (10, 12, 16, 205)},
    "light": {"text": CAPTION_INK, "plate": None},
}


def make_caption(text, highlights, out, center_y=None, theme="dark", font_family=None):
    """Render a transparent lower-third caption PNG with keyword highlighting.

    Args:
        text: Caption text (Roman-Urdu + English).
        highlights: Iterable of lowercased keywords to colour in the accent.
        out: Output PNG path.
        center_y: Vertical centre of the block. Defaults to CAPTION_CENTER_Y;
            override only with a value that still clears the platform UI band.
        theme: "dark" (white text on a dark plate, for captions over footage) or
            "light" (dark text, no plate, for captions on the white fill band).
        font_family: None for the default SF, or a FONT_FAMILIES key for a
            different caption typeface on this video.
    """
    scheme = CAPTION_THEMES[theme]
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cf = font(60, "Heavy", family=font_family)
    hl = {h.lower() for h in highlights}
    lines = wrap(d, text, cf, W - 200)[:2]  # max 2 lines

    line_h = CAPTION_LINE_H
    pad_x = 46
    block_h = line_h * len(lines)
    center = CAPTION_CENTER_Y if center_y is None else int(center_y)
    top = center - block_h // 2
    bar_top, bar_bottom = caption_bar_y(len(lines), center_y)
    widest = max(d.textlength(ln, font=cf) for ln in lines)
    if scheme["plate"]:
        bar = [(W - widest) / 2 - pad_x, bar_top,
               (W + widest) / 2 + pad_x, bar_bottom]
        rounded(d, bar, 28, scheme["plate"])

    y = top
    for ln in lines:
        lw = d.textlength(ln, font=cf)
        x = (W - lw) / 2
        for word in ln.split():
            key = word.strip(".,!?/()").lower()
            col = ACCENT if key in hl else scheme["text"]
            d.text((x, y), word, font=cf, fill=col)
            x += d.textlength(word + " ", font=cf)
        y += line_h
    img.save(out)


def _icon_youtube(d, cx, cy, s):
    w, h = int(s * 1.02), int(s * 0.72)
    rounded(d, [cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2], h // 4, (255, 0, 0, 255))
    t = s * 0.22
    d.polygon([(cx - t * 0.5, cy - t), (cx - t * 0.5, cy + t), (cx + t, cy)], fill=WHITE)


def _icon_facebook(d, cx, cy, s):
    d.ellipse([cx - s // 2, cy - s // 2, cx + s // 2, cy + s // 2], fill=(24, 119, 242, 255))
    ff = font(int(s * 0.82), "Heavy")
    fw = d.textlength("f", font=ff)
    d.text((cx - fw / 2, cy - s * 0.44), "f", font=ff, fill=WHITE)


def _icon_linkedin(d, cx, cy, s):
    rounded(d, [cx - s // 2, cy - s // 2, cx + s // 2, cy + s // 2], s // 5, (10, 102, 194, 255))
    lf = font(int(s * 0.5), "Heavy")
    tw = d.textlength("in", font=lf)
    d.text((cx - tw / 2, cy - s * 0.30), "in", font=lf, fill=WHITE)


def _icon_tiktok(d, cx, cy, s):
    rounded(d, [cx - s // 2, cy - s // 2, cx + s // 2, cy + s // 2], s // 5, (0, 0, 0, 255))
    k = s
    # music note (white) with cyan/pink offset for the TikTok feel
    def note(col, dx, dy):
        d.ellipse([cx - 0.30 * k + dx, cy + 0.02 * k + dy, cx - 0.05 * k + dx, cy + 0.24 * k + dy], fill=col)
        d.rounded_rectangle([cx - 0.09 * k + dx, cy - 0.30 * k + dy, cx - 0.02 * k + dx, cy + 0.14 * k + dy], radius=6, fill=col)
        d.rounded_rectangle([cx - 0.09 * k + dx, cy - 0.30 * k + dy, cx + 0.18 * k + dx, cy - 0.19 * k + dy], radius=6, fill=col)
    note((37, 220, 226, 255), -6, -6)   # cyan
    note((238, 29, 82, 255), 6, 6)      # pink
    note(WHITE, 0, 0)                   # white on top


def make_outro(out, handle="@raowaqasakram", name="Rao Waqas Akram",
               title="Sr. Software Engineer | Mentor"):
    """Render the end-card: like/subscribe/follow + social logos + handle."""
    img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 10], fill=ACCENT)
    d.rectangle([0, H - 10, W, H], fill=(ACCENT[0], ACCENT[1], ACCENT[2], 120))

    # Heading
    hf = font(72, "Heavy")
    head = "LIKE  •  SUBSCRIBE"
    hw = d.textlength(head, font=hf)
    d.text(((W - hw) / 2, 560), head, font=hf, fill=WHITE)
    h2 = "& FOLLOW"
    h2w = d.textlength(h2, font=hf)
    d.text(((W - h2w) / 2, 650), h2, font=hf, fill=ACCENT)

    # Social icons row
    s = 150
    gap = 64
    n = 4
    total = n * s + (n - 1) * gap
    x0 = (W - total) // 2 + s // 2
    cy = 1000
    xs = [x0 + i * (s + gap) for i in range(n)]
    _icon_youtube(d, xs[0], cy, s)
    _icon_facebook(d, xs[1], cy, s)
    _icon_tiktok(d, xs[2], cy, s)
    _icon_linkedin(d, xs[3], cy, s)

    # Handle + name + title
    hnf = font(60, "Heavy")
    hw2 = d.textlength(handle, font=hnf)
    d.text(((W - hw2) / 2, 1200), handle, font=hnf, fill=ACCENT)
    nf = font(56, "Bold")
    nw = d.textlength(name, font=nf)
    d.text(((W - nw) / 2, 1290), name, font=nf, fill=WHITE)
    tf = font(38, "Semibold")
    tw = d.textlength(title, font=tf)
    d.text(((W - tw) / 2, 1370), title, font=tf, fill=(170, 180, 190, 255))
    img.save(out)
    print("outro ->", out)


if __name__ == "__main__":
    import sys
    d = sys.argv[1]
    if len(sys.argv) > 2 and sys.argv[2] == "caption":
        make_caption("Learn DOCKER before KUBERNETES", ["docker", "kubernetes"], f"{d}/caption_test.png")
        raise SystemExit

    make_question_card(
        "Sir app ka in future Pakistan mai koi tech company/startup start karna ka plan rakhta ha?",
        "@M.Danish-y8q", f"{d}/question_card.png")
    make_lower_third("Rao Waqas Akram", "Software Architect | AI Mentor", f"{d}/lower_third.png")
