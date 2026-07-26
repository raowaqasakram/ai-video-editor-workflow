"""Animated tech-keyword chips — on-screen graphics when a tech term is spoken.

When the speaker says a technology / career keyword ("LinkedIn", "GitHub",
"internship", "AI", "Docker"...) a small branded chip pops in on screen with a
vector icon + the word, holds, then fades out. Purely reusable: per video the
only input is a timeline (or "auto", derived from the captions).

Like every other text layer here, frames are baked with Pillow (this ffmpeg has
no drawtext/libass) and assembled into a **qtrle** alpha video via the concat
demuxer — the pop-in is a short PNG sequence, the hold is a single held frame,
so a 90s reel costs ~20 PNGs, not 2700.

    items = [(start_s, end_s, "LINKEDIN", "linkedin"), ...]   # body time
    layer(items, body_dur, workdir, "tech_layer.mov")
"""
import os
import subprocess

from PIL import Image, ImageDraw, ImageFilter

W, H = 1080, 1920
ACCENT = (0, 174, 239, 255)
WHITE = (255, 255, 255, 255)
CARD = (10, 14, 20, 235)

FPS = 30
POP_FRAMES = 9          # pop-in animation frames (~0.3s)
FADE_FRAMES = 6         # fade-out frames (~0.2s)

# chip anchor: right edge / top of the card, in the clear band above the video
ANCHOR_X = W - 62
ANCHOR_Y = 196

import sys  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import overlays as ro  # noqa: E402  (brand font loader)


# ---------------------------------------------------------------------------
# Icons — small vector glyphs drawn into a transparent SxS tile
# ---------------------------------------------------------------------------
def _ic_code(d, s):
    f = ro.font(int(s * 0.62), "Heavy")
    t = "</>"
    d.text(((s - d.textlength(t, font=f)) / 2, s * 0.16), t, font=f, fill=ACCENT)


def _ic_linkedin(d, s):
    d.rounded_rectangle([0, 0, s, s], radius=s // 5, fill=(10, 102, 194, 255))
    f = ro.font(int(s * 0.52), "Heavy")
    d.text(((s - d.textlength("in", font=f)) / 2, s * 0.20), "in", font=f, fill=WHITE)


def _ic_github(d, s):
    d.ellipse([0, 0, s, s], fill=(240, 246, 252, 255))
    c, r = s / 2, s * 0.30
    d.ellipse([c - r, c - r * 1.05, c + r, c + r * 0.95], fill=(20, 24, 30, 255))
    d.rounded_rectangle([c - r * 0.42, c + r * 0.30, c + r * 0.42, c + r * 1.15],
                        radius=int(s * 0.05), fill=(20, 24, 30, 255))
    for dx in (-r * 0.34, r * 0.10):
        d.ellipse([c + dx, c - r * 0.34, c + dx + r * 0.24, c - r * 0.10], fill=(240, 246, 252, 255))


def _ic_doc(d, s):
    d.rounded_rectangle([s * 0.16, s * 0.06, s * 0.84, s * 0.94], radius=int(s * 0.08),
                        fill=WHITE)
    for i, w in enumerate((0.5, 0.62, 0.44, 0.58)):
        y = s * (0.26 + i * 0.15)
        d.rounded_rectangle([s * 0.28, y, s * 0.28 + s * w * 0.72, y + s * 0.06],
                            radius=int(s * 0.03), fill=ACCENT if i == 0 else (120, 132, 146, 255))


def _ic_briefcase(d, s):
    d.rounded_rectangle([s * 0.36, s * 0.14, s * 0.64, s * 0.30], radius=int(s * 0.05),
                        outline=ACCENT, width=max(3, int(s * 0.07)))
    d.rounded_rectangle([s * 0.08, s * 0.28, s * 0.92, s * 0.86], radius=int(s * 0.10),
                        fill=ACCENT)
    d.rectangle([s * 0.08, s * 0.52, s * 0.92, s * 0.60], fill=(10, 14, 20, 255))


def _ic_ai(d, s):
    c = s / 2
    d.ellipse([c - s * 0.30, c - s * 0.30, c + s * 0.30, c + s * 0.30],
              outline=ACCENT, width=max(3, int(s * 0.07)))
    for a in (0, 1, 2):
        dx = (a - 1) * s * 0.22
        d.ellipse([c + dx - s * 0.06, c - s * 0.06, c + dx + s * 0.06, c + s * 0.06], fill=WHITE)
    d.line([c - s * 0.22, c, c + s * 0.22, c], fill=WHITE, width=max(2, int(s * 0.04)))


def _ic_skill(d, s):
    """Star / level-up badge."""
    import math
    c, R, r = s / 2, s * 0.42, s * 0.18
    pts = []
    for i in range(10):
        ang = math.pi / 2 + i * math.pi / 5
        rad = R if i % 2 == 0 else r
        pts.append((c + rad * math.cos(ang), c - rad * math.sin(ang)))
    d.polygon(pts, fill=ACCENT)


def _ic_rocket(d, s):
    c = s / 2
    d.polygon([(c, s * 0.06), (c + s * 0.22, s * 0.56), (c, s * 0.72), (c - s * 0.22, s * 0.56)],
              fill=WHITE)
    d.ellipse([c - s * 0.08, s * 0.26, c + s * 0.08, s * 0.42], fill=ACCENT)
    d.polygon([(c - s * 0.22, s * 0.50), (c - s * 0.38, s * 0.74), (c - s * 0.10, s * 0.66)], fill=ACCENT)
    d.polygon([(c + s * 0.22, s * 0.50), (c + s * 0.38, s * 0.74), (c + s * 0.10, s * 0.66)], fill=ACCENT)
    d.polygon([(c - s * 0.10, s * 0.72), (c, s * 0.96), (c + s * 0.10, s * 0.72)], fill=(255, 138, 0, 255))


def _ic_warning(d, s):
    d.polygon([(s / 2, s * 0.06), (s * 0.96, s * 0.90), (s * 0.04, s * 0.90)],
              fill=(255, 186, 8, 255))
    d.rounded_rectangle([s * 0.44, s * 0.34, s * 0.56, s * 0.66], radius=int(s * 0.05),
                        fill=(20, 16, 8, 255))
    d.ellipse([s * 0.44, s * 0.72, s * 0.56, s * 0.84], fill=(20, 16, 8, 255))


def _ic_java(d, s):
    d.ellipse([s * 0.12, s * 0.40, s * 0.72, s * 0.92], fill=WHITE)
    d.arc([s * 0.60, s * 0.46, s * 0.94, s * 0.76], -90, 90, fill=WHITE, width=max(3, int(s * 0.07)))
    for dx in (0.28, 0.44, 0.58):
        d.arc([s * dx - s * 0.06, s * 0.04, s * dx + s * 0.10, s * 0.38], 200, 340,
              fill=ACCENT, width=max(2, int(s * 0.05)))


def _ic_cloud(d, s):
    d.ellipse([s * 0.06, s * 0.42, s * 0.50, s * 0.80], fill=WHITE)
    d.ellipse([s * 0.30, s * 0.24, s * 0.78, s * 0.74], fill=WHITE)
    d.ellipse([s * 0.52, s * 0.44, s * 0.94, s * 0.80], fill=WHITE)
    d.rounded_rectangle([s * 0.12, s * 0.62, s * 0.88, s * 0.80], radius=int(s * 0.09), fill=WHITE)


def _ic_terminal(d, s):
    d.rounded_rectangle([s * 0.04, s * 0.12, s * 0.96, s * 0.88], radius=int(s * 0.10),
                        fill=(16, 20, 26, 255), outline=ACCENT, width=max(2, int(s * 0.05)))
    f = ro.font(int(s * 0.34), "Heavy")
    d.text((s * 0.18, s * 0.34), ">_", font=f, fill=(80, 250, 123, 255))


ICONS = {
    "code": _ic_code, "linkedin": _ic_linkedin, "github": _ic_github,
    "doc": _ic_doc, "resume": _ic_doc, "briefcase": _ic_briefcase, "job": _ic_briefcase,
    "ai": _ic_ai, "skill": _ic_skill, "star": _ic_skill, "rocket": _ic_rocket,
    "warning": _ic_warning, "java": _ic_java, "cloud": _ic_cloud, "terminal": _ic_terminal,
}

# keyword -> (display label, icon) used by auto()
KEYWORD_MAP = {
    "internship": ("INTERNSHIP", "briefcase"), "internships": ("INTERNSHIP", "briefcase"),
    "resume": ("RESUME", "doc"), "cv": ("CV", "doc"),
    "linkedin": ("LINKEDIN", "linkedin"), "github": ("GITHUB", "github"),
    "portfolio": ("PORTFOLIO", "doc"), "project": ("PROJECTS", "code"),
    "projects": ("PROJECTS", "code"), "skill": ("SKILLS", "skill"),
    "skills": ("SKILLS", "skill"), "experience": ("EXPERIENCE", "star"),
    "certificate": ("CERTIFICATE", "doc"), "certification": ("CERTIFICATION", "doc"),
    "offer": ("OFFER LETTER", "doc"), "resumes": ("RESUME", "doc"),
    "java": ("JAVA", "java"), "spring": ("SPRING BOOT", "java"),
    "docker": ("DOCKER", "cloud"), "kubernetes": ("KUBERNETES", "cloud"),
    "cloud": ("CLOUD", "cloud"), "aws": ("AWS", "cloud"), "azure": ("AZURE", "cloud"),
    "ai": ("AI", "ai"), "ml": ("MACHINE LEARNING", "ai"),
    "python": ("PYTHON", "code"), "javascript": ("JAVASCRIPT", "code"),
    "coding": ("CODING", "terminal"), "code": ("CODING", "terminal"),
    "developer": ("DEVELOPER", "code"), "development": ("DEVELOPMENT", "code"),
    "company": ("COMPANY", "briefcase"), "companies": ("COMPANIES", "briefcase"),
    "interview": ("INTERVIEW", "briefcase"), "career": ("CAREER", "rocket"),
    "scam": ("RED FLAG", "warning"), "fake": ("RED FLAG", "warning"),
    "paid": ("PAID", "warning"), "money": ("PAYMENT", "warning"),
}


def make_chip(label, icon="code", pad=34, icon_s=76):
    """Render one chip to its own RGBA image (tight bounds)."""
    f = ro.font(52, "Heavy")
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    tw = probe.textlength(label, font=f)
    w = int(pad + icon_s + 22 + tw + pad)
    h = 132
    img = Image.new("RGBA", (w + 40, h + 40), (0, 0, 0, 0))   # +40 room for the glow
    ox, oy = 20, 20

    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).rounded_rectangle([ox - 8, oy - 8, ox + w + 8, oy + h + 8],
                                           radius=38, fill=(ACCENT[0], ACCENT[1], ACCENT[2], 105))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(16)))

    d = ImageDraw.Draw(img)
    d.rounded_rectangle([ox, oy, ox + w, oy + h], radius=30, fill=CARD, outline=ACCENT, width=3)

    tile = Image.new("RGBA", (icon_s, icon_s), (0, 0, 0, 0))
    ICONS.get(icon, _ic_code)(ImageDraw.Draw(tile), icon_s)
    img.alpha_composite(tile, (ox + pad, oy + (h - icon_s) // 2))

    d.text((ox + pad + icon_s + 22, oy + (h - 62) // 2), label, font=f, fill=WHITE)
    return img


def _frame(chip, scale, alpha, out):
    """Composite `chip` onto a full 1080x1920 transparent canvas, scaled + faded."""
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cw, ch = chip.size
    sw, sh = max(1, int(cw * scale)), max(1, int(ch * scale))
    c = chip.resize((sw, sh), Image.LANCZOS)
    if alpha < 255:
        a = c.getchannel("A").point(lambda v: int(v * alpha / 255))
        c.putalpha(a)
    # anchor: right edge fixed, vertically centred on the chip's resting box
    x = ANCHOR_X - sw
    y = ANCHOR_Y + (int(ch) - sh) // 2
    canvas.alpha_composite(c, (x, y))
    canvas.save(out)


def auto(caps, max_chips=12, hold=2.6, gap=6.0):
    """Derive a chip timeline from captions: [[s,e,text,[hl]], ...] -> items.

    One chip per matched keyword, at most one at a time, and the same label is
    not repeated within `gap` seconds.
    """
    items, last_end, last_seen = [], 0.0, {}
    for cap in caps:
        s, e, text = float(cap[0]), float(cap[1]), cap[2]
        for word in text.split():
            key = word.strip(".,!?/()\"'").lower()
            if key not in KEYWORD_MAP:
                continue
            label, icon = KEYWORD_MAP[key]
            if s < last_end or s - last_seen.get(label, -99) < gap:
                continue
            end = min(s + hold, e + 0.6)
            if end - s < 0.9:
                continue
            items.append((round(s, 2), round(end, 2), label, icon))
            last_end, last_seen[label] = end, s
            break
        if len(items) >= max_chips:
            break
    return items


def layer(items, total_dur, work, out=None):
    """Build the qtrle alpha layer holding every chip over [0, total_dur]."""
    if not items:
        return None
    fdir = os.path.join(work, "tech")
    os.makedirs(fdir, exist_ok=True)
    out = out or os.path.join(work, "tech_layer.mov")
    blank = os.path.join(fdir, "blank.png")
    Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(blank)

    lines, t = [], 0.0

    def add(png, d):
        if d > 0.005:
            lines.append(f"file '{os.path.abspath(png)}'")
            lines.append(f"duration {d:.3f}")

    for i, (s, e, label, icon) in enumerate(sorted(items)):
        s, e = max(float(s), t), float(e)
        if e - s < 0.4:
            continue
        if s > t:
            add(blank, s - t)
        chip = make_chip(label, icon)
        step = 1.0 / FPS
        # pop-in: scale overshoot 0.55 -> 1.06 -> 1.0 with a fade
        for k in range(POP_FRAMES):
            p = (k + 1) / POP_FRAMES
            scale = 0.55 + 0.51 * p if p < 0.72 else 1.06 - 0.06 * ((p - 0.72) / 0.28)
            png = os.path.join(fdir, f"c{i:02d}_in{k:02d}.png")
            _frame(chip, scale, int(255 * min(1.0, p * 1.6)), png)
            add(png, step)
        hold_png = os.path.join(fdir, f"c{i:02d}_hold.png")
        _frame(chip, 1.0, 255, hold_png)
        hold_d = max(step, (e - s) - (POP_FRAMES + FADE_FRAMES) * step)
        add(hold_png, hold_d)
        for k in range(FADE_FRAMES):
            p = (k + 1) / FADE_FRAMES
            png = os.path.join(fdir, f"c{i:02d}_out{k:02d}.png")
            _frame(chip, 1.0 + 0.04 * p, int(255 * (1 - p)), png)
            add(png, step)
        t = s + POP_FRAMES * step + hold_d + FADE_FRAMES * step

    if t < total_dur:
        add(blank, total_dur - t)
    lines.append(f"file '{os.path.abspath(blank)}'")     # concat demuxer quirk
    listf = os.path.join(work, "tech_concat.txt")
    open(listf, "w").write("\n".join(lines))
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listf, "-r", str(FPS),
                    "-vf", "scale=1080:1920,format=rgba", "-c:v", "qtrle", out,
                    "-loglevel", "error"], check=True)
    print(f"tech-chip layer -> {out} ({len(items)} chips)")
    return out


if __name__ == "__main__":
    make_chip("LINKEDIN", "linkedin").save(sys.argv[1] if len(sys.argv) > 1 else "chip.png")
