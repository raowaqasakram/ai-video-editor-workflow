"""PIL overlay renderer for the AI Content Studio social treatment.

Produces transparent PNGs composited later with ffmpeg `overlay`:
  - question card (opaque 1080x1920 intro)
  - lower-third branding (transparent)
  - captions (transparent, keyword-highlighted)  [used in the caption pass]

Uses SF (San Francisco) for a premium/Apple feel, falling back to Arial.
"""
from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
ACCENT = (0, 174, 239, 255)      # #00AEEF
WHITE = (255, 255, 255, 255)
SECONDARY = (204, 204, 204, 255)
BG = (11, 15, 20, 255)           # near-black premium

SF = "/System/Library/Fonts/SFNS.ttf"
ARIAL_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
ARIAL = "/System/Library/Fonts/Supplemental/Arial.ttf"


def font(size, weight="Bold"):
    """Load SF at a given size/weight, falling back to Arial."""
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


def make_question_card(question, handle, out):
    """Render the premium 'Viewer Question' intro card."""
    img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img)
    # subtle accent glow bar at top and bottom
    d.rectangle([0, 0, W, 10], fill=ACCENT)
    d.rectangle([0, H - 10, W, H], fill=(ACCENT[0], ACCENT[1], ACCENT[2], 120))

    margin = 110
    # Label pill
    label = "VIEWER QUESTION"
    lf = font(40, "Heavy")
    lw = d.textlength(label, font=lf)
    pill = [margin, 470, margin + lw + 70, 470 + 78]
    rounded(d, pill, 39, ACCENT)
    d.text((margin + 35, 470 + 18), label, font=lf, fill=(8, 12, 16, 255))

    # Big quotation mark
    qf = font(220, "Heavy")
    d.text((margin - 12, 560), "“", font=qf, fill=(ACCENT[0], ACCENT[1], ACCENT[2], 90))

    # Question text
    tf = font(70, "Bold")
    lines = wrap(d, question, tf, W - 2 * margin)
    y = 720
    for ln in lines:
        d.text((margin, y), ln, font=tf, fill=WHITE)
        y += 92

    # Asker handle
    hf = font(42, "Semibold")
    d.text((margin, y + 40), handle, font=hf, fill=SECONDARY)

    # Footer brand
    ff = font(34, "Semibold")
    foot = "Rao Waqas Akram  •  Sr. Software Engineer | Mentor"
    fw = d.textlength(foot, font=ff)
    d.text(((W - fw) / 2, H - 150), foot, font=ff, fill=(150, 160, 170, 255))

    img.save(out)
    print("card ->", out, f"({len(lines)} lines)")


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


# Vertical centre of the caption block.
#
# CAPTION_CENTER_Y is the shipped value (locked recipe: captions in the lower
# band). CAPTION_CENTER_Y_SAFE lifts the block clear of the platform UI: TikTok,
# Reels and Shorts paint the username / description / audio row and the right
# action rail over roughly the bottom 420-480px of a 1080x1920 frame, and at the
# default the bar bottom sits only ~210px up, so part of it can be covered
# in-feed. Opt in per video with `"caption_safe": true` — check one export on a
# phone before switching it on everywhere, since it does move the framing.
CAPTION_CENTER_Y = 1600
CAPTION_CENTER_Y_SAFE = 1450


def make_caption(text, highlights, out, safe_zone=False):
    """Render a transparent lower-third caption PNG with keyword highlighting.

    Args:
        text: Caption text (Roman-Urdu + English).
        highlights: Iterable of lowercased keywords to colour in the accent.
        out: Output PNG path.
        safe_zone: Lift the block to CAPTION_CENTER_Y_SAFE so the platform UI
            cannot cover it.
    """
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cf = font(60, "Heavy")
    hl = {h.lower() for h in highlights}
    lines = wrap(d, text, cf, W - 200)[:2]  # max 2 lines

    line_h = 84
    pad_x, pad_y = 46, 34
    block_h = line_h * len(lines)
    center = CAPTION_CENTER_Y_SAFE if safe_zone else CAPTION_CENTER_Y
    top = center - block_h // 2
    widest = max(d.textlength(ln, font=cf) for ln in lines)
    bar = [(W - widest) / 2 - pad_x, top - pad_y,
           (W + widest) / 2 + pad_x, top + block_h + pad_y - 10]
    rounded(d, bar, 28, (10, 12, 16, 205))

    y = top
    for ln in lines:
        lw = d.textlength(ln, font=cf)
        x = (W - lw) / 2
        for word in ln.split():
            key = word.strip(".,!?/()").lower()
            col = ACCENT if key in hl else WHITE
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
