"""One-command, config-driven reel builder — the runtime entry point.

Per question there is NO model work: drop a `config.json` in the question folder,
fill 2-3 fields, run this once. It auto-detects screen shares, builds the
screen-share-aware vertical body (cached), and assembles
card -> body -> captions -> outro.  (See feedback-reuse-code-runtime.)

    python3 pipeline/process_question.py "<clip.mp4>" "<Q_dir>"

`<Q_dir>/config.json`:
{
  "question":  "the viewer's question text",
  "asker":     "@handle",
  "name":      "Rao Waqas Akram",              # optional (defaults)
  "title":     "Sr. Software Engineer | Mentor", # optional
  "shares":    null,          # null = auto-detect; or [[start,end], ...]
  "captions":  "captions.json", # file path | inline [[s,e,txt,[hl]]] | null
  "outro":     null,          # null = cinematic brand outro
  "out_name":  "REEL.mp4"
}

Re-runs are cheap: the heavy body render is cached (delete _body.mp4 or pass
--force to rebuild), so tweaking captions only re-does the light layers.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import screenshare_vertical as ssv  # noqa: E402
import build_reel as br  # noqa: E402

TEMPLATE = {
    "question": "REPLACE WITH THE VIEWER QUESTION",
    "asker": "@handle",
    "name": "Rao Waqas Akram",
    "title": "Sr. Software Engineer | Mentor",
    "shares": None,
    "captions": "captions.json",
    "outro": None,
    "out_name": "REEL.mp4",
    # upload metadata (ALWAYS filled — catchy title + description + hashtags):
    "video_title": "CATCHY TITLE UNDER 70 CHARS",
    "thumbnail_title": "SHORT PUNCHY THUMBNAIL TEXT",
    "description": "2-4 line description + key takeaways.",
    "hashtags": ["#SoftwareEngineering", "#DeveloperCareer"],
    "scores": {"hook": 0, "educational_value": 0, "developer_relevance": 0,
               "shareability": 0, "virality_potential": 0},
    "notes": "",
}


def _write_metadata(cfg, out_dir, reel_path):
    """Write the upload deliverables: title / thumbnail / description / hashtags /
    metadata.json. Titles are meant to be CATCHY (see feedback-metadata-files)."""
    def w(name, text):
        open(os.path.join(out_dir, name), "w").write(text.rstrip() + "\n")

    title = cfg.get("video_title")
    if not title or title == TEMPLATE["video_title"]:
        print("WARNING: no catchy 'video_title' in config — skipping metadata files.")
        return
    w("title.txt", title)
    if cfg.get("thumbnail_title"):
        w("thumbnail_title.txt", cfg["thumbnail_title"])
    if cfg.get("description"):
        w("description.txt", cfg["description"])
    if cfg.get("hashtags"):
        w("hashtags.txt", "\n".join(cfg["hashtags"]))
    meta = {
        "id": os.path.basename(out_dir.rstrip("/")),
        "asker": cfg.get("asker"),
        "question": cfg.get("question"),
        "shares": cfg.get("shares"),
        "language": "Roman Urdu + English",
        "format": {"vertical": "1080x1920", "fps": 30,
                   "duration_s": round(br.dur(reel_path), 1)},
        "video_title": title,
        "thumbnail_title": cfg.get("thumbnail_title"),
        "scores": cfg.get("scores"),
        "notes": cfg.get("notes", ""),
    }
    json.dump(meta, open(os.path.join(out_dir, "metadata.json"), "w"),
              indent=2, ensure_ascii=False)
    print(f"metadata files written -> {out_dir}")


def process(clip, out_dir, force=False):
    os.makedirs(out_dir, exist_ok=True)
    cfg_path = os.path.join(out_dir, "config.json")
    if not os.path.exists(cfg_path):
        json.dump(TEMPLATE, open(cfg_path, "w"), indent=2, ensure_ascii=False)
        print(f"wrote template {cfg_path} — fill it in, then re-run.")
        return None
    cfg = json.load(open(cfg_path))

    # 1) body (screen-share-aware, cached)
    body = os.path.join(out_dir, "_body.mp4")
    if force or not os.path.exists(body):
        ssv.render(clip, body, shares=cfg.get("shares"))
    else:
        print(f"body cached -> {body} (pass --force to rebuild)")

    # 2) captions: file path (relative to out_dir), inline list, or None
    caps = cfg.get("captions")
    if isinstance(caps, str):
        cpath = caps if os.path.isabs(caps) else os.path.join(out_dir, caps)
        caps = json.load(open(cpath)) if os.path.exists(cpath) else None
        if caps is None:
            print(f"note: captions file '{cpath}' not found — building without captions.")

    # 3) assemble card -> body -> captions -> outro
    reel = br.build(
        body=body, out_dir=out_dir,
        question=cfg["question"], asker=cfg["asker"],
        name=cfg.get("name", "Rao Waqas Akram"),
        title=cfg.get("title", "Sr. Software Engineer | Mentor"),
        caps=caps, outro=cfg.get("outro"),
        out_name=cfg.get("out_name", "REEL.mp4"))

    # 4) upload deliverables (title/description/hashtags/metadata) — always
    _write_metadata(cfg, out_dir, reel)
    return reel


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--force"]
    force = "--force" in sys.argv
    if len(args) < 2:
        print(__doc__)
        sys.exit(1)
    process(args[0], args[1], force=force)
