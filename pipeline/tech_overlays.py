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


# --- hiring-platform marks -------------------------------------------------
# Drawn approximations of the platforms he names on camera, in each brand's own
# colour, so a viewer recognises the site the moment he says it. They are
# glyphs, not trademark files — recognisable at 76px, which is the whole job.
def _wordmark(d, s, text, size=0.44, dy=0.24, fill=WHITE):
    f = ro.font(max(8, int(s * size)), "Heavy")
    d.text(((s - d.textlength(text, font=f)) / 2, s * dy), text, font=f, fill=fill)


def _ic_upwork(d, s):
    d.ellipse([0, 0, s, s], fill=(20, 168, 0, 255))          # Upwork green
    _wordmark(d, s, "up", 0.50, 0.20)


def _ic_fiverr(d, s):
    d.rounded_rectangle([0, 0, s, s], radius=s // 5, fill=(29, 191, 115, 255))
    _wordmark(d, s, "fi", 0.52, 0.18)


def _ic_indeed(d, s):
    # Indeed's mark is the dotted lowercase "i" on deep blue — deliberately NOT
    # the "in" square, which at chip size is indistinguishable from LinkedIn.
    d.ellipse([0, 0, s, s], fill=(0, 58, 155, 255))
    d.rounded_rectangle([s * 0.43, s * 0.40, s * 0.57, s * 0.78], radius=int(s * 0.07),
                        fill=WHITE)
    d.ellipse([s * 0.41, s * 0.20, s * 0.59, s * 0.36], fill=WHITE)


def _ic_glassdoor(d, s):
    d.ellipse([0, 0, s, s], fill=(12, 170, 65, 255))         # Glassdoor green
    d.rounded_rectangle([s * 0.30, s * 0.24, s * 0.70, s * 0.78], radius=int(s * 0.06),
                        outline=WHITE, width=max(3, int(s * 0.09)))
    d.ellipse([s * 0.56, s * 0.48, s * 0.66, s * 0.58], fill=WHITE)


def _ic_toptal(d, s):
    c = s / 2
    d.polygon([(c, s * 0.08), (s * 0.92, c), (c, s * 0.92), (s * 0.08, c)],
              fill=(32, 78, 207, 255))
    d.polygon([(c, s * 0.28), (s * 0.74, c), (c, s * 0.72), (s * 0.26, c)], fill=WHITE)


def _ic_freelancer(d, s):
    d.rounded_rectangle([0, 0, s, s], radius=s // 5, fill=(41, 178, 254, 255))
    d.polygon([(s * 0.24, s * 0.70), (s * 0.62, s * 0.20), (s * 0.72, s * 0.34),
               (s * 0.40, s * 0.78)], fill=WHITE)
    d.polygon([(s * 0.46, s * 0.78), (s * 0.78, s * 0.42), (s * 0.82, s * 0.72)],
              fill=(10, 14, 20, 255))


def _ic_remotebase(d, s):
    d.rounded_rectangle([0, 0, s, s], radius=s // 5, fill=(88, 62, 214, 255))
    _wordmark(d, s, "RB", 0.44, 0.22)


def _ic_turing(d, s):
    d.rounded_rectangle([0, 0, s, s], radius=s // 5, fill=(16, 18, 24, 255))
    _wordmark(d, s, "T", 0.62, 0.12)


def _ic_andela(d, s):
    d.ellipse([0, 0, s, s], fill=(0, 143, 138, 255))
    _wordmark(d, s, "A", 0.58, 0.14)


def _ic_globe(d, s):
    """Remote / worldwide."""
    c, r = s / 2, s * 0.42
    w = max(3, int(s * 0.06))
    d.ellipse([c - r, c - r, c + r, c + r], outline=ACCENT, width=w)
    d.line([c - r, c, c + r, c], fill=ACCENT, width=w)
    d.ellipse([c - r * 0.48, c - r, c + r * 0.48, c + r], outline=ACCENT, width=w)
    d.arc([c - r, c - r * 0.62, c + r, c + r * 0.30], 0, 180, fill=ACCENT, width=w)


def _ic_money(d, s):
    d.ellipse([0, 0, s, s], fill=(46, 190, 116, 255))
    _wordmark(d, s, "$", 0.62, 0.14)


def _ic_clock(d, s):
    c, r = s / 2, s * 0.42
    d.ellipse([c - r, c - r, c + r, c + r], fill=WHITE, outline=ACCENT,
              width=max(3, int(s * 0.06)))
    w = max(3, int(s * 0.06))
    d.line([c, c, c, c - r * 0.62], fill=(20, 24, 30, 255), width=w)
    d.line([c, c, c + r * 0.44, c], fill=ACCENT, width=w)


def _ic_search(d, s):
    c, r = s * 0.44, s * 0.30
    d.ellipse([c - r, c - r, c + r, c + r], outline=ACCENT, width=max(3, int(s * 0.08)))
    d.line([c + r * 0.72, c + r * 0.72, s * 0.90, s * 0.90], fill=ACCENT,
           width=max(4, int(s * 0.10)))


def _ic_pages(d, s):
    """Two stacked sheets — the page-count rule (one-page resume, two-page CV)."""
    d.rounded_rectangle([s * 0.06, s * 0.10, s * 0.66, s * 0.82], radius=int(s * 0.07),
                        fill=(150, 162, 176, 255))
    d.rounded_rectangle([s * 0.30, s * 0.22, s * 0.94, s * 0.94], radius=int(s * 0.07),
                        fill=WHITE)
    for i, w in enumerate((0.46, 0.34, 0.42)):
        y = s * (0.38 + i * 0.16)
        d.rounded_rectangle([s * 0.40, y, s * 0.40 + s * w, y + s * 0.06],
                            radius=int(s * 0.03),
                            fill=ACCENT if i == 0 else (120, 132, 146, 255))


def _ic_calendar(d, s):
    """Wall calendar — the semesters, the years, the days in a week."""
    d.rounded_rectangle([s * 0.08, s * 0.18, s * 0.92, s * 0.92], radius=int(s * 0.10),
                        fill=WHITE)
    d.rounded_rectangle([s * 0.08, s * 0.18, s * 0.92, s * 0.42], radius=int(s * 0.10),
                        fill=ACCENT)
    d.rectangle([s * 0.08, s * 0.34, s * 0.92, s * 0.42], fill=ACCENT)
    w = max(3, int(s * 0.07))
    for x in (s * 0.30, s * 0.70):                                    # binder rings
        d.line([x, s * 0.08, x, s * 0.26], fill=WHITE, width=w)
    for row in range(2):                                              # the day grid
        for col in range(3):
            x = s * (0.20 + col * 0.24)
            y = s * (0.52 + row * 0.20)
            d.rounded_rectangle([x, y, x + s * 0.14, y + s * 0.12], radius=int(s * 0.03),
                                fill=ACCENT if (row, col) == (0, 1) else
                                (150, 162, 176, 255))


def _ic_target(d, s):
    """Bullseye — focus: what you aim at instead of everything at once."""
    c = s / 2
    for r, fill in ((s * 0.44, ACCENT), (s * 0.30, WHITE), (s * 0.16, ACCENT)):
        d.ellipse([c - r, c - r, c + r, c + r], fill=fill)
    d.ellipse([c - s * 0.06, c - s * 0.06, c + s * 0.06, c + s * 0.06], fill=WHITE)


def _ic_degree(d, s):
    """Graduation cap — the university degree."""
    c = s / 2
    d.polygon([(c, s * 0.16), (s * 0.96, s * 0.40), (c, s * 0.64), (s * 0.04, s * 0.40)],
              fill=ACCENT)
    d.polygon([(s * 0.24, s * 0.50), (s * 0.76, s * 0.50), (s * 0.76, s * 0.74),
               (c, s * 0.86), (s * 0.24, s * 0.74)], fill=WHITE)
    w = max(3, int(s * 0.05))
    d.line([s * 0.90, s * 0.44, s * 0.90, s * 0.78], fill=WHITE, width=w)
    d.ellipse([s * 0.84, s * 0.76, s * 0.96, s * 0.88], fill=WHITE)


# --- contract / ethics marks -----------------------------------------------
# The vocabulary a "should I take this client directly?" answer needs: the paper
# you signed, the thing that protects the company, and the judgement call.
def _ic_contract(d, s):
    """A signed page — the contract / non-compete clause."""
    d.rounded_rectangle([s * 0.14, s * 0.06, s * 0.86, s * 0.94], radius=int(s * 0.08),
                        fill=WHITE)
    for i, w in enumerate((0.52, 0.44, 0.58)):
        y = s * (0.20 + i * 0.13)
        d.rounded_rectangle([s * 0.24, y, s * 0.24 + s * w, y + s * 0.05],
                            radius=int(s * 0.03), fill=(120, 132, 146, 255))
    # the signature scrawl on the dotted line
    d.line([s * 0.24, s * 0.78, s * 0.76, s * 0.78], fill=(150, 162, 176, 255),
           width=max(2, int(s * 0.03)))
    w = max(3, int(s * 0.06))
    d.line([s * 0.28, s * 0.72, s * 0.40, s * 0.62], fill=ACCENT, width=w)
    d.line([s * 0.40, s * 0.62, s * 0.48, s * 0.74], fill=ACCENT, width=w)
    d.line([s * 0.48, s * 0.74, s * 0.62, s * 0.58], fill=ACCENT, width=w)


def _ic_shield(d, s):
    """Protection — NDA, confidentiality, covering yourself."""
    c = s / 2
    d.polygon([(c, s * 0.06), (s * 0.90, s * 0.24), (s * 0.90, s * 0.56),
               (c, s * 0.94), (s * 0.10, s * 0.56), (s * 0.10, s * 0.24)],
              fill=ACCENT)
    w = max(4, int(s * 0.09))
    d.line([s * 0.32, s * 0.48, s * 0.45, s * 0.62], fill=WHITE, width=w)
    d.line([s * 0.45, s * 0.62, s * 0.70, s * 0.34], fill=WHITE, width=w)


def _ic_scale(d, s):
    """Balance scale — the ethical / legal judgement call."""
    c = s / 2
    w = max(3, int(s * 0.05))
    d.line([c, s * 0.14, c, s * 0.78], fill=WHITE, width=w)          # post
    d.line([s * 0.14, s * 0.30, s * 0.86, s * 0.30], fill=WHITE, width=w)  # beam
    d.polygon([(s * 0.30, s * 0.82), (s * 0.70, s * 0.82), (c, s * 0.70)], fill=WHITE)
    d.ellipse([c - s * 0.06, s * 0.10, c + s * 0.06, s * 0.22], fill=ACCENT)
    for x in (s * 0.14, s * 0.86):                                    # the two pans
        d.polygon([(x - s * 0.13, s * 0.42), (x + s * 0.13, s * 0.42), (x, s * 0.60)],
                  fill=ACCENT)
        d.line([x, s * 0.30, x, s * 0.42], fill=WHITE, width=max(2, int(s * 0.03)))


def _ic_people(d, s):
    """Two figures — the client relationship, you and them.

    A literal handshake was drawn first and abandoned: interlocking hands at
    76px collapse into an unreadable smear. Two head-and-shoulder silhouettes
    say "the other party" instantly at any size.
    """
    for cx, fill in ((0.34, WHITE), (0.68, ACCENT)):
        d.ellipse([s * (cx - 0.17), s * 0.14, s * (cx + 0.17), s * 0.48], fill=fill)
        d.pieslice([s * (cx - 0.30), s * 0.52, s * (cx + 0.30), s * 1.12], 180, 360,
                   fill=fill)
    # a thin gap so the two bodies stay separate figures, not one mass
    d.line([s * 0.52, s * 0.50, s * 0.52, s * 0.96], fill=(10, 14, 20, 255),
           width=max(2, int(s * 0.05)))


def _ic_mail(d, s):
    d.rounded_rectangle([s * 0.06, s * 0.22, s * 0.94, s * 0.78], radius=int(s * 0.08),
                        fill=WHITE)
    d.line([s * 0.10, s * 0.26, s / 2, s * 0.56], fill=ACCENT, width=max(3, int(s * 0.07)))
    d.line([s * 0.90, s * 0.26, s / 2, s * 0.56], fill=ACCENT, width=max(3, int(s * 0.07)))


# --- country flags --------------------------------------------------------
# A "which country?" answer is mostly place names, and a name is the one thing a
# scroller will not stop for. These are simplified flags, not exact ones: at 76px
# the field colours and one distinguishing mark are all that survives, and that is
# enough to be recognised. Each is built as a rounded badge — the base is drawn
# rounded, bands overlap so only the OUTER corners keep the radius.
FLAG_R = 0.14          # corner radius as a fraction of the tile


def _flag_base(d, s, colour):
    d.rounded_rectangle([s * 0.04, s * 0.16, s * 0.96, s * 0.84],
                        radius=int(s * FLAG_R), fill=colour)


def _ic_flag_ksa(d, s):
    """Saudi Arabia — green field, shahada bar and sword in white."""
    _flag_base(d, s, (0, 108, 53, 255))
    # Two white bars, not a literal sword: at 76px a bladed shape reads as an
    # ARROW, which is worse than no sword at all. Green field + white script is
    # what actually identifies this flag at chip size.
    d.rounded_rectangle([s * 0.14, s * 0.30, s * 0.86, s * 0.43], radius=int(s * 0.06),
                        fill=WHITE)                                        # shahada line
    d.rounded_rectangle([s * 0.22, s * 0.56, s * 0.78, s * 0.65], radius=int(s * 0.045),
                        fill=WHITE)                                        # sword bar
    d.rounded_rectangle([s * 0.74, s * 0.51, s * 0.80, s * 0.70], radius=int(s * 0.03),
                        fill=WHITE)                                        # hilt


def _ic_flag_uae(d, s):
    """UAE — red hoist bar, then green / white / black bands."""
    _flag_base(d, s, WHITE)
    d.rounded_rectangle([s * 0.04, s * 0.16, s * 0.40, s * 0.39], radius=int(s * FLAG_R),
                        fill=(0, 115, 47, 255))
    d.rounded_rectangle([s * 0.04, s * 0.61, s * 0.96, s * 0.84], radius=int(s * FLAG_R),
                        fill=(0, 0, 0, 255))
    d.rectangle([s * 0.40, s * 0.16, s * 0.96, s * 0.39], fill=(0, 115, 47, 255))
    d.rectangle([s * 0.04, s * 0.61, s * 0.40, s * 0.72], fill=(0, 0, 0, 255))
    d.rounded_rectangle([s * 0.04, s * 0.16, s * 0.28, s * 0.84], radius=int(s * FLAG_R),
                        fill=(255, 0, 0, 255))
    d.rectangle([s * 0.18, s * 0.16, s * 0.28, s * 0.84], fill=(255, 0, 0, 255))


def _ic_flag_bahrain(d, s):
    """Bahrain — white hoist, red field, five-point serration between them."""
    _flag_base(d, s, WHITE)
    d.rounded_rectangle([s * 0.40, s * 0.16, s * 0.96, s * 0.84], radius=int(s * FLAG_R),
                        fill=(206, 17, 38, 255))
    d.rectangle([s * 0.40, s * 0.16, s * 0.56, s * 0.84], fill=(206, 17, 38, 255))
    for k in range(5):                                                     # the serration
        y0 = s * (0.16 + k * 0.136)
        d.polygon([(s * 0.40, y0), (s * 0.40, y0 + s * 0.136), (s * 0.26, y0 + s * 0.068)],
                  fill=(206, 17, 38, 255))


def _ic_flag_pakistan(d, s):
    """Pakistan — white hoist bar, green field, crescent and star."""
    _flag_base(d, s, (1, 65, 28, 255))
    d.rounded_rectangle([s * 0.04, s * 0.16, s * 0.26, s * 0.84], radius=int(s * FLAG_R),
                        fill=WHITE)
    d.rectangle([s * 0.18, s * 0.16, s * 0.26, s * 0.84], fill=WHITE)
    d.ellipse([s * 0.42, s * 0.32, s * 0.78, s * 0.68], fill=WHITE)
    d.ellipse([s * 0.52, s * 0.30, s * 0.86, s * 0.64], fill=(1, 65, 28, 255))
    d.polygon([(s * 0.74, s * 0.34), (s * 0.78, s * 0.44), (s * 0.68, s * 0.40)],
              fill=WHITE)


def _ic_flag_eu(d, s):
    """Europe — blue field, ring of stars (drawn as dots; points do not survive)."""
    import math
    _flag_base(d, s, (0, 51, 153, 255))
    c, r, dot = s / 2, s * 0.235, s * 0.048
    for k in range(12):
        a = math.radians(k * 30 - 90)
        x, y = c + r * math.cos(a), c + r * math.sin(a)
        d.ellipse([x - dot, y - dot, x + dot, y + dot], fill=(255, 204, 0, 255))


# --- moving abroad ---------------------------------------------------------
# The vocabulary of a "should I go work there?" answer: the document, the stamp,
# the flight, the skyline you are picturing — and the two ways it goes wrong.


def _ic_passport(d, s):
    d.rounded_rectangle([s * 0.16, s * 0.08, s * 0.84, s * 0.92], radius=int(s * 0.10),
                        fill=(24, 62, 46, 255), outline=ACCENT, width=max(2, int(s * 0.04)))
    c, r = s / 2, s * 0.17
    d.ellipse([c - r, s * 0.28, c + r, s * 0.28 + 2 * r], outline=WHITE,
              width=max(2, int(s * 0.04)))
    d.line([c, s * 0.28, c, s * 0.28 + 2 * r], fill=WHITE, width=max(2, int(s * 0.03)))
    d.line([c - r, c - s * 0.05, c + r, c - s * 0.05], fill=WHITE, width=max(2, int(s * 0.03)))
    d.rounded_rectangle([s * 0.30, s * 0.74, s * 0.70, s * 0.80], radius=int(s * 0.03),
                        fill=WHITE)


def _ic_visa_stamp(d, s):
    """An inked entry stamp — the thing an azad visa is not."""
    c = s / 2
    d.rounded_rectangle([s * 0.06, s * 0.20, s * 0.94, s * 0.80], radius=int(s * 0.08),
                        fill=WHITE)
    r = s * 0.26
    d.ellipse([c - r, c - r, c + r, c + r], outline=ACCENT, width=max(3, int(s * 0.06)))
    d.line([c - s * 0.14, c, c - s * 0.03, c + s * 0.11], fill=ACCENT,
           width=max(3, int(s * 0.06)))
    d.line([c - s * 0.03, c + s * 0.11, c + s * 0.15, c - s * 0.11], fill=ACCENT,
           width=max(3, int(s * 0.06)))


def _ic_plane(d, s):
    d.polygon([(s * 0.10, s * 0.54), (s * 0.90, s * 0.34), (s * 0.90, s * 0.50),
               (s * 0.34, s * 0.66)], fill=WHITE)
    d.polygon([(s * 0.62, s * 0.42), (s * 0.78, s * 0.10), (s * 0.88, s * 0.14),
               (s * 0.80, s * 0.44)], fill=ACCENT)
    d.polygon([(s * 0.18, s * 0.58), (s * 0.34, s * 0.62), (s * 0.24, s * 0.80),
               (s * 0.14, s * 0.76)], fill=ACCENT)


def _ic_jail(d, s):
    """Barred window — 'you can get a wonderful jail'."""
    d.rounded_rectangle([s * 0.10, s * 0.12, s * 0.90, s * 0.88], radius=int(s * 0.10),
                        fill=(28, 34, 44, 255), outline=WHITE, width=max(3, int(s * 0.06)))
    w = max(3, int(s * 0.07))
    for x in (0.32, 0.50, 0.68):
        d.line([s * x, s * 0.18, s * x, s * 0.82], fill=WHITE, width=w)
    d.line([s * 0.14, s * 0.50, s * 0.86, s * 0.50], fill=ACCENT, width=w)


def _ic_city(d, s):
    """Skyline — the country you are picturing."""
    d.rectangle([s * 0.08, s * 0.52, s * 0.30, s * 0.90], fill=(150, 162, 176, 255))
    d.rectangle([s * 0.34, s * 0.30, s * 0.56, s * 0.90], fill=WHITE)
    d.rectangle([s * 0.60, s * 0.44, s * 0.80, s * 0.90], fill=(150, 162, 176, 255))
    d.polygon([(s * 0.84, s * 0.90), (s * 0.90, s * 0.34), (s * 0.96, s * 0.90)],
              fill=ACCENT)                                                 # the tower
    for row in range(3):
        for x in (0.39, 0.47):
            y = s * (0.40 + row * 0.14)
            d.rectangle([s * x, y, s * x + s * 0.05, y + s * 0.07], fill=ACCENT)


def _ic_percent(d, s):
    """A quota — Saudization is expressed in percentages."""
    w = max(4, int(s * 0.09))
    d.line([s * 0.24, s * 0.80, s * 0.76, s * 0.20], fill=ACCENT, width=w)
    r = s * 0.15
    d.ellipse([s * 0.12, s * 0.14, s * 0.12 + 2 * r, s * 0.14 + 2 * r], outline=WHITE,
              width=w)
    d.ellipse([s * 0.58, s * 0.56, s * 0.58 + 2 * r, s * 0.56 + 2 * r], outline=WHITE,
              width=w)


def _ic_ban(d, s):
    """Do-not — the 'kindly don't target Saudi Arabia' beat."""
    c, r = s / 2, s * 0.40
    w = max(5, int(s * 0.12))
    d.ellipse([c - r, c - r, c + r, c + r], outline=(232, 62, 62, 255), width=w)
    off = r * 0.62
    d.line([c - off, c - off, c + off, c + off], fill=(232, 62, 62, 255), width=w)


# --- voice / speech-to-text -----------------------------------------------
# The vocabulary a "I dictate my instructions instead of typing them" answer
# needs: the microphone, the keyboard it replaces, and the tools he names.
def _ic_mic(d, s):
    """Microphone — the input he switched to."""
    d.rounded_rectangle([s * 0.34, s * 0.08, s * 0.66, s * 0.56], radius=int(s * 0.16),
                        fill=ACCENT)
    w = max(4, int(s * 0.08))
    d.arc([s * 0.18, s * 0.28, s * 0.82, s * 0.76], 0, 180, fill=WHITE, width=w)
    d.line([s / 2, s * 0.74, s / 2, s * 0.88], fill=WHITE, width=w)
    d.line([s * 0.30, s * 0.90, s * 0.70, s * 0.90], fill=WHITE, width=w)


def _ic_keyboard(d, s):
    """Keyboard — what typing the same instructions out costs him."""
    d.rounded_rectangle([s * 0.04, s * 0.24, s * 0.96, s * 0.80], radius=int(s * 0.10),
                        fill=WHITE)
    k, gap = s * 0.13, s * 0.05
    for row in range(2):
        for col in range(5):
            x = s * 0.11 + col * (k + gap)
            y = s * 0.33 + row * (k + gap * 0.6)
            d.rounded_rectangle([x, y, x + k, y + k * 0.8], radius=int(s * 0.03),
                                fill=(150, 162, 176, 255))
    d.rounded_rectangle([s * 0.24, s * 0.64, s * 0.76, s * 0.73], radius=int(s * 0.03),
                        fill=ACCENT)                                   # space bar


def _ic_wave(d, s):
    """Audio waveform — speech becoming text."""
    bars = (0.26, 0.50, 0.74, 0.54, 0.82, 0.38, 0.22)
    w = s * 0.09
    for i, h in enumerate(bars):
        x = s * 0.06 + i * (w + s * 0.045)
        d.rounded_rectangle([x, s / 2 - s * h / 2, x + w, s / 2 + s * h / 2],
                            radius=int(w / 2), fill=ACCENT if i % 2 else WHITE)


def _ic_claude(d, s):
    """Claude — the burnt-orange starburst on its cream tile."""
    import math
    d.rounded_rectangle([0, 0, s, s], radius=s // 5, fill=(240, 238, 230, 255))
    c, r0, r1 = s / 2, s * 0.07, s * 0.36
    w = max(3, int(s * 0.075))
    for i in range(8):                       # eight radiating spokes
        a = i * math.pi / 4 + math.pi / 8
        d.line([c + r0 * math.cos(a), c + r0 * math.sin(a),
                c + r1 * math.cos(a), c + r1 * math.sin(a)],
               fill=(217, 119, 87, 255), width=w)


def _ic_openai(d, s):
    """ChatGPT — the white knot on the OpenAI green. Two hexagon outlines offset
    by half a segment are not the real geometry, but the six-fold star they make
    reads as the mark at 76px, which is the whole job (same rule as the
    hiring-platform glyphs). The offset must be pi/6, not pi/3 — a hexagon has
    60-degree symmetry, so a pi/3 turn draws the same six points twice."""
    import math
    d.ellipse([0, 0, s, s], fill=(16, 163, 127, 255))     # OpenAI green
    c, r = s / 2, s * 0.32
    w = max(3, int(s * 0.055))
    for turn in (0.0, math.pi / 6):
        pts = [(c + r * math.cos(turn + i * math.pi / 3),
                c + r * math.sin(turn + i * math.pi / 3)) for i in range(6)]
        d.polygon(pts, outline=WHITE, width=w)


def _ic_fluidvoice(d, s):
    """Fluid Voice — the dark app tile with its glossy F."""
    d.rounded_rectangle([0, 0, s, s], radius=s // 4, fill=(18, 24, 38, 255))
    d.rounded_rectangle([s * 0.06, s * 0.06, s * 0.94, s * 0.50], radius=int(s * 0.18),
                        fill=(30, 40, 62, 255))                        # top gloss
    f = ro.font(int(s * 0.62), "Heavy")
    d.text(((s - d.textlength("F", font=f)) / 2, s * 0.14), "F", font=f,
           fill=(120, 170, 255, 255))


def _ic_flow(d, s):
    """Whisper Flow — the bar-chart wordmark on its pale card."""
    d.rounded_rectangle([0, 0, s, s], radius=s // 5, fill=(250, 250, 240, 255))
    bars = (0.34, 0.62, 0.44, 0.70)
    w = s * 0.11
    for i, h in enumerate(bars):
        x = s * 0.18 + i * (w + s * 0.06)
        d.rounded_rectangle([x, s * 0.72 - s * h, x + w, s * 0.72], radius=int(w / 2),
                            fill=(40, 44, 52, 255))


def _ic_download(d, s):
    """Download — the one-time package the free tool pulls down."""
    w = max(4, int(s * 0.10))
    d.line([s / 2, s * 0.10, s / 2, s * 0.58], fill=ACCENT, width=w)
    d.polygon([(s * 0.28, s * 0.48), (s * 0.72, s * 0.48), (s / 2, s * 0.76)], fill=ACCENT)
    d.line([s * 0.14, s * 0.86, s * 0.86, s * 0.86], fill=WHITE, width=w)


# --- requirements / product design ----------------------------------------
# The vocabulary a "do you use an LLM to write requirements?" answer needs: the
# thing being specified (an interface, a phone screen), the stack it is built
# in, and the parts he says to keep away from the LLM (architecture, system
# design).
def _ic_ui(d, s):
    """App window — the user interface he is describing."""
    d.rounded_rectangle([s * 0.04, s * 0.14, s * 0.96, s * 0.86], radius=int(s * 0.10),
                        fill=WHITE)
    d.rounded_rectangle([s * 0.04, s * 0.14, s * 0.96, s * 0.34], radius=int(s * 0.10),
                        fill=(150, 162, 176, 255))
    d.rectangle([s * 0.04, s * 0.28, s * 0.96, s * 0.34], fill=(150, 162, 176, 255))
    r = s * 0.035
    for i, cx in enumerate((0.16, 0.27, 0.38)):
        d.ellipse([s * cx - r, s * 0.24 - r, s * cx + r, s * 0.24 + r], fill=WHITE)
    d.rounded_rectangle([s * 0.12, s * 0.44, s * 0.36, s * 0.78], radius=int(s * 0.04),
                        fill=ACCENT)                                   # sidebar
    for y in (0.46, 0.58, 0.70):
        d.rounded_rectangle([s * 0.44, s * y, s * 0.88, s * y + s * 0.07],
                            radius=int(s * 0.03), fill=(150, 162, 176, 255))


def _ic_typescript(d, s):
    """TypeScript — the blue tile with its wordmark initials."""
    d.rounded_rectangle([0, 0, s, s], radius=s // 6, fill=(49, 120, 198, 255))
    f = ro.font(int(s * 0.46), "Heavy")
    d.text(((s - d.textlength("TS", font=f)) / 2, s * 0.26), "TS", font=f, fill=WHITE)


def _ic_phone(d, s):
    """Phone — the iPhone screen effect he cannot describe in words."""
    d.rounded_rectangle([s * 0.24, s * 0.04, s * 0.76, s * 0.96], radius=int(s * 0.14),
                        fill=WHITE)
    d.rounded_rectangle([s * 0.30, s * 0.14, s * 0.70, s * 0.82], radius=int(s * 0.06),
                        fill=ACCENT)
    d.rounded_rectangle([s * 0.42, s * 0.07, s * 0.58, s * 0.11], radius=int(s * 0.02),
                        fill=(150, 162, 176, 255))                     # notch
    r = s * 0.05
    d.ellipse([s / 2 - r, s * 0.86 - r, s / 2 + r, s * 0.86 + r],
              outline=(150, 162, 176, 255), width=max(3, int(s * 0.045)))


def _ic_architecture(d, s):
    """Stacked layers — the architecture / system design he owns himself."""
    for y, col in ((0.14, WHITE), (0.42, ACCENT), (0.70, WHITE)):
        d.polygon([(s / 2, s * y), (s * 0.94, s * y + s * 0.10),
                   (s / 2, s * y + s * 0.20), (s * 0.06, s * y + s * 0.10)], fill=col)


def _ic_bulb(d, s):
    """Light bulb — brainstorming, the word in the question itself."""
    c, r = s / 2, s * 0.30
    d.ellipse([c - r, s * 0.08, c + r, s * 0.08 + 2 * r], fill=ACCENT)
    d.rounded_rectangle([c - r * 0.46, s * 0.62, c + r * 0.46, s * 0.74],
                        radius=int(s * 0.03), fill=WHITE)
    d.rounded_rectangle([c - r * 0.38, s * 0.78, c + r * 0.38, s * 0.90],
                        radius=int(s * 0.03), fill=WHITE)


def _ic_slides(d, s):
    """Presentation — the deck an LLM builds once the requirements exist."""
    d.rounded_rectangle([s * 0.06, s * 0.12, s * 0.94, s * 0.68], radius=int(s * 0.08),
                        fill=WHITE)
    bars = (0.26, 0.44, 0.34)
    w = s * 0.14
    for i, h in enumerate(bars):
        x = s * 0.20 + i * (w + s * 0.09)
        d.rounded_rectangle([x, s * 0.58 - s * h, x + w, s * 0.58], radius=int(s * 0.02),
                            fill=ACCENT)
    wl = max(4, int(s * 0.07))
    d.line([s / 2, s * 0.68, s / 2, s * 0.82], fill=WHITE, width=wl)
    d.line([s * 0.28, s * 0.92, s * 0.72, s * 0.92], fill=WHITE, width=wl)


def _ic_sliders(d, s):
    """Sliders — customization, what every business now wants."""
    w = max(4, int(s * 0.08))
    for y, kx in ((0.24, 0.68), (0.50, 0.34), (0.76, 0.58)):
        d.line([s * 0.10, s * y, s * 0.90, s * y], fill=WHITE, width=w)
        r = s * 0.11
        d.ellipse([s * kx - r, s * y - r, s * kx + r, s * y + r], fill=ACCENT)


def _ic_tenable(d, s):
    """Tenable — the vendor he uses as the 'hundreds of customers' example.
    Their teal tile with the wordmark initial; a recognisable chip, not a
    trademark file (same rule as the hiring-platform glyphs)."""
    d.rounded_rectangle([0, 0, s, s], radius=s // 5, fill=(0, 204, 188, 255))
    f = ro.font(int(s * 0.62), "Heavy")
    d.text(((s - d.textlength("t", font=f)) / 2, s * 0.12), "t", font=f,
           fill=(11, 43, 51, 255))


def _ic_database(d, s):
    """Stacked cylinder — the database, the ERD, the table structure."""
    top, bot, eh = s * 0.14, s * 0.86, s * 0.16
    d.ellipse([s * 0.10, top, s * 0.90, top + eh], fill=ACCENT)
    d.rectangle([s * 0.10, top + eh / 2, s * 0.90, bot - eh / 2], fill=ACCENT)
    d.ellipse([s * 0.10, bot - eh, s * 0.90, bot], fill=ACCENT)
    # Two white bands read as the platter seams. Drawn as full ellipses so the
    # curve matches the cylinder's own; a straight line reads as a crack.
    for y in (top + s * 0.22, top + s * 0.44):
        d.ellipse([s * 0.10, y, s * 0.90, y + eh], outline=WHITE,
                  width=max(3, int(s * 0.05)))


def _ic_api(d, s):
    """`{ }` — the API contract. Set in the brand font like `_ic_code`'s `</>`
    rather than stroked by hand: hand-drawn braces need mitred joins to read as
    braces, and without them they came out as two lightning bolts."""
    f = ro.font(int(s * 0.78), "Heavy")
    for glyph, x in (("{", s * 0.10), ("}", s * 0.58)):
        d.text((x, s * 0.02), glyph, font=f, fill=WHITE)
    r = s * 0.10
    d.ellipse([s / 2 - r, s / 2 - r, s / 2 + r, s / 2 + r], fill=ACCENT)


def _ic_flowchart(d, s):
    """Three boxes and the arrows between them — the diagram he asks the LLM
    for. The boxes are deliberately different sizes; a uniform stack reads as a
    list, not a flow."""
    w = max(3, int(s * 0.05))
    d.rounded_rectangle([s * 0.30, s * 0.06, s * 0.70, s * 0.28],
                        radius=int(s * 0.06), fill=ACCENT)
    d.rounded_rectangle([s * 0.06, s * 0.62, s * 0.42, s * 0.92],
                        radius=int(s * 0.06), fill=WHITE)
    d.rounded_rectangle([s * 0.58, s * 0.62, s * 0.94, s * 0.92],
                        radius=int(s * 0.06), fill=WHITE)
    d.line([s * 0.50, s * 0.28, s * 0.50, s * 0.45], fill=WHITE, width=w)
    d.line([s * 0.24, s * 0.45, s * 0.76, s * 0.45], fill=WHITE, width=w)
    for x in (0.24, 0.76):
        d.line([s * x, s * 0.45, s * x, s * 0.62], fill=WHITE, width=w)
        d.polygon([(s * x - s * 0.06, s * 0.56), (s * x + s * 0.06, s * 0.56),
                   (s * x, s * 0.66)], fill=ACCENT)


def _ic_key(d, s):
    """Key — credentials, secrets, the thing that differs per environment."""
    r = s * 0.20
    cx, cy = s * 0.30, s * 0.32
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=ACCENT,
              width=max(4, int(s * 0.11)))
    w = max(4, int(s * 0.09))
    d.line([cx + r * 0.6, cy + r * 0.6, s * 0.86, s * 0.88], fill=WHITE, width=w)
    d.line([s * 0.62, s * 0.64, s * 0.74, s * 0.52], fill=WHITE, width=w)
    d.line([s * 0.72, s * 0.74, s * 0.84, s * 0.62], fill=WHITE, width=w)


ICONS = {
    "code": _ic_code, "linkedin": _ic_linkedin, "github": _ic_github,
    "doc": _ic_doc, "resume": _ic_doc, "briefcase": _ic_briefcase, "job": _ic_briefcase,
    "ai": _ic_ai, "skill": _ic_skill, "star": _ic_skill, "rocket": _ic_rocket,
    "warning": _ic_warning, "java": _ic_java, "cloud": _ic_cloud, "terminal": _ic_terminal,
    # hiring platforms + freelancing
    "upwork": _ic_upwork, "fiverr": _ic_fiverr, "indeed": _ic_indeed,
    "glassdoor": _ic_glassdoor, "toptal": _ic_toptal, "freelancer": _ic_freelancer,
    "turing": _ic_turing, "andela": _ic_andela, "remotebase": _ic_remotebase,
    "globe": _ic_globe, "remote": _ic_globe, "money": _ic_money, "clock": _ic_clock,
    "search": _ic_search, "mail": _ic_mail,
    # documentation anatomy (1 Aug Q1): what the .md files actually describe
    "database": _ic_database, "db": _ic_database, "erd": _ic_database,
    "api": _ic_api, "apis": _ic_api,
    "flowchart": _ic_flowchart, "diagram": _ic_flowchart,
    "key": _ic_key, "credentials": _ic_key, "secrets": _ic_key,
    # CV / resume anatomy
    "pages": _ic_pages, "degree": _ic_degree, "university": _ic_degree,
    # study life: the calendar you actually have, and the thing to aim at
    "calendar": _ic_calendar, "semester": _ic_calendar, "schedule": _ic_calendar,
    "target": _ic_target, "focus": _ic_target,
    # flags + moving abroad
    "ksa": _ic_flag_ksa, "saudi": _ic_flag_ksa, "uae": _ic_flag_uae,
    "bahrain": _ic_flag_bahrain, "pakistan": _ic_flag_pakistan,
    "eu": _ic_flag_eu, "europe": _ic_flag_eu,
    "passport": _ic_passport, "visa": _ic_visa_stamp, "stamp": _ic_visa_stamp,
    "plane": _ic_plane, "travel": _ic_plane, "jail": _ic_jail, "city": _ic_city,
    "percent": _ic_percent, "quota": _ic_percent, "ban": _ic_ban, "dont": _ic_ban,
    # contract / ethics
    "contract": _ic_contract, "clause": _ic_contract, "nda": _ic_shield,
    "shield": _ic_shield, "scale": _ic_scale, "ethics": _ic_scale,
    "people": _ic_people, "handshake": _ic_people, "trust": _ic_people,
    "client": _ic_people,
    # voice / speech-to-text
    "mic": _ic_mic, "voice": _ic_mic, "keyboard": _ic_keyboard, "typing": _ic_keyboard,
    "wave": _ic_wave, "speech": _ic_wave, "claude": _ic_claude,
    "openai": _ic_openai, "chatgpt": _ic_openai, "gpt": _ic_openai,
    "fluidvoice": _ic_fluidvoice, "fluid": _ic_fluidvoice,
    "flow": _ic_flow, "whisperflow": _ic_flow, "whisper": _ic_flow,
    "download": _ic_download,
    # requirements / product design
    "ui": _ic_ui, "interface": _ic_ui, "frontend": _ic_ui, "design": _ic_ui,
    "typescript": _ic_typescript, "ts": _ic_typescript,
    "phone": _ic_phone, "iphone": _ic_phone, "mobile": _ic_phone,
    "architecture": _ic_architecture, "systemdesign": _ic_architecture,
    "layers": _ic_architecture,
    "bulb": _ic_bulb, "idea": _ic_bulb, "brainstorm": _ic_bulb,
    "slides": _ic_slides, "presentation": _ic_slides,
    "sliders": _ic_sliders, "custom": _ic_sliders, "customization": _ic_sliders,
    "tenable": _ic_tenable,
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
    "upwork": ("UPWORK", "upwork"), "fiverr": ("FIVERR", "fiverr"),
    "indeed": ("INDEED", "indeed"), "glassdoor": ("GLASSDOOR", "glassdoor"),
    "toptal": ("TOPTAL", "toptal"), "freelancer": ("FREELANCER", "freelancer"),
    "turing": ("TURING", "turing"), "andela": ("ANDELA", "andela"),
    "remotebase": ("REMOTEBASE", "remotebase"),
    "freelancing": ("FREELANCING", "freelancer"), "remote": ("REMOTE JOBS", "globe"),
    "upwork.com": ("UPWORK", "upwork"), "profile": ("PROFILE", "search"),
    "proposal": ("PROPOSAL", "mail"), "proposals": ("PROPOSALS", "mail"),
    "rate": ("HOURLY RATE", "money"), "dollar": ("DOLLARS", "money"),
    "dollars": ("DOLLARS", "money"), "timezone": ("TIME ZONE", "clock"),
    "degree": ("DEGREE", "degree"), "university": ("UNIVERSITY", "degree"),
    "semester": ("SEMESTER", "calendar"), "semesters": ("SEMESTERS", "calendar"),
    "focus": ("FOCUS", "target"), "grades": ("GRADES", "degree"),
    "cgpa": ("CGPA", "degree"), "compromise": ("COMPROMISE", "scale"),
    "page": ("ONE PAGE", "doc"), "pages": ("PAGE LIMIT", "pages"),
    "screening": ("SCREENING", "search"), "concise": ("BE CONCISE", "pages"),
    "contract": ("CONTRACT", "contract"), "clause": ("THE CLAUSE", "contract"),
    "agreement": ("AGREEMENT", "contract"), "sign": ("READ BEFORE SIGNING", "contract"),
    "nda": ("NDA", "shield"), "confidential": ("CONFIDENTIAL", "shield"),
    "ethics": ("ETHICS", "scale"), "ethical": ("ETHICS", "scale"),
    "legal": ("LEGAL", "scale"), "client": ("THE CLIENT", "handshake"),
    "clients": ("CLIENTS", "handshake"), "reputation": ("REPUTATION", "star"),
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


def make_stat(value, label, icon=None, pad=34):
    """Render a STAT card — a big number over a small caption.

    The plain chip is one line of text, which is the wrong shape for the moments an
    answer actually turns on ("60%", "10 -> 6", "3-4 YEARS"). A number wants to be
    read as a number, so it gets its own card: value in display type, the thing it
    counts underneath in the accent, and an optional icon down the left.

    Same tight-bounds contract as `make_chip`, so `layer()` animates either one
    without caring which it got.
    """
    vf, lf = ro.font(78, "Heavy"), ro.font(34, "Heavy")
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    tw = max(probe.textlength(value, font=vf), probe.textlength(label, font=lf))
    icon_s = 84 if icon else 0
    w = int(pad + (icon_s + 26 if icon else 0) + tw + pad)
    h = 190
    img = Image.new("RGBA", (w + 40, h + 40), (0, 0, 0, 0))
    ox, oy = 20, 20

    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).rounded_rectangle([ox - 8, oy - 8, ox + w + 8, oy + h + 8],
                                           radius=42, fill=(ACCENT[0], ACCENT[1], ACCENT[2], 115))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(18)))

    d = ImageDraw.Draw(img)
    d.rounded_rectangle([ox, oy, ox + w, oy + h], radius=34, fill=CARD, outline=ACCENT, width=3)
    # accent rule down the left edge — reads as a pull-quote rather than a button
    d.rounded_rectangle([ox + 3, oy + 26, ox + 11, oy + h - 26], radius=4, fill=ACCENT)

    tx = ox + pad
    if icon:
        tile = Image.new("RGBA", (icon_s, icon_s), (0, 0, 0, 0))
        ICONS.get(icon, _ic_code)(ImageDraw.Draw(tile), icon_s)
        img.alpha_composite(tile, (tx + 8, oy + (h - icon_s) // 2))
        tx += icon_s + 26
    d.text((tx, oy + 26), value, font=vf, fill=WHITE)
    d.text((tx, oy + 122), label, font=lf, fill=ACCENT)
    return img


def _render(item):
    """One timeline item -> its card image. Items are
    (start, end, label, icon) or (start, end, value, icon, "stat", sub_label)."""
    style = item[4] if len(item) > 4 else "chip"
    if style == "stat":
        return make_stat(item[2], item[5] if len(item) > 5 else "", item[3])
    return make_chip(item[2], item[3])


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

    for i, item in enumerate(sorted(items, key=lambda x: float(x[0]))):
        s, e = max(float(item[0]), t), float(item[1])
        if e - s < 0.4:
            continue
        if s > t:
            add(blank, s - t)
        chip = _render(item)
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
