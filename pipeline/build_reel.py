"""Generalized, reusable reel assembler (see feedback-reuse-code-runtime).

Composes:  question card (3.6s)  ->  answer body  ->  animated outro
with Roman-Urdu captions (keyword-highlighted) and a top name tag.

Per-video inputs are small files/args, not model work:
  - body mp4 (screen-share-aware vertical from pipeline/screenshare_vertical.py)
  - question text + asker handle          (for the card)
  - captions.json  [[start, end, "text", ["highlight", ...]], ...]  in BODY time
  - tech chips     [[start, end, "LABEL", "icon"], ...] or "auto"
  - outro mp4      (defaults to the cinematic brand outro)

Text is baked with Pillow (this ffmpeg has no drawtext/libass); the caption layer
is a qtrle alpha video built from a concat timeline (proven freeze-safe pattern).

Encode path (this is the part that decides output quality)
---------------------------------------------------------
    body segments   [encode 1, in screenshare_vertical]
        -> overlay composite   [encode 2, here — captions/chips/name tag]
        -> join with card + outro   [COPY, no encode]
        -> audio master             [audio only, video copied]

So the picture is encoded twice and the audio exactly once. The old path put the
picture through three generations and the audio through three, because the final
join used the concat *filter*. Everything downstream of the composite is now a
stream copy, which is both cleaner and much faster.

For that to hold, every part must share one encode profile — see
pipeline/encode.py, which owns those values.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audio_master  # noqa: E402
import encode  # noqa: E402
import overlays as ro  # noqa: E402
import tech_overlays as tov  # noqa: E402
from PIL import Image  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "_cache")     # normalised outros, shared by all questions
CARD_DUR = 3.6
NAME_TAG_SECONDS = 5

# keywords auto-highlighted in captions when a caption doesn't specify its own
KEYWORDS = {"java", "spring", "boot", "docker", "kubernetes", "ai", "ml", "aws",
            "azure", "cloud", "system", "design", "microservices", "architecture",
            "github", "stackoverflow", "terminal", "api", "git", "linux", "python",
            "startup", "tech", "company", "idea", "investment", "team", "hire",
            "business", "services", "products", "logic", "debug", "documentation"}


def dur(p):
    """Kept as a module-level name because callers (and metadata) use it."""
    return encode.duration(p)


def _auto_hl(text):
    return [w.strip(".,!?/()").lower() for w in text.split()
            if w.strip(".,!?/()").lower() in KEYWORDS]


def _caption_layer(caps, body_dur, work, transparent, p, caption_y=None):
    """Render caption PNGs and assemble a qtrle alpha layer over [0, body_dur]."""
    cap_dir = os.path.join(work, "caps")
    os.makedirs(cap_dir, exist_ok=True)
    items = []
    for i, cap in enumerate(caps):
        s, e, text = cap[0], cap[1], cap[2]
        hl = cap[3] if len(cap) > 3 and cap[3] else _auto_hl(text)
        png = os.path.join(cap_dir, f"cap_{i:03d}.png")
        ro.make_caption(text, hl, png, center_y=caption_y)
        items.append((round(float(s), 2), round(float(e), 2), png))

    lines, t = [], 0.0

    def add(img, d):
        # concat demuxer resolves `file` paths relative to the LIST file's dir,
        # so absolute paths are required (the clip dir also contains spaces).
        if d > 0.02:
            lines.append(f"file '{os.path.abspath(img)}'")
            lines.append(f"duration {d:.3f}")

    for s, e, png in items:
        s = max(s, t)
        if s > t:
            add(transparent, s - t)
        add(png, max(0.1, e - s))
        t = e
    if t < body_dur:
        add(transparent, body_dur - t)
    lines.append(f"file '{os.path.abspath(transparent)}'")   # trailing entry (concat quirk)
    listf = os.path.join(work, "caps_concat.txt")
    open(listf, "w").write("\n".join(lines))
    layer = os.path.join(work, "caption_layer.mov")
    encode.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listf,
                "-r", str(p["fps"]),
                "-vf", "%s,format=rgba" % encode.scale_filter(p),
                "-c:v", "qtrle", "-loglevel", "error", layer])
    return layer


def _composite_body(body, work, caps, tech_layer, lt_png, bdur, p,
                    transparent, caption_y):
    """Overlay captions + tech chips + name tag onto the body. The one re-encode.

    `shortest=1` plus an explicit `-t` is not optional: ffmpeg's overlay extends
    to its LONGEST input, so a caption layer even slightly longer than the body
    freezes the tail with no audio (see RUNBOOK env gotchas).

    Audio is copied, never re-encoded — mastering happens once at the very end.
    """
    out = os.path.join(work, "body_final.mp4")
    cmd = ["ffmpeg", "-y", "-i", body]
    chain, src, idx = [], "[0:v]", 1

    if caps:
        cmd += ["-i", _caption_layer(caps, bdur, work, transparent, p, caption_y)]
        chain.append(f"{src}[{idx}:v]overlay=0:0:shortest=1[vc]")
        src, idx = "[vc]", idx + 1
    if tech_layer:
        cmd += ["-i", tech_layer]
        chain.append(f"{src}[{idx}:v]overlay=0:0:shortest=1[vt]")
        src, idx = "[vt]", idx + 1

    # The name tag is a still, so a 5fps loop is plenty of input frames.
    cmd += ["-loop", "1", "-framerate", "5", "-t", f"{bdur}", "-i", lt_png]
    chain.append(f"{src}[{idx}:v]overlay=0:0:"
                 f"enable='between(t,0.2,{NAME_TAG_SECONDS})'[v]")

    cmd += ["-filter_complex", ";".join(chain),
            "-map", "[v]", "-map", "0:a", "-t", f"{bdur}"]
    cmd += encode.video_args(p) + ["-c:a", "copy"]
    cmd += ["-loglevel", "error", out]
    encode.run(cmd)
    return out


def _outro_part(outro, p):
    """Normalise the outro to the canonical profile, cached across all questions.

    The brand outro is one static file reused by every video; it used to be
    re-encoded on every run of every question for no reason.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    stem = os.path.splitext(os.path.basename(outro))[0]
    cached = os.path.join(
        CACHE_DIR, "%s.%s-%s.mp4" % (stem, p["orientation"], p["quality"]))
    return encode.normalize(outro, cached, p)


def build(body, out_dir, question, asker, name, title,
          caps=None, outro=None, out_name="REEL.mp4", tech=None,
          quality="final", orientation="vertical", denoise=False,
          caption_y=None):
    """Assemble the finished reel and return its path.

    Args:
        body: vertical answer body (from screenshare_vertical.render).
        out_dir: question folder; receives the reel and the PNG artefacts.
        question, asker: question-card content.
        name, title: name-tag content.
        caps: caption timeline in BODY time, or None.
        tech: chip timeline, "auto" (derive from caps), or None.
        outro: outro mp4, or None for the cinematic brand outro.
        quality: "final" ships; "preview"/"draft" for fast iteration.
        orientation: delivery canvas (see pipeline/encode.py).
        denoise: gentle noise reduction in the audio master (off by default).
        caption_y: override the caption block's vertical centre. Defaults to
            overlays.CAPTION_CENTER_Y, which is set to clear the platform UI
            band — only override with a value that still clears it.
    """
    os.makedirs(out_dir, exist_ok=True)
    work = os.path.join(out_dir, "_reel_work")
    os.makedirs(work, exist_ok=True)
    p = encode.profile(quality, orientation)
    outro = outro or os.path.join(HERE, "outro_cinematic.mp4")

    # A cheap render must never be mistaken for — or overwrite — the shipping
    # file, so its quality is stamped into the filename.
    if quality != "final":
        stem, ext = os.path.splitext(out_name)
        out_name = "%s.%s%s" % (stem, quality, ext)

    transparent = os.path.join(work, "transparent.png")
    Image.new("RGBA", (ro.W, ro.H), (0, 0, 0, 0)).save(transparent)

    card_png = os.path.join(out_dir, "question_card.png")
    lt_png = os.path.join(out_dir, "name_tag.png")
    ro.make_question_card(question, asker, card_png)
    ro.make_lower_third(name, title, lt_png)

    bdur = dur(body)
    if tech == "auto":
        tech = tov.auto(caps) if caps else None
    tech_layer = tov.layer(tech, bdur, work) if tech else None

    print(f"assembling reel [{p['quality']} {p['w']}x{p['h']} crf{p['crf']}]")
    body_final = _composite_body(body, work, caps, tech_layer, lt_png, bdur, p,
                                 transparent, caption_y)

    card_mp4 = encode.still_clip(card_png, CARD_DUR, os.path.join(work, "card.mp4"), p)
    outro_mp4 = _outro_part(outro, p)

    # Join by stream copy — no re-encode of any part.
    parts = [card_mp4, body_final, outro_mp4]
    joined = os.path.join(work, "joined.mp4")
    encode.join(parts, joined, p, work)

    # Master the audio once, over the joined reel. Cut fades are NOT applied here
    # — they belong at the trim, where the cut is made (see audio_master).
    final = os.path.join(out_dir, out_name)
    audio_master.master(joined, final, denoise=denoise,
                        two_pass=(quality != "draft"))

    print(f"REEL -> {final}  ({dur(final):.1f}s, {len(caps or [])} captions,"
          f" {len(tech or [])} chips)")
    return final


if __name__ == "__main__":
    # Usage: build_reel.py <body.mp4> <out_dir> <meta.json>
    # meta.json: {question, asker, name, title, captions?, tech?, outro?, out_name?}
    body, out_dir, meta_path = sys.argv[1], sys.argv[2], sys.argv[3]
    meta = json.load(open(meta_path))
    build(body, out_dir, meta["question"], meta["asker"],
          meta.get("name", "Rao Waqas Akram"),
          meta.get("title", "Sr. Software Engineer | Mentor"),
          caps=meta.get("captions"), outro=meta.get("outro"),
          out_name=meta.get("out_name", "REEL.mp4"), tech=meta.get("tech"),
          quality=meta.get("quality", "final"),
          denoise=meta.get("denoise", False),
          caption_y=meta.get("caption_y"))
