# RUNBOOK — StreamYard Livestream → Social Reels

The **reproducible, battle-tested** workflow used to turn a weekly StreamYard
livestream into per-question vertical videos + hook reels. First proven on
`INPUT/17th July 2026/` (81-min, 1280×720). Read this before processing a stream.

> Some steps are **automated**, some are **manual review** (marked ✋). The manual
> steps exist because ASR/CV are imperfect on casual Urdu/English code-switch
> audio and same-asker back-to-back questions — do not skip them.

---

## 0. Environment (one-time)

- **macOS**, system **Python 3.9**, Homebrew.
- `brew install ffmpeg tesseract` (if `brew` errors on perms: `sudo chown -R $(whoami) /opt/homebrew`).
- `pip install pyyaml numpy opencv-python-headless pytesseract pillow openai-whisper`
- Verify: `tesseract --version`, `ffmpeg -version`, `python3 -c "import cv2,whisper,pytesseract,PIL"`

**Environment gotchas baked into the pipeline:**
- This ffmpeg has **no `drawtext` / `libass`** → all text is rendered as
  transparent PNGs with Pillow and composited via `overlay`; timed captions use a
  **qtrle** (alpha) layer video.
- `opencv-python` is **5.0** → no legacy Haar `CascadeClassifier`. Speaker is
  centered, so framing uses a fixed crop (no face-tracking needed).
- ffmpeg `overlay` extends to the **longest** input → composited bodies MUST use
  `overlay=...:shortest=1` **and** an explicit `-t <body_dur>`, or the tail
  **freezes with no audio**.

---

## Quick path — one command per question (reusable, low-token)

Once a clip is cut, the whole reel is built by **one config-driven command** — no
per-video model work beyond authoring the small inputs. This is the runtime path;
sections 1–6 below explain the internals.

```bash
# 1) make the folder + a config template
mkdir -p "INPUT/<day>/Clips/Q<N>"
python3 pipeline/process_question.py "<clip>.mp4" "INPUT/<day>/Clips/Q<N>"   # writes config.json template
# 2) fill in config.json (question, asker, video_title[catchy], description, hashtags),
#    add captions.json (Roman Urdu; optional), then run again:
python3 pipeline/process_question.py "<clip>.mp4" "INPUT/<day>/Clips/Q<N>"
```

It auto-detects screen shares, builds the **screen-share-aware body** (stacked: shared
screen on top, camera below) — cached as `_body.mp4` (`--force` to rebuild) — then
assembles **card → body → captions + tech chips → cinematic outro**, masters the audio,
writes the upload deliverables (**title / thumbnail_title / description / hashtags /
metadata.json** — title must be **catchy**), and finishes with an **automated QC pass**.
Per-video creative inputs: `config.json` fields + `captions.json`.

**Flags:** `--draft` (rough render for checking caption/chip timing — ~2x faster, written
to `<out_name>.draft.mp4` so it can never overwrite the real file), `--preview`,
`--force` (rebuild the cached body), `--no-verify` (skip QC).

### The encode path (this is what decides output quality)

```
trim  [CRF 16, 30ms edge fades — pipeline/trim.py]
  └─> body segments        [encode 1: CRF 19, parallel, measured grade]
        └─> overlay composite  [encode 2: captions + chips + name tag, audio COPIED]
              └─> join with card + outro   [COPY — no encode]
                    └─> audio master       [audio only, video COPIED]
```

**Picture is encoded twice; audio exactly once.** The old path put the picture through
three generations and the audio through three, because the final join used the concat
*filter*. `pipeline/encode.py` owns the one profile every part shares — that shared
profile is precisely what lets the join be a stream copy. If a copy-join ever drifts,
`encode.join()` verifies the duration and falls back to re-encoding, so a quirk costs
quality but never produces a broken reel.

Measured on Q2 (84s answer, no screen share): full run ~90s, `--draft` ~47s, and the
export gained bitrate at the same CRF (1965 → 2205 kbps) — the expected signature of one
fewer generation.

### Automated QC (`pipeline/verify_reel.py`)

Runs after every build. These are the failures that never raise during rendering:

```
container / format / faststart · duration vs the sum of parts · integrated LUFS
frozen tail (the `overlay` longest-input bug) · caption coverage of the body
```

Exit status is non-zero on any FAIL, so it can gate an upload. It has already earned its
place: it caught a silent-audio export during this pipeline's own development.

### Audio mastering (`pipeline/audio_master.py`)

One pass, at the end: **two-pass loudnorm to -14 LUFS / -1.0 dBTP** (what YouTube,
TikTok, Instagram and LinkedIn normalise to — the old -16 shipped quieter than
neighbouring videos in feed), plus head/tail fades. `linear=true` applies one constant
gain instead of riding it across the clip.

⚠️ **Cut fades belong at the trim, not here.** `afade=t=in:st=T` does not fade in at T —
it **mutes everything before T**. Chaining one per junction silences the whole reel.
`pipeline/trim.py` and `pipeline/silence.py` fade each piece's own edges before anything
is joined; the fades then survive untouched all the way to the export.

### On-screen tech chips (`pipeline/tech_overlays.py`)

When a technology / career keyword is **spoken**, a branded chip (vector icon +
word) pops in top-right, holds, fades out — the visual layer the brand expects
alongside captions. Config field `tech`:

- `"tech.json"` — hand-timed `[[start, end, "LABEL", "icon"], ...]` in **body time**
  (preferred: you place chips on the words that matter),
- `"auto"` — derived from `captions.json` via `tech_overlays.KEYWORD_MAP`,
- `null` — no chips.

Icons available: `briefcase, doc, code, terminal, github, linkedin, cloud, java,
ai, skill/star, rocket, warning`. Rules learned on Q2: keep chips **clear of the
first 5s** (the name tag owns the top band then), one chip at a time, ~3s each.

### Reusable modules (`pipeline/`)

| module | role |
| --- | --- |
| `process_question.py` | orchestrator — the one command you run per question |
| `encode.py` | **the encode profile every part shares**, probing, parallel map, copy-join |
| `trim.py` | cut one answer out of the stream: CRF 16, frame-accurate, edge fades |
| `screenshare_vertical.py` | framing, multi-share detection, parallel segment encodes |
| `grade.py` | measured, bounded colour correction (replaces the hardcoded eq) |
| `build_reel.py` | assembly: card → body → captions + chips → outro |
| `audio_master.py` | the single audio pass: two-pass loudnorm + fades |
| `overlays.py` / `tech_overlays.py` | Pillow text layers and animated chips |
| `verify_reel.py` | automated QC on the export |
| `transcribe.py` | local Whisper → segments, **word timings**, caption scaffold |
| `pack_transcripts.py` | all questions → one compact time-annotated markdown |
| `silence.py` | find and remove dead air; remap captions after a trim |
| `timeline_view.py` | filmstrip + waveform + word labels PNG for eyeball QC |

Worked example: `INPUT/17th July 2026/Clips/Q2/` (`config.json` + `captions.json` +
`tech.json`).

### Caption timing is no longer hand-authored

`pipeline/transcribe.py` now requests word-level timestamps, which makes three things
mechanical:

```bash
python3 pipeline/transcribe.py "<clip>.mp4" "<Q_dir>" large-v3 ur
# -> transcript.json, transcript.srt, words.json, captions.draft.json
```

`captions.draft.json` is already in the exact shape `captions.json` expects, so the only
remaining manual work is **transliterating the text to Roman Urdu** — the timings are
already right. Measured against the hand-authored Q2 captions, the scaffold lands at
~3.3s / 8.2 words per block versus 2.89s / 7.3 hand-authored: close enough to edit rather
than redo. Caption policy is unchanged: never ship a caption over audio you cannot
actually hear.

### Tightening pacing (`pipeline/silence.py`)

```bash
python3 pipeline/silence.py "<clip>.mp4" "<Q_dir>/words.json"            # report only
python3 pipeline/silence.py "<clip>.mp4" "<Q_dir>/words.json" \
    --apply tight.mp4 --captions "<Q_dir>/captions.json"                 # cut + remap
```

Always read the report first — a "gap" can be a deliberate beat. Tighten **before**
authoring captions where possible; if captions already exist, `--captions` remaps them
(blocks whose audio was cut are dropped).

### Eyeball QC (`pipeline/timeline_view.py`)

```bash
python3 pipeline/timeline_view.py "<Q_dir>/_body.mp4" 12 20 --words "<Q_dir>/words.json"
```

One PNG: filmstrip, waveform, word labels, shaded silences. Use it at the two or three
moments you are unsure about (caption sync, a share boundary, suspected dead air) — it is
a drill-down, not a scan. Rendering one per caption defeats the purpose.

## 1. Detect questions (automated draft ✋ verify)

```bash
python3 pipeline/detect_questions_streamyard.py "INPUT/<day>/<stream>.mp4" OUTPUT/questions_draft.json
```
How it works (`config/settings.yaml → ocr.streamyard_banner`):
- The question overlay is a **white bottom banner** with black text + a blue
  asker-handle pill. Detect **presence** by the bottom-band *white fraction*
  (≥0.25 = banner up; empty frames ≈0.03).
- The banner stays up while text swaps, so split segments at **banner frame-diff
  spikes** (>0.09 = new question). OCR a mid-segment frame with Tesseract for text.

**✋ VERIFY (critical):** pixel-diff **cannot** split same-asker back-to-back
questions or catch sub-threshold changes. Extract a frame near each segment's
**start and end**, read them, and fix boundaries:
- merge false splits (same question shown twice),
- split missed boundaries (two questions in one segment),
- drop non-questions (viewer comments).
On 17 Jul this turned 21 raw segments into ~20 verified questions.

## 2. Extract question clips

```bash
# per verified question — frame-accurate, CRF 16, with 30ms audio edge fades
python3 pipeline/trim.py "INPUT/<day>/<stream>.mp4" \
  "INPUT/<day>/Clips/Question_NN_<asker>-<slug>.mp4" --start 18:03 --end 19:27
```

Use `pipeline/trim.py` rather than a hand-written ffmpeg command. `-c copy` can only cut on
a keyframe, so a stream copy lands wherever the nearest keyframe happens to be; and the
**30ms audio fades belong here**, on the piece being cut, because every later stage
deliberately copies audio rather than re-encoding it (see the encode path above).

A lossless survey copy is still fine when you only need to eyeball where a question sits:
`ffmpeg -y -ss <start> -i <stream> -t <dur> -c copy <out>`.

## 3. Transcribe (automated ✋ review)

```bash
python3 pipeline/transcribe.py "<clip>.mp4" "<Q_dir>" large-v3 ur
# -> transcript.json, transcript.srt, words.json, captions.draft.json
```
`small` is quicker for a first pass; `large-v3` is slower on CPU but noticeably better on
unclear audio. Word timings come out of the same run at no extra cost.
Output is **Urdu script**. **✋ Hand-transliterate to Roman Urdu** (keep English
tech terms) — this is the brand caption style (PROMPTS.md #14). **Never fabricate
captions over unclear audio**: if Whisper hallucinates (repeated tokens), omit
those captions or hand-correct. (`large-v3` gives better text but is slow on CPU
and can still fail on genuinely unclear stretches.)

## 4. Build the vertical body — framing

`config/settings.yaml → social.framing` (**blurred-fit**, NOT a tight crop — a
tight 9:16 crop of 720p clips the shoulders):
```
[0:v]scale=-1:1920,crop=1080:1920,boxblur=26:2,eq=brightness=-0.16:saturation=1.1[bg];
[0:v]crop=555:588:362:0,scale=1080:-1,<MEASURED GRADE>,unsharp=5:5:0.4[fg];
[bg][fg]overlay=(W-w)/2:(H-h)/2[v]
```
`crop=555:588:362:0` keeps both shoulders and **removes the StreamYard banner**;
bands top/bottom hold the name tag and captions.

`<MEASURED GRADE>` used to be the fixed `eq=brightness=0.02:contrast=1.05:saturation=1.04`.
`pipeline/grade.py` now measures the clip instead — it samples frames through
`signalstats` on the **shipping crop** (not the raw frame, which still contains the banner
we discard) and emits a correction bounded to ±8% with no hue shift. On the 17 Jul stream
it emits `eq=contrast=1.030:saturation=1.040`: effectively the shipped look, but it now
adapts if the lighting changes. Calibration note: the bands are tuned for a **face crop**,
where a correctly lit face measures luma ~0.66 — a generic scene-oriented target of ~0.48
would read healthy footage as over-exposed and quietly darken every video.

Inspect what it would do, without rendering:
```bash
python3 pipeline/grade.py --analyze "<clip>.mp4" --dur 20 --crop 555:588:362:0
```

Screen-share segments are graded on the **camera PIP only** — the shared screen is left
exactly as captured, because nudging contrast on someone's code or slides makes it harder
to read, not easier.

Audio is **not** processed here. It is copied through and mastered once at the end
(`pipeline/audio_master.py`, -14 LUFS two-pass), so a body built on its own is not
loudness-normalised — that is deliberate.

## 5. Overlays + assemble (`pipeline/`)

- `pipeline/overlays.py` — Pillow renderers: `make_question_card`, `make_lower_third`
  (name tag, top-left), `make_caption` (2-line, keyword-highlighted, lower band),
  `make_outro` (static fallback).
- `pipeline/outro.py` — **animated** outro (`outro.mp4`): heading fades in, the 4
  social logos pop in staggered with an overshoot + idle bob, handle/name rise up.
- `pipeline/build_reel.py` — the assembler used at runtime. It:
  1. builds a **qtrle** caption layer (transparent) from the timeline,
  2. builds the tech-chip layer the same way,
  3. composites captions + chips + name tag with `overlay ... shortest=1` + `-t`
     (freeze-safe) — **the body's only re-encode**, with audio copied,
  4. normalises the card and the (cached) animated outro to the same profile,
  5. joins **question-card → body → outro** by **stream copy**,
  6. hands off to `audio_master.py` for the single audio pass.

- `pipeline/build_reel_example.py` — the original Q1 script, kept only as a historical
  reference. Do not build new videos with it: it predates the shared encode profile and
  re-encodes at every stage. Use `process_question.py`.

## 6. Metadata (author per question)

Per question package: `title.txt` (<70 chars, no clickbait), `thumbnail_title.txt`
(≤5 words), `description.txt` (question + takeaways), `hashtags.txt` (12-15),
`subtitles.srt` (Roman-Urdu, offset by the card duration), `metadata.json`.

`process_question.py` writes these from `config.json` and warns before anything overflows
a platform field (title >100 chars hard limit, >70 advisory; description >5000).

## 7. Review the packed stream (low-token)

```bash
python3 pipeline/pack_transcripts.py "INPUT/<day>/Clips" -o OUTPUT/takes_packed.md
```

Every question's transcript as phrase-level `[start-end]` lines in one file — roughly a
tenth the size of the raw JSON. This is the right surface for picking hook windows or
spotting duplicate answers across a whole stream in one pass instead of twenty.

---

## Deliverables per question

```
INPUT/<day>/Clips/Q<N>/
├── config.json                     # the only per-question input you author
├── captions.json                   # Roman-Urdu captions (from captions.draft.json)
├── tech.json                       # optional hand-timed chips
├── words.json, transcript.json     # from pipeline/transcribe.py
├── captions.draft.json             # generated caption scaffold
├── _trim.mp4, _body.mp4            # intermediates (cached; _body is the heavy one)
├── <out_name>.mp4                  # THE REEL (QC-verified)
├── <out_name>.draft.mp4            # optional cheap render, never shipped
├── question_card.png, name_tag.png
└── title/thumbnail_title/description/hashtags.txt, metadata.json
```

## Locked recipe (do not silently change)
- Framing: **blurred-fit**, both shoulders visible, banner removed.
- Captions: **Roman Urdu** + English tech terms, 2-line, keyword accent, centred at
  y=1330 — **inside the platform safe zone** (see below), not the old lower band.
- Title on all videos: **"Sr. Software Engineer | Mentor"**; handle **@raowaqasakram**.
- **Animated** like/subscribe/follow outro with YouTube/Facebook/TikTok/LinkedIn on
  **every** video.
- Never fabricate captions; verify question boundaries by eye.
- **Picture encoded at most twice, audio once.** Never reintroduce the concat *filter*
  into the final join, and never re-encode audio in an assembly stage.

## Caption safe zone (resolved 2026-07-26)

TikTok, Reels, Shorts and Facebook Reels paint their username / description / audio row and
the right-hand action rail over the bottom of the frame — roughly the **bottom 320px** for
organic posts, and **480px** under TikTok's strictest guidance. The old caption position put
the bar's bottom edge only ~212px up, i.e. **entirely inside** the band the apps write over,
so captions could be partly covered in-feed.

Captions are now centred at **y=1330** (`overlays.CAPTION_CENTER_Y`), which puts even a
two-line bar at 1212–1438 — **482px clear**, so it survives the strictest zone on every app.
The caption sits over the speaker's chest and never over the face.

The value is driven by the two-line case, which is the maximum `make_caption` renders:

```
bar_bottom = center + line_h + 24   ->   center <= H - 480 - 108   ->   center <= 1332
```

**The screen-share windows moved up with it.** `SCREEN_Y` 380→308 and `FACE_Y` 940→781: at
the old position the camera well ran to y=1321 and the raised caption would have covered its
bottom third. It now ends at ~1162, clearing the bar top at 1212. The vertical budget is:

```
name tag ends 248 | gap | shared screen (~405) | gap | camera (~381) | caption 1212
```

⚠️ **These two constants are coupled.** Do not raise the caption without checking the camera
well, or lower the wells without checking the caption. `tests/test_pipeline_quality.py`
asserts both bounds (clearance from the UI band, and no overlap with the well), so a drift in
either direction fails the suite rather than shipping a covered caption.

Per-question override if ever needed: `"caption_y": <number>` in `config.json` (default
`null` = the safe value). `overlays.CAPTION_CENTER_Y_LEGACY = 1600` is retained only to
document what changed — do not ship it.

## Horizontal output (planned)

`pipeline/encode.py` carries the delivery canvas in the profile
(`ORIENTATIONS = vertical | horizontal | square`), and `screenshare_vertical._out_scale()`
already adapts the render to a profile whose canvas differs from the layout canvas. So the
encode/join/master half of the pipeline is orientation-agnostic today.

What a horizontal render still needs: the Pillow layers (`overlays.py`, `tech_overlays.py`)
and the share-layout constants are authored at 1080×1920, so each needs a horizontal
variant — caption width and line wrapping, name-tag position, question-card layout, and the
stacked screen/camera geometry (side-by-side reads better than stacked at 16:9). That is a
layout job, not a plumbing job.

## Known limitations / TODO
- Reel window selection is still manual; caption **timing** is now generated
  (`captions.draft.json`) and only the Roman-Urdu transliteration is by hand.
- 720p → 1080×1920 fit is slightly soft (inherent to source).
- The cheap draft blur (`FACE_BG_CHEAP`) would nearly halve the **final** render too
  (10.0s → 5.3s on a 20s body) but is not pixel-identical to the shipping blur — a look
  decision, not taken unilaterally.
- Future: config-driven `modules/` refactor of the `pipeline/` scripts; optional
  `large-v3` pass; auto hook-window suggestion.
```
