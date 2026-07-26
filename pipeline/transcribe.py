"""Local Whisper transcription -> timed segments, word timings, caption scaffold.

No network, no API — everything runs on this machine (credential policy).

Why word-level timings matter
-----------------------------
Segment-level output only tells us "somewhere in these 4 seconds someone said
this", so caption *timing* had to be authored by hand — the most expensive step
left in the workflow. With `word_timestamps=True` we get per-word start/end, and
three things become mechanical:

* `draft_captions()` writes a `captions.draft.json` already in the exact shape
  `captions.json` expects, so the remaining manual work is transliterating the
  text into Roman Urdu — the timings are already right and stay right.
* `pipeline/silence.py` can find dead air to trim.
* `pipeline/timeline_view.py` can label words on a waveform for eyeball QC.

Whisper's Urdu output is in Urdu script. It is a **draft**: hand-transliterate to
Roman Urdu (brand caption style) and never ship a caption over audio you cannot
actually hear (caption policy — no fabrication).

Outputs in `out_dir`:
    transcript.json       [{start, end, text}, ...]     (unchanged shape)
    transcript.srt        subtitle file
    words.json            [{start, end, word}, ...]     (when word timings on)
    captions.draft.json   [[start, end, text], ...]     caption scaffold

Usage:
    python3 pipeline/transcribe.py <video> <out_dir> [model] [language]
"""
import json
import os
import sys

# Caption shape targets, matched to the reels already shipped: roughly 7 words
# and ~3s per card, hard-capped at 2 rendered lines by overlays.make_caption.
CAP_MAX_WORDS = 9
CAP_MAX_SECONDS = 4.0
CAP_MIN_SECONDS = 0.9
CAP_BREAK_GAP = 0.45        # a pause this long ends a caption
CAP_PUNCT = ".!?،۔"         # sentence enders, Urdu and Latin


def fmt_ts(t):
    h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def _collect_words(res):
    """Flatten Whisper's per-segment word lists into one ordered list."""
    words = []
    for seg in res.get("segments", []):
        for w in seg.get("words") or []:
            text = (w.get("word") or "").strip()
            if not text or w.get("start") is None or w.get("end") is None:
                continue
            words.append({"start": round(float(w["start"]), 2),
                          "end": round(float(w["end"]), 2),
                          "word": text})
    return words


def _merge_short(blocks):
    """Fold blocks shorter than CAP_MIN_SECONDS into a neighbour.

    Two rules that are easy to get wrong:

    * Merge into the *previous* block only when it is genuinely adjacent. Pulling
      a fragment backwards across a real pause would put words from after the
      pause on screen before they are spoken.
    * A too-short block with no previous block (or one across a pause) is carried
      *forward* into the next one instead of being kept — otherwise the very
      first caption can flash past in a fraction of a second.
    """
    out, pending = [], None
    for block in blocks:
        if pending is not None:
            block = [pending[0], block[1],
                     (pending[2] + " " + block[2]).strip()]
            pending = None
        # Epsilon guard: durations are float differences, and an exactly-minimum
        # block must not be treated as too short by a rounding artefact.
        if (block[1] - block[0]) >= CAP_MIN_SECONDS - 1e-6:
            out.append(block)
        elif out and (block[0] - out[-1][1]) < CAP_BREAK_GAP:
            out[-1][1] = block[1]
            out[-1][2] = (out[-1][2] + " " + block[2]).strip()
        else:
            pending = block
    if pending is not None:
        if out:
            out[-1][1] = pending[1]
            out[-1][2] = (out[-1][2] + " " + pending[2]).strip()
        else:
            out.append(pending)          # a single very short utterance
    return out


def draft_captions(words):
    """Group word timings into caption-sized blocks: [[start, end, text], ...].

    Breaks on, in order of authority: a pause >= CAP_BREAK_GAP, sentence
    punctuation, then the word/duration caps. Blocks too short to read are then
    merged into a neighbour by _merge_short.
    """
    blocks, current = [], []

    def flush():
        if not current:
            return
        text = " ".join(w["word"] for w in current).strip()
        blocks.append([current[0]["start"], current[-1]["end"], text])
        del current[:]

    prev_end = None
    for w in words:
        if current:
            gap = w["start"] - prev_end
            spans = w["end"] - current[0]["start"]
            ends_sentence = current[-1]["word"][-1:] in CAP_PUNCT
            if (gap >= CAP_BREAK_GAP or ends_sentence
                    or len(current) >= CAP_MAX_WORDS or spans > CAP_MAX_SECONDS):
                flush()
        current.append(w)
        prev_end = w["end"]
    flush()
    return [[round(a, 2), round(b, 2), t] for a, b, t in _merge_short(blocks)]


def transcribe(video, out_dir, model="small", language="ur", word_timestamps=True):
    """Transcribe `video` and write the artefacts listed in the module docstring.

    Returns the segment list (same shape this function has always returned, so
    existing callers keep working).
    """
    import whisper
    os.makedirs(out_dir, exist_ok=True)
    mdl = whisper.load_model(model)
    # Anti-hallucination settings from config/settings.yaml -> speech. Casual
    # Urdu/English code-switching makes Whisper prone to repeated-token runs.
    res = mdl.transcribe(video, language=language, condition_on_previous_text=False,
                         no_speech_threshold=0.5, compression_ratio_threshold=2.2,
                         word_timestamps=word_timestamps, verbose=False)

    segs = [{"start": round(s["start"], 2), "end": round(s["end"], 2),
             "text": s["text"].strip()} for s in res["segments"]]
    with open(os.path.join(out_dir, "transcript.json"), "w") as f:
        json.dump(segs, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "transcript.srt"), "w") as f:
        for i, s in enumerate(segs, 1):
            f.write(f"{i}\n{fmt_ts(s['start'])} --> {fmt_ts(s['end'])}\n{s['text']}\n\n")
    print(f"transcribed {len(segs)} segments -> {out_dir}")

    if word_timestamps:
        words = _collect_words(res)
        with open(os.path.join(out_dir, "words.json"), "w") as f:
            json.dump(words, f, ensure_ascii=False, indent=2)
        caps = draft_captions(words)
        with open(os.path.join(out_dir, "captions.draft.json"), "w") as f:
            json.dump(caps, f, ensure_ascii=False, indent=2)
        print(f"  {len(words)} word timings -> words.json")
        print(f"  {len(caps)} caption blocks -> captions.draft.json"
              f"  (timings ready; transliterate the text to Roman Urdu,"
              f" then save as captions.json)")
    return segs


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    transcribe(sys.argv[1], sys.argv[2],
               model=sys.argv[3] if len(sys.argv) > 3 else "small",
               language=sys.argv[4] if len(sys.argv) > 4 else "ur")
