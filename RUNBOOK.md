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

### Pacing: dead-air trim + reel speed (one encode)

Reels ship **sped up**, with the dead air cut. Both are `config.json` fields and both
happen in a **single** encode (`silence.apply(speed=)`) that produces a cached
`_source.mp4`, so they cost one generation rather than two:

```jsonc
"speed": 1.35,          // standing default; he has changed it (1.5 -> 1.4 -> 1.35)
"trim_silence": true    // dead-air spans located from captions.json, else words.json
```

**`captions.json` and `tech.json` are authored in the RAW clip's timebase.** The
pipeline remaps them through the cut and divides by `speed`. That invariant is what
makes the speed a knob — otherwise every speed change means re-timing captions by hand.

Keep-ranges prefer `captions.json` over `words.json`, because the captions are the
verified record of where speech is; Whisper claims continuous speech wherever it
hallucinates a repetition loop, which is often exactly where the longest silence sits.

⚠️ In any speed pass `-t` must come **before** `-i`. After `-i` it bounds the *output*,
so ffmpeg reads `duration × speed` of input to fill it and every segment comes out
full-length holding the wrong footage — silently. Assert output durations.

### Framing knobs for a new stream

```jsonc
"face_crop": "746:776:0:0",  // SOURCE pixels; null = measured default, scaled
"background": "white",       // ALWAYS white unless told otherwise
"caption_y": 1549,           // see below
"card_design": null          // null = round-robin on the Q<N> folder number
```

- Crop constants were measured on **1280×720**. From the 25 June 2026 stream StreamYard
  records **1920×1080**, so they are scaled by source height (720p → exactly 1.0, so
  older clips re-render identically). Scaling is not always enough: set `face_crop`
  when the speaker is not centred in frame, and check it clears the StreamYard asker
  pill.
- Screen-share detection needs the corner to be genuinely brand-blue
  (`SHARE_CORNER_BLUE = 70`). At the old bound of 25 a grey-lavender wall read as a
  share and shattered the body into alternating segments.
- **`caption_y` balances VISIBLE white, not the band on the canvas.** The caption text
  sits in the bottom band and eats it, so a canvas-centred band looks top-heavy
  (398px above vs ~211px below on Q2). Solve `seam - band_h == 1920 - ink_bottom` and
  confirm on a rendered frame.
- Intro cards rotate through `overlays.CARD_DESIGNS`
  (`classic, spotlight, panel, editorial, banner`) so consecutive reels do not open
  identically. Index 0 is the original card.

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
[bg][fg]overlay=(W-w)/2:1282-h[v]
```
`crop=555:588:362:0` keeps both shoulders and **removes the StreamYard banner**.

### The sharp band is lifted, not centred (2026-07-27)

The vertical offset is `1282-h`, **not** `(H-h)/2`. Centred, the 1146px band ran
387..1533, which left a 387px grey blur slab across the top and pushed the band's
bottom edge past the caption bar at 1282 — so every caption was drawn across the
speaker's chest and had to compete with him to stay readable. Lifting the band so
its **bottom edge meets the caption bar's top edge** puts captions on clean blurred
fill and shrinks the top band to 136px.

- The seam is **derived, never hardcoded**:
  `FACE_SEAM_Y = ro.caption_bar_y(2)[0]` in `screenshare_vertical.py`. Move the
  caption and the framing follows. `overlays.caption_bar_y()` is the single source
  for that geometry — `make_caption`, the framing and the tests all read it.
- It is written as `1282-h` so **ffmpeg** computes the offset from the band's real
  height at runtime. The height is not the obvious number: `crop=555` is odd,
  yuv420p forces an even crop to 554, and `scale=1080:-1` then lands on **1146**
  (not 1144). A Python-side guess misplaces the seam.
- Accepted side effect: the name tag (y=110..248) now straddles the seam instead of
  sitting wholly in the grey band. Its own opaque plate keeps it legible.

`tests/test_pipeline_quality.py` asserts the seam equals the caption bar top, that
the band still fits on the canvas, and that it is no longer centred.

### The fill can be white instead of the blurred room (2026-07-28)

`config.json → "background"` selects what fills the canvas around the sharp band:

| value | fill | caption theme it implies |
| --- | --- | --- |
| `"blur"` (default) | the room, defocused and darkened | `dark` — white text on a near-opaque plate |
| `"white"` | solid white bands above and below | `light` — near-black text, **no** plate |

These are **one decision, not two**. White text on a white band is invisible, and
the dark plate on white reads as a black box floating in the frame — so
`screenshare_vertical.BACKGROUND_CAPTION_THEME` maps each fill to its caption
theme and `process_question.py` applies it automatically. `"caption_theme"`
overrides it per video, but there is rarely a reason to.

The white fill is `scale=4:4,drawbox=t=fill:c=white,scale=1080:1920` — derived
from the source frame rather than a lavfi `color` input purely so the background
inherits the video's own timestamps (an extra input would need its own PTS reset
and a `shortest` guard). Scaling to 4x4 before painting makes it near-free, and
`drawbox` writes broadcast white in the native pixel format: measured 253/253/253
on the export.

**The seam follows the caption.** `render()` now derives the seam from the
`caption_y` it is given (`ro.caption_bar_y(2, caption_y)[0]`) instead of reading
the module default, so moving the caption moves the footage band with it.

### Equal white borders — and what they cost (Q7, 2026-07-28)

Because the seam is derived from the caption, the caption position is also what
decides the *balance* of the two white bands:

| `caption_y` | top border | bottom border | caption clearance |
| --- | --- | --- | --- |
| 1400 (default) | 136 | 638 | 412px ✅ |
| 1460 | 196 | 578 | 352px ✅ |
| **1651** | **387** | **387** | **161px ⚠️** |

`1651` is the one value that centres the 1146px band exactly. The creator chose
it on Q7 after being shown the trade-off: at 161px the caption bar sits **inside**
the ~320px band TikTok/Reels/Shorts paint their username and description over, so
it can be partly covered in feed. That is a deliberate, per-video decision —
**do not promote it to the default.** `tests/test_pipeline_quality.py` asserts
both halves of it: that 1651 produces equal borders, and that it fails the safe
zone the default still passes.

Getting equal borders *and* the safe zone at the same time needs a shorter band
(≤828px, i.e. a `555:424` crop instead of `555:588`), which cuts the speaker
higher on the chest. That variant was rendered and not chosen.

Side effect at 1651: the name tag and the tech chips (anchored at y=110 and
y=196) now sit wholly in the **white top band** rather than over footage. Both
are dark plates, so they read as clean badges — checked on the export.

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
- Framing: **blurred-fit**, both shoulders visible, banner removed, sharp band
  **lifted** so its bottom edge meets the caption bar (not centred).
- **No background replacement.** Designed studio plates and RVM matting were built
  and then removed on 2026-07-27 — the creator rejected the look twice and asked for
  it gone. The room in the footage is the room that ships. Do not reintroduce it.
- Captions: **Roman Urdu** + English tech terms, 2-line, keyword accent, centred at
  y=1400 — **inside the platform safe zone** (see below), not the old lower band.
- Title on all videos: **"Sr. Software Engineer | Mentor"**; handle **@raowaqasakram**.
- **Animated** like/subscribe/follow outro with YouTube/Facebook/TikTok/LinkedIn on
  **every** video.
- Never fabricate captions; verify question boundaries by eye.
- **Picture encoded at most twice, audio once.** Never reintroduce the concat *filter*
  into the final join, and never re-encode audio in an assembly stage.

## Caption safe zone (resolved 2026-07-26, retuned 2026-07-27)

TikTok, Reels, Shorts and Facebook Reels paint their username / description / audio row and
the right-hand action rail over the bottom of the frame — roughly the **bottom 320px** for
organic posts, and **480px** under TikTok's strictest guidance. The old caption position put
the bar's bottom edge only ~212px up, i.e. **entirely inside** the band the apps write over,
so captions could be partly covered in-feed.

Captions are centred at **y=1400** (`overlays.CAPTION_CENTER_Y`), which puts a two-line bar
at **1282–1508 — 412px of clearance**:

| zone | reserved | result |
| --- | --- | --- |
| organic username/description band | ~320px | clear by ~90px ✅ |
| TikTok's strictest ad-safe zone | ~480px | inside by ~70px ⚠️ |

That trade-off is deliberate. 1330 cleared even the ad-safe zone but sat visibly high on the
speaker's chest; 480px reserves room for a CTA button organic posts do not have, so 412px is
safe for normal posts everywhere. **If a clip is ever run as a paid ad, set
`"caption_y": 1330` for that one.**

Since 2026-07-27 the caption no longer sits over the chest at all: the sharp footage band is
lifted so it ends exactly at the bar's top edge, and the caption sits on blurred fill (see
§4).

The value is driven by the two-line case, which is the maximum `make_caption` renders.
`overlays.caption_bar_y(lines, center_y=None)` is the **single source** for that geometry —
`make_caption` draws to it, `screenshare_vertical.FACE_SEAM_Y` aligns the framing to it, and
the tests assert against it. Do not re-derive it anywhere.

**The screen-share windows moved up with it.** `SCREEN_Y` 380→308 and `FACE_Y` 940→781: at
the old position the camera well ran to y=1321 and the raised caption would have covered its
bottom third. It now ends at ~1162, clearing the bar top at 1282. The vertical budget is:

```
name tag ends 248 | gap | shared screen (~405) | gap | camera (~381) | caption 1282
```

⚠️ **Three things are coupled to the caption position:** the screen-share camera well, the
platform UI band, and now the blurred-fit seam. Do not move the caption without checking all
three. `tests/test_pipeline_quality.py` asserts every bound — clearance from the UI band, no
overlap with the well, and seam == bar top — so a drift in any direction fails the suite
rather than shipping a covered caption.

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
