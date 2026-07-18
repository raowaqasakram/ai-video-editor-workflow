"""Render an animated outro video: heading fades in, the four social logos
pop/slide in one-by-one with an overshoot, then the handle + name rise up.
Frame-based (Pillow) because this ffmpeg build lacks drawtext/libass.
"""
import os, sys, math, subprocess
sys.path.insert(0, os.path.dirname(__file__))
import overlays as ro
from PIL import Image, ImageDraw, ImageChops

W, H = ro.W, ro.H
ACCENT, WHITE, BG = ro.ACCENT, ro.WHITE, ro.BG
FPS, DUR = 30, 4.8
S = os.path.dirname(__file__)
FR = f"{S}/outro_frames"

def clamp(x, a=0.0, b=1.0): return max(a, min(b, x))
def ease_out_cubic(p): return 1 - (1 - p) ** 3
def ease_out_back(p):
    c1 = 1.70158; c3 = c1 + 1
    return 1 + c3 * (p - 1) ** 3 + c1 * (p - 1) ** 2

ICONS = [ro._icon_youtube, ro._icon_facebook, ro._icon_tiktok, ro._icon_linkedin]

def layer(render_fn, alpha=1.0, dy=0):
    """Render on a transparent layer, offset vertically, scale alpha."""
    lyr = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    render_fn(ImageDraw.Draw(lyr))
    if dy:
        lyr = ImageChops.offset(lyr, 0, int(dy))
    if alpha < 1.0:
        a = lyr.split()[3].point(lambda p: int(p * alpha))
        lyr.putalpha(a)
    return lyr

def frame(t):
    img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 10], fill=ACCENT)
    d.rectangle([0, H - 10, W, H], fill=(ACCENT[0], ACCENT[1], ACCENT[2], 120))

    # Heading (two lines) fade + slide up
    ph = clamp(t / 0.5)
    ah = ease_out_cubic(ph); dyh = (1 - ah) * 40
    def _head(dd):
        hf = ro.font(72, "Heavy")
        l1 = "LIKE  •  SUBSCRIBE"; w1 = dd.textlength(l1, font=hf)
        dd.text(((W - w1) / 2, 560), l1, font=hf, fill=WHITE)
        l2 = "& FOLLOW"; w2 = dd.textlength(l2, font=hf)
        dd.text(((W - w2) / 2, 650), l2, font=hf, fill=ACCENT)
    img.alpha_composite(layer(_head, ah, dyh))

    # Icons pop in staggered with overshoot + subtle post-land bob
    s0 = 150; gap = 64; n = 4
    total = n * s0 + (n - 1) * gap
    x0 = (W - total) // 2 + s0 // 2
    cy = 1000
    for i, fn in enumerate(ICONS):
        start = 0.75 + i * 0.16
        p = clamp((t - start) / 0.5)
        if p <= 0:
            continue
        sc = ease_out_back(p)
        a = clamp(p * 1.7)
        dy = (1 - ease_out_cubic(p)) * 70
        # gentle continuous bob after landing
        if t > start + 0.5:
            sc += 0.03 * math.sin((t - start) * 3.0 + i)
        cx = x0 + i * (s0 + gap)
        s = max(4, int(s0 * sc))
        img.alpha_composite(layer(lambda dd, fn=fn, cx=cx, s=s: fn(dd, cx, cy, s), a, dy))

    # Handle + name + title rise up
    pn = clamp((t - 1.7) / 0.6)
    an = ease_out_cubic(pn); dyn = (1 - an) * 50
    def _who(dd):
        hnf = ro.font(60, "Heavy"); handle = "@raowaqasakram"
        dd.text(((W - dd.textlength(handle, font=hnf)) / 2, 1200), handle, font=hnf, fill=ACCENT)
        nf = ro.font(56, "Bold"); name = "Rao Waqas Akram"
        dd.text(((W - dd.textlength(name, font=nf)) / 2, 1290), name, font=nf, fill=WHITE)
        tf = ro.font(38, "Semibold"); title = "Sr. Software Engineer | Mentor"
        dd.text(((W - dd.textlength(title, font=tf)) / 2, 1370), title, font=tf, fill=(170, 180, 190, 255))
    img.alpha_composite(layer(_who, an, dyn))
    return img.convert("RGB")

def main():
    os.makedirs(FR, exist_ok=True)
    nframes = int(DUR * FPS)
    for i in range(nframes):
        frame(i / FPS).save(f"{FR}/f_{i:04d}.png")
    out = f"{S}/outro.mp4"
    subprocess.run(["ffmpeg", "-y", "-framerate", str(FPS), "-i", f"{FR}/f_%04d.png",
                    "-f", "lavfi", "-t", str(DUR), "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                    "-vf", "format=yuv420p", "-c:v", "libx264", "-crf", "19", "-preset", "medium",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", out, "-loglevel", "error"], check=True)
    print("animated outro ->", out, f"{nframes} frames")

if __name__ == "__main__":
    main()
