"""Generalized, reusable reel assembler (see feedback-reuse-code-runtime).

Composes:  question card (3.6s)  ->  answer body  ->  animated outro
with Roman-Urdu captions (keyword-highlighted) and a top name tag.

Per-video inputs are small files/args, not model work:
  - body mp4 (screen-share-aware vertical from pipeline/screenshare_vertical.py)
  - question text + asker handle          (for the card)
  - captions.json  [[start, end, "text", ["highlight", ...]], ...]  in BODY time
  - outro mp4      (defaults to the cinematic brand outro)

Text is baked with Pillow (this ffmpeg has no drawtext/libass); the caption layer
is a qtrle alpha video built from a concat timeline (proven freeze-safe pattern).
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import overlays as ro  # noqa: E402
from PIL import Image  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CARD_DUR = 3.6
NAME_TAG_SECONDS = 5
CRF, PRESET = "19", "fast"

# keywords auto-highlighted in captions when a caption doesn't specify its own
KEYWORDS = {"java", "spring", "boot", "docker", "kubernetes", "ai", "ml", "aws",
            "azure", "cloud", "system", "design", "microservices", "architecture",
            "github", "stackoverflow", "terminal", "api", "git", "linux", "python",
            "startup", "tech", "company", "idea", "investment", "team", "hire",
            "business", "services", "products", "logic", "debug", "documentation"}


def run(c):
    subprocess.run(c, check=True)


def dur(p):
    return float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", p]).decode())


def _auto_hl(text):
    return [w.strip(".,!?/()").lower() for w in text.split()
            if w.strip(".,!?/()").lower() in KEYWORDS]


def _still(png, d, out):
    """A PNG -> d-second 1080x1920 30fps clip with a silent stereo track."""
    run(["ffmpeg", "-y", "-loop", "1", "-t", f"{d}", "-i", png,
         "-f", "lavfi", "-t", f"{d}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
         "-vf", "scale=1080:1920,format=yuv420p", "-r", "30",
         "-c:v", "libx264", "-crf", CRF, "-preset", PRESET,
         "-c:a", "aac", "-b:a", "192k", "-shortest", out, "-loglevel", "error"])


def _normalize(inp, out):
    """Re-encode any clip to the reel's canonical params (1080x1920, 30fps, aac).
    Adds a silent track if the source has none, so concat always has audio."""
    has_audio = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=index", "-of", "csv=p=0", inp],
        capture_output=True, text=True).stdout.strip() != ""
    cmd = ["ffmpeg", "-y", "-i", inp]
    if not has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    cmd += ["-vf", "scale=1080:1920,format=yuv420p", "-r", "30",
            "-c:v", "libx264", "-crf", CRF, "-preset", PRESET,
            "-c:a", "aac", "-b:a", "192k"]
    if not has_audio:
        cmd += ["-map", "0:v", "-map", "1:a", "-shortest"]
    cmd += [out, "-loglevel", "error"]
    run(cmd)


def _caption_layer(caps, body_dur, work, transparent):
    """Render caption PNGs and assemble a qtrle alpha layer over [0, body_dur]."""
    cap_dir = os.path.join(work, "caps")
    os.makedirs(cap_dir, exist_ok=True)
    items = []
    for i, cap in enumerate(caps):
        s, e, text = cap[0], cap[1], cap[2]
        hl = cap[3] if len(cap) > 3 and cap[3] else _auto_hl(text)
        png = os.path.join(cap_dir, f"cap_{i:03d}.png")
        ro.make_caption(text, hl, png)
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
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listf, "-r", "30",
         "-vf", "scale=1080:1920,format=rgba", "-c:v", "qtrle", layer, "-loglevel", "error"])
    return layer


def build(body, out_dir, question, asker, name, title,
          caps=None, outro=None, out_name="REEL.mp4"):
    os.makedirs(out_dir, exist_ok=True)
    work = os.path.join(out_dir, "_reel_work")
    os.makedirs(work, exist_ok=True)
    outro = outro or os.path.join(HERE, "outro_cinematic.mp4")

    transparent = os.path.join(work, "transparent.png")
    Image.new("RGBA", (ro.W, ro.H), (0, 0, 0, 0)).save(transparent)

    card_png = os.path.join(out_dir, "question_card.png")
    lt_png = os.path.join(out_dir, "name_tag.png")
    ro.make_question_card(question, asker, card_png)
    ro.make_lower_third(name, title, lt_png)

    bdur = dur(body)

    # composite captions (optional) + name tag (first NAME_TAG_SECONDS) onto body.
    # shortest=1 + explicit -t so a longer caption layer can never freeze the tail.
    body_final = os.path.join(work, "body_final.mp4")
    if caps:
        layer = _caption_layer(caps, bdur, work, transparent)
        run(["ffmpeg", "-y", "-i", body, "-i", layer,
             "-loop", "1", "-framerate", "5", "-t", f"{bdur}", "-i", lt_png,
             "-filter_complex",
             "[0:v][1:v]overlay=0:0:shortest=1[v1];"
             f"[v1][2:v]overlay=0:0:enable='between(t,0.2,{NAME_TAG_SECONDS})'[v]",
             "-map", "[v]", "-map", "0:a", "-t", f"{bdur}",
             "-c:v", "libx264", "-crf", CRF, "-preset", PRESET, "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k", body_final, "-loglevel", "error"])
    else:
        run(["ffmpeg", "-y", "-i", body,
             "-loop", "1", "-framerate", "5", "-t", f"{bdur}", "-i", lt_png,
             "-filter_complex",
             f"[0:v][1:v]overlay=0:0:enable='between(t,0.2,{NAME_TAG_SECONDS})'[v]",
             "-map", "[v]", "-map", "0:a", "-t", f"{bdur}",
             "-c:v", "libx264", "-crf", CRF, "-preset", PRESET, "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k", body_final, "-loglevel", "error"])

    card_mp4 = os.path.join(work, "card.mp4")
    outro_mp4 = os.path.join(work, "outro.mp4")
    _still(card_png, CARD_DUR, card_mp4)
    _normalize(outro, outro_mp4)

    final = os.path.join(out_dir, out_name)
    run(["ffmpeg", "-y", "-i", card_mp4, "-i", body_final, "-i", outro_mp4,
         "-filter_complex",
         "[0:v][0:a][1:v][1:a][2:v][2:a]concat=n=3:v=1:a=1[v][a]",
         "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-crf", CRF, "-preset", PRESET, "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", final, "-loglevel", "error"])
    print(f"REEL -> {final}  ({dur(final):.1f}s, {len(caps or [])} captions)")
    return final


if __name__ == "__main__":
    # Usage: build_reel.py <body.mp4> <out_dir> <meta.json>
    # meta.json: {question, asker, name, title, captions?, outro?, out_name?}
    body, out_dir, meta_path = sys.argv[1], sys.argv[2], sys.argv[3]
    meta = json.load(open(meta_path))
    build(body, out_dir, meta["question"], meta["asker"],
          meta.get("name", "Rao Waqas Akram"),
          meta.get("title", "Sr. Software Engineer | Mentor"),
          caps=meta.get("captions"), outro=meta.get("outro"),
          out_name=meta.get("out_name", "REEL.mp4"))
