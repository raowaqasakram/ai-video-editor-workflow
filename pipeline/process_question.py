"""One-command, config-driven reel builder — the runtime entry point.

Per question there is NO model work: drop a `config.json` in the question folder,
fill 2-3 fields, run this once. It auto-detects screen shares, builds the
screen-share-aware vertical body (cached), assembles
card -> body -> captions + chips -> outro, masters the audio, writes the upload
deliverables, and QCs the export.  (See feedback-reuse-code-runtime.)

    python3 pipeline/process_question.py "<clip.mp4>" "<Q_dir>"
    python3 pipeline/process_question.py "<clip.mp4>" "<Q_dir>" --draft
    python3 pipeline/process_question.py "<clip.mp4>" "<Q_dir>" --force

`<Q_dir>/config.json`:
{
  "question":  "the viewer's question text",
  "asker":     "@handle",
  "name":      "Rao Waqas Akram",              # optional (defaults)
  "title":     "Sr. Software Engineer | Mentor", # optional
  "shares":    null,          # null = auto-detect; or [[start,end], ...]
  "captions":  "captions.json", # file path | inline [[s,e,txt,[hl]]] | null
  "tech":      "tech.json",   # on-screen tech chips: file | "auto" (from captions)
                              # | inline [[s,e,"LABEL","icon"]] | null
  "outro":     null,          # null = cinematic brand outro
  "out_name":  "REEL.mp4",
  "style":        "blur",     # blur | studio_bands | studio_set | studio_real
  "backdrop_photo": null,     # studio styles: null = rendered set | "auto" (best
                              # fit for this framing) | "rotate" (cycle per
                              # question) | a filename | a path
  "backdrop_dir":   null,     # where "auto"/"rotate" look. null = assets/backdrops/
  "auto_grade":   true,       # measured colour correction (pipeline/grade.py)
  "denoise":      false,      # gentle noise reduction — only for rough audio
  "caption_y":    null        # null = the safe default (overlays.CAPTION_CENTER_Y)
}

Re-runs are cheap: the heavy body render is cached (delete _body.mp4 or pass
--force to rebuild), so tweaking captions only re-does the light layers.

Flags:
    --draft      rough render for checking caption/chip timing. Roughly twice as
                 fast (84s question: ~47s vs ~90s) and written to
                 `<out_name>.draft.mp4`, so it can never overwrite the real file.
    --preview    middle rung; watchable, cheaper than final
    --force      rebuild the cached body
    --no-verify  skip the automated QC pass
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backdrop  # noqa: E402
import build_reel as br  # noqa: E402
import encode  # noqa: E402
import screenshare_vertical as ssv  # noqa: E402
import verify_reel as qc  # noqa: E402

# Platform field limits (see feedback-metadata-length-limits). YouTube hard-caps
# titles at 100 chars; we aim under 70 so nothing truncates in a feed.
TITLE_HARD_LIMIT = 100
TITLE_TARGET = 70
DESCRIPTION_LIMIT = 5000

TEMPLATE = {
    "question": "REPLACE WITH THE VIEWER QUESTION",
    "asker": "@handle",
    "name": "Rao Waqas Akram",
    "title": "Sr. Software Engineer | Mentor",
    "shares": None,
    "captions": "captions.json",
    "tech": "auto",
    "outro": None,
    "out_name": "REEL.mp4",
    # render options (safe defaults — see the module docstring):
    "style": "blur",
    "backdrop_photo": None,
    "backdrop_dir": None,
    "auto_grade": True,
    "denoise": False,
    "caption_y": None,
    # upload metadata (ALWAYS filled — catchy title + description + hashtags):
    "video_title": "CATCHY TITLE UNDER 70 CHARS",
    "thumbnail_title": "SHORT PUNCHY THUMBNAIL TEXT",
    "description": "2-4 line description + key takeaways.",
    "hashtags": ["#SoftwareEngineering", "#DeveloperCareer"],
    "scores": {"hook": 0, "educational_value": 0, "developer_relevance": 0,
               "shareability": 0, "virality_potential": 0},
    "notes": "",
}


def _check_metadata_lengths(cfg):
    """Warn before anything overflows a platform field."""
    title = cfg.get("video_title") or ""
    if len(title) > TITLE_HARD_LIMIT:
        print("WARNING: title is %d chars — over the %d-char platform limit."
              % (len(title), TITLE_HARD_LIMIT))
    elif len(title) > TITLE_TARGET:
        print("note: title is %d chars; under %d reads better in a feed."
              % (len(title), TITLE_TARGET))
    desc = cfg.get("description") or ""
    if len(desc) > DESCRIPTION_LIMIT:
        print("WARNING: description is %d chars — over the %d-char limit."
              % (len(desc), DESCRIPTION_LIMIT))


def _write_metadata(cfg, out_dir, reel_path):
    """Write the upload deliverables: title / thumbnail / description / hashtags /
    metadata.json. Titles are meant to be CATCHY (see feedback-metadata-files)."""
    def w(name, text):
        open(os.path.join(out_dir, name), "w").write(text.rstrip() + "\n")

    title = cfg.get("video_title")
    if not title or title == TEMPLATE["video_title"]:
        print("WARNING: no catchy 'video_title' in config — skipping metadata files.")
        return
    _check_metadata_lengths(cfg)
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


# Shared backdrop photos live at the repo root, not per question, so every video
# in a season sits in the same room — the consistency is the point.
BACKDROP_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "assets", "backdrops")


def _backdrop_photo(cfg, out_dir=None):
    """Resolve the optional `backdrop_photo` config value to a real path.

    Accepted values:
        null              use the rendered set (pipeline/backdrop.py)
        "auto"            pick the photo that best suits this framing
        "rotate"          cycle photos by question number, so a batch of videos
                          is not all shot in the same room
        "<filename>"      a specific photo in the backdrop directory
        "<path>"          a specific photo anywhere

    A directory can be overridden per question with `backdrop_dir`, which is how
    a folder outside the repo (unlicensed or just large) stays out of git.

    Anything unresolvable warns and falls back to the rendered plate rather than
    failing: a typo in a filename should not cost a full re-encode.
    """
    name = cfg.get("backdrop_photo")
    if not name:
        return None
    directory = os.path.expanduser(cfg.get("backdrop_dir") or BACKDROP_DIR)

    if name in ("auto", "rotate"):
        rotate = None
        if name == "rotate":
            # Question number off the folder name (Q20 -> 20); anything unparseable
            # rotates from 0, which is still stable for that folder.
            digits = "".join(c for c in os.path.basename(out_dir or "") if c.isdigit())
            rotate = int(digits) if digits else 0
        chosen = backdrop.pick_photo(directory, rotate=rotate)
        if not chosen:
            print("WARNING: no backdrop photos in %s — using the rendered plate."
                  % directory)
        return chosen

    for cand in (os.path.expanduser(name), os.path.join(directory, name)):
        if os.path.exists(cand):
            return os.path.abspath(cand)
    print("WARNING: backdrop_photo %r not found (looked in %s) — "
          "using the rendered plate." % (name, directory))
    return None


def process(clip, out_dir, force=False, quality="final", verify=True):
    """Build one question end to end. Returns the reel path, or None if a config
    template was just written and there is nothing to build yet."""
    os.makedirs(out_dir, exist_ok=True)
    cfg_path = os.path.join(out_dir, "config.json")
    if not os.path.exists(cfg_path):
        json.dump(TEMPLATE, open(cfg_path, "w"), indent=2, ensure_ascii=False)
        print(f"wrote template {cfg_path} — fill it in, then re-run.")
        return None
    cfg = json.load(open(cfg_path))

    # 1) body (screen-share-aware, cached). Cheap-quality bodies are cached under
    #    their own name so an iteration pass can never overwrite the shipping one.
    # The style is part of the cache key: switching look must not silently reuse
    # a body rendered in the previous one.
    style = cfg.get("style", "blur")
    suffix = "" if quality == "final" else f".{quality}"
    suffix += "" if style == "blur" else f".{style}"
    # The backdrop photo is part of the look, so it is part of the cache key too.
    photo = _backdrop_photo(cfg, out_dir)
    if photo:
        suffix += "." + os.path.splitext(os.path.basename(photo))[0]
    body = os.path.join(out_dir, f"_body{suffix}.mp4")
    if force or not os.path.exists(body):
        ssv.render(clip, body, shares=cfg.get("shares"), quality=quality,
                   auto_grade=cfg.get("auto_grade", True),
                   style=cfg.get("style", "blur"), backdrop_photo=photo)
    else:
        print(f"body cached -> {body} (pass --force to rebuild)")

    # 2) timelines: a file path (relative to out_dir), an inline list, or None.
    #    "tech" also accepts "auto" -> derived from the captions by keyword.
    def _timeline(key):
        val = cfg.get(key)
        if not isinstance(val, str) or val == "auto":
            return val
        p = val if os.path.isabs(val) else os.path.join(out_dir, val)
        if os.path.exists(p):
            return json.load(open(p))
        print(f"note: {key} file '{p}' not found — building without it.")
        return None

    caps = _timeline("captions")
    tech = _timeline("tech")

    # 3) assemble card -> body -> captions + tech chips -> outro, master the audio
    reel = br.build(
        body=body, out_dir=out_dir,
        question=cfg["question"], asker=cfg["asker"],
        name=cfg.get("name", "Rao Waqas Akram"),
        title=cfg.get("title", "Sr. Software Engineer | Mentor"),
        caps=caps, outro=cfg.get("outro"), tech=tech,
        out_name=cfg.get("out_name", "REEL.mp4"),
        quality=quality,
        denoise=cfg.get("denoise", False),
        caption_y=cfg.get("caption_y"))

    # 4) upload deliverables (title/description/hashtags/metadata) — always
    _write_metadata(cfg, out_dir, reel)

    # 5) automated QC. These are the failures that never raise during rendering:
    #    a frozen tail, a duration that does not match the parts, loudness drift,
    #    a body that is only half captioned. Cheap to check, expensive to miss.
    if verify:
        body_dur = br.dur(body)
        outro_part = br._outro_part(
            cfg.get("outro") or os.path.join(br.HERE, "outro_cinematic.mp4"),
            encode.profile(quality))
        expected = br.CARD_DUR + body_dur + br.dur(outro_part)
        report = qc.verify(reel, expect_duration=expected,
                           caps=caps if isinstance(caps, list) else None,
                           body_dur=body_dur, quality=quality)
        report.print()
        if report.failed:
            print("QC FAILED — fix this before uploading the file.")
    return reel


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if len(args) < 2:
        print(__doc__)
        sys.exit(1)
    quality = ("draft" if "--draft" in flags
               else "preview" if "--preview" in flags else "final")
    process(args[0], args[1], force="--force" in flags, quality=quality,
            verify="--no-verify" not in flags)
