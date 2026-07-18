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
assembles **card → body → captions → cinematic outro** and writes the upload
deliverables (**title / thumbnail_title / description / hashtags / metadata.json** — title
must be **catchy**). Per-video creative inputs: `config.json` fields + `captions.json`.

Reusable modules: `pipeline/screenshare_vertical.py` (framing, multi-share),
`pipeline/transcribe.py` (local Whisper → SRT draft), `pipeline/build_reel.py`
(assembly), `pipeline/process_question.py` (orchestrator). Worked example:
`INPUT/17th July 2026/Clips/Q16/` (`config.json` + `captions.json`).

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

## 2. Extract question clips (lossless)

```bash
# per verified question: -ss START -to END, stream-copy (fast, original quality)
ffmpeg -y -ss <start> -i "INPUT/<day>/<stream>.mp4" -t <dur> -c copy \
  "INPUT/<day>/Clips/Question_NN_<asker>-<slug>.mp4"
```
For a **precise** cut (frame-accurate), re-encode instead: `-c:v libx264 -crf 19
-preset medium -c:a aac -b:a 192k`.

## 3. Transcribe (automated ✋ review)

```bash
ffmpeg -y -i <clip>.mp4 -vn -ac 1 -ar 16000 clip.wav
# whisper 'medium', anti-hallucination (config/settings.yaml → speech)
python3 -c "import whisper; m=whisper.load_model('medium'); \
  print(m.transcribe('clip.wav', language='ur', condition_on_previous_text=False, \
  no_speech_threshold=0.5, compression_ratio_threshold=2.2))"
```
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
[0:v]crop=555:588:362:0,scale=1080:-1,eq=brightness=0.02:contrast=1.05:saturation=1.04,unsharp=5:5:0.4[fg];
[bg][fg]overlay=(W-w)/2:(H-h)/2[v]
```
`crop=555:588:362:0` keeps both shoulders and **removes the StreamYard banner**;
bands top/bottom hold the name tag and captions. Audio: `loudnorm=I=-16:TP=-1.5:LRA=11`.

## 5. Overlays + assemble (`pipeline/`)

- `pipeline/overlays.py` — Pillow renderers: `make_question_card`, `make_lower_third`
  (name tag, top-left), `make_caption` (2-line, keyword-highlighted, lower band),
  `make_outro` (static fallback).
- `pipeline/outro.py` — **animated** outro (`outro.mp4`): heading fades in, the 4
  social logos pop in staggered with an overshoot + idle bob, handle/name rise up.
- `pipeline/build_reel_example.py` — the Q1 reel builder = **template**. Per video,
  edit: source `CLIP`, `START/END` window, and the authored `CAPS` (Roman-Urdu
  caption blocks with per-block highlight words). It:
  1. trims the body to the chosen window,
  2. builds a **qtrle** caption layer (transparent) from the timeline,
  3. composites captions + name tag with `overlay ... shortest=1` + `-t` (freeze-safe),
  4. renders the animated outro,
  5. concats **question-card → body → outro**.

```bash
python3 pipeline/build_reel_example.py     # writes Q1_reel_final.mp4 next to the scripts
```

## 6. Metadata (author per question)

Per question package: `title.txt` (<70 chars, no clickbait), `thumbnail_title.txt`
(≤5 words), `description.txt` (question + takeaways), `hashtags.txt` (12-15),
`subtitles.srt` (Roman-Urdu, offset by the card duration), `metadata.json`.

---

## Deliverables per question

```
INPUT/<day>/Clips/
├── Question_NN_<asker>-<slug>.mp4        # lossless extracted answer
└── Question_NN_SOCIAL/
    ├── Question_NN_vertical.mp4          # full answer, vertical treatment
    ├── Question_NN_SHORT_reel.mp4        # trimmed hook reel (card+body+outro)
    ├── question_card.png, outro_animated.mp4
    ├── subtitles.srt
    └── title/thumbnail_title/description/hashtags.txt, metadata.json
```

## Locked recipe (do not silently change)
- Framing: **blurred-fit**, both shoulders visible, banner removed.
- Captions: **Roman Urdu** + English tech terms, 2-line, keyword accent, lower band.
- Title on all videos: **"Sr. Software Engineer | Mentor"**; handle **@raowaqasakram**.
- **Animated** like/subscribe/follow outro with YouTube/Facebook/TikTok/LinkedIn on
  **every** video.
- Never fabricate captions; verify question boundaries by eye.

## Known limitations / TODO
- Reel window + caption authoring are manual per video (the highest-effort step).
- 720p → 1080×1920 fit is slightly soft (inherent to source).
- Future: config-driven `modules/` refactor of the `pipeline/` scripts; optional
  `large-v3` pass; auto hook-window suggestion.
```
