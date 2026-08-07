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
  "speed":     1.35,          # reel playback speed; dead-air cut + speed happen in
                              # ONE encode. captions/tech are authored in the RAW
                              # clip's timebase and remapped onto it automatically.
  "trim_silence": true,       # remove dead air (spans located from captions.json,
                              # falling back to words.json)
  "shares":    null,          # null = auto-detect; or [[start,end], ...]
  "captions":  "captions.json", # file path | inline [[s,e,txt,[hl]]] | null
  "tech":      "tech.json",   # on-screen tech chips: file | "auto" (from captions)
                              # | inline [[s,e,"LABEL","icon"]] | null
  "outro":     null,          # null = cinematic brand outro
  "out_name":  "REEL.mp4",
  "auto_grade":   true,       # measured colour correction (pipeline/grade.py)
  "denoise":      false,      # gentle noise reduction — only for rough audio
  "caption_y":    null,       # null = the safe default (overlays.CAPTION_CENTER_Y)
  "background":   "blur",     # fill around the footage band: "blur" | "white"
  "intro_outro":  "dark",     # palette of the intro CARD and the brand OUTRO:
                              # "white" (matches a white-fill body) | "dark"
                              # (the original near-black cinematic pair)
  "caption_theme": null,      # null = whatever the background implies
  "face_crop":    null,       # "w:h:x:y" in SOURCE pixels; null = the measured
                              # default, scaled to the source resolution. Set it
                              # when the speaker is not centred in frame.
  "grade_crop":   null,       # "w:h:x:y" in SOURCE pixels around the SPEAKER,
                              # used ONLY to measure the grade. Set it when the
                              # room is lit differently from him — a big bright
                              # wall averages a backlit face away and he ships
                              # dark. null = measure the whole framing crop.
  "grade_target": null,       # exposure to aim his face at (0..1) when
                              # grade_crop is set. null = grade.SUBJECT_TARGET_
                              # LUMA. Raise it for a brighter render.
  "card_design":  null        # intro-card design: index or name from
                              # overlays.CARD_DESIGNS. null = round-robin on the
                              # Q<N> folder number (classic, spotlight, panel,
                              # editorial, banner, then repeat).
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
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_reel as br  # noqa: E402
import encode  # noqa: E402
import outro_cinematic  # noqa: E402
import overlays as ro  # noqa: E402
import silence  # noqa: E402
import screenshare_vertical as ssv  # noqa: E402
import verify_reel as qc  # noqa: E402

# Platform field limits (see feedback-metadata-length-limits). YouTube hard-caps
# titles at 100 chars; we aim under 70 so nothing truncates in a feed.
TITLE_HARD_LIMIT = 100
TITLE_TARGET = 70
DESCRIPTION_LIMIT = 5000

# `intro_outro` is spelled in the same vocabulary as `background` ("white"), while
# the renderers name their palettes "light"/"dark". This is the one place the two
# meet. A missing field reads as "dark" — the pair every reel shipped before
# 2026-08-06 ends and opens with.
INTRO_OUTRO_THEMES = {"white": "light", "light": "light", "dark": "dark"}

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
    "speed": 1.35,
    "trim_silence": True,
    "auto_grade": True,
    "denoise": False,
    "caption_y": None,
    # The standing brief is a WHITE video (feedback-white-fill-equal-borders),
    # so a NEW question starts there: white fill behind the footage and a white
    # intro card / outro to book-end it. A config that predates these fields is
    # read with the old defaults ("blur"/"dark"), so nothing already shipped
    # re-renders differently.
    "background": "white",
    "intro_outro": "white",
    "caption_theme": None,
    "face_crop": None,
    "grade_crop": None,
    "grade_target": None,
    "card_design": None,
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


def _srt_time(t):
    h, rem = divmod(max(0.0, t), 3600)
    m, s = divmod(rem, 60)
    return "%02d:%02d:%06.3f" % (h, m, s)  # SRT wants a comma decimal, fixed below


def _write_srt(caps, out_dir, name="subtitles.srt"):
    """Write the burned-in captions out as an uploadable subtitle file.

    The captions are in BODY time, but the reel opens with the question card, so
    every cue is shifted by `br.CARD_DUR` to line up with the exported file.
    Platforms that accept a subtitle track (YouTube, LinkedIn) then carry the same
    Roman-Urdu text the video already shows.
    """
    if not isinstance(caps, list) or not caps:
        return None
    path = os.path.join(out_dir, name)
    blocks = []
    for i, c in enumerate(caps, 1):
        start = _srt_time(float(c[0]) + br.CARD_DUR).replace(".", ",")
        end = _srt_time(float(c[1]) + br.CARD_DUR).replace(".", ",")
        blocks.append("%d\n%s --> %s\n%s\n" % (i, start, end, c[2]))
    open(path, "w").write("\n".join(blocks))
    print(f"subtitles -> {path} ({len(caps)} cues)")
    return path


def _speech_spans(out_dir, cfg):
    """Word-shaped spans of KNOWN speech, for the dead-air pass.

    Prefers `captions.json` over `words.json`: the captions are the hand-verified
    record of where speech actually is, while Whisper's words claim continuous
    speech wherever it hallucinated a repetition loop — which is exactly where the
    longest silences tend to be.
    """
    caps = cfg.get("captions")
    if isinstance(caps, str) and caps != "auto":
        p = caps if os.path.isabs(caps) else os.path.join(out_dir, caps)
        if os.path.exists(p):
            data = json.load(open(p))
            return [{"start": float(c[0]), "end": float(c[1]), "word": ""} for c in data]
    elif isinstance(caps, list):
        return [{"start": float(c[0]), "end": float(c[1]), "word": ""} for c in caps]
    words = os.path.join(out_dir, "words.json")
    if os.path.exists(words):
        return json.load(open(words))
    return []


def _prepare_source(clip, out_dir, cfg, suffix, force):
    """Cut dead air and apply the reel speed in one encode. Returns
    (source_path, keeps, speed) — keeps/speed are what map authored times onto it."""
    speed = float(cfg.get("speed") or 1.0)
    trim = cfg.get("trim_silence", True)
    if speed == 1.0 and not trim:
        return clip, None, 1.0

    spans = _speech_spans(out_dir, cfg)
    if trim and not spans:
        print("note: no captions.json/words.json to locate dead air — speed only.")
    keeps = (silence.keep_ranges(spans, encode.duration(clip))
             if (trim and spans) else None)

    out = os.path.join(out_dir, f"_source{suffix}.mp4")
    if force or not os.path.exists(out):
        ranges = keeps or [[0.0, encode.duration(clip)]]
        if keeps:
            cut = encode.duration(clip) - sum(e - s for s, e in keeps)
            print("dead air: removing %.2fs over %d span(s)" % (cut, len(keeps) - 1))
        silence.apply(clip, ranges, out, speed=speed)
    else:
        print(f"prepared source cached -> {out} (pass --force to rebuild)")
    print("source: %.2fs -> %.2fs at %.2fx" % (encode.duration(clip),
                                               encode.duration(out), speed))
    return out, keeps, speed


def _retime(timeline, keeps, speed):
    """Move an authored timeline from the raw clip onto the prepared source."""
    if timeline is None or not isinstance(timeline, list):
        return timeline
    out = silence.remap_captions(timeline, keeps) if keeps else [list(c) for c in timeline]
    if speed != 1.0:
        out = [[round(c[0] / speed, 2), round(c[1] / speed, 2)] + list(c[2:]) for c in out]
    return out


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

    # The fill and the caption are one decision: white bands want dark text, a
    # blurred room wants the dark plate. The caption position feeds the body too,
    # because the footage band's bottom edge is pinned to the caption's top edge.
    background = cfg.get("background") or "blur"
    caption_y = cfg.get("caption_y")
    caption_theme = (cfg.get("caption_theme")
                     or ssv.BACKGROUND_CAPTION_THEME[background])

    # The intro card and the outro book-end the same video, so they are one
    # choice, not two. "white" is spelled the way `background` is; it maps to the
    # "light" palette name the renderers use.
    card_theme = INTRO_OUTRO_THEMES[cfg.get("intro_outro") or "dark"]

    # 0) prepare the source: cut dead air and apply the reel speed, in ONE encode.
    #    `captions`/`tech` are authored in the RAW clip's timebase; this step
    #    returns the mapping needed to move them onto the prepared source, so the
    #    speed stays a knob instead of something baked into hand-authored timings.
    suffix = "" if quality == "final" else f".{quality}"
    clip, keeps, speed = _prepare_source(clip, out_dir, cfg, suffix, force)

    # 1) body (screen-share-aware, cached). Cheap-quality bodies are cached under
    #    their own name so an iteration pass can never overwrite the shipping one.
    body = os.path.join(out_dir, f"_body{suffix}.mp4")
    if force or not os.path.exists(body):
        ssv.render(clip, body, shares=cfg.get("shares"), quality=quality,
                   auto_grade=cfg.get("auto_grade", True),
                   background=background, caption_y=caption_y,
                   face_crop=cfg.get("face_crop"),
                   grade_crop=cfg.get("grade_crop"),
                   grade_target=cfg.get("grade_target"))
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

    # Authored in the RAW clip's timebase, then moved onto the prepared source.
    caps = _retime(_timeline("captions"), keeps, speed)
    tech = _retime(_timeline("tech"), keeps, speed)

    # The intro card rotates through overlays.CARD_DESIGNS so a viewer bingeing
    # the series does not see the same opening five times. Explicit config wins;
    # otherwise the design follows the Q<N> folder number, which makes the
    # rotation automatic and stable across re-runs.
    card_design = cfg.get("card_design")
    if card_design is None:
        m = re.search(r"\d+", os.path.basename(out_dir.rstrip("/")))
        card_design = ro.card_design_for(m.group()) if m else 0

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
        caption_y=caption_y, caption_theme=caption_theme,
        card_design=card_design, card_theme=card_theme)

    # 4) upload deliverables (title/description/hashtags/metadata/subtitles) — always
    _write_metadata(cfg, out_dir, reel)
    _write_srt(caps, out_dir)

    # 5) automated QC. These are the failures that never raise during rendering:
    #    a frozen tail, a duration that does not match the parts, loudness drift,
    #    a body that is only half captioned. Cheap to check, expensive to miss.
    if verify:
        body_dur = br.dur(body)
        outro_part = br._outro_part(
            cfg.get("outro") or outro_cinematic.ensure(card_theme),
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
