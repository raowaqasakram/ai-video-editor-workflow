"""Pack many question transcripts into one compact, time-annotated markdown.

The point is token cost. Picking hook windows or spotting duplicate answers means
reading across every question in a stream. Raw Whisper JSON for a 20-question
stream is enormous and mostly punctuation and floats; this collapses it to
phrase-level lines with `[start-end]` prefixes — roughly a tenth the size, while
keeping enough timing precision to address a cut.

    ## Q2  (84.0s, 31 phrases)
      [000.30-003.90] What do you think about the internships offered nowadays
      [003.95-006.80] I just need to apply and got the offer letter

Reads `words.json` when present (from pipeline/transcribe.py, best precision) and
falls back to `transcript.json` segments otherwise, so it works on questions
transcribed before word timings existed.

Usage:
    python3 pipeline/pack_transcripts.py "INPUT/17th July 2026/Clips" \
        -o OUTPUT/takes_packed.md
"""
import argparse
import glob
import json
import os

SILENCE_BREAK = 0.5     # a pause this long starts a new phrase


def _fmt(seconds):
    return "%06.2f" % seconds


def phrases_from_words(words, silence_break=SILENCE_BREAK):
    """Group word timings into phrases, breaking on pauses >= silence_break."""
    out, current, prev_end = [], [], None
    for w in words:
        if current and (w["start"] - prev_end) >= silence_break:
            out.append({"start": current[0]["start"], "end": prev_end,
                        "text": " ".join(x["word"] for x in current)})
            current = []
        current.append(w)
        prev_end = w["end"]
    if current:
        out.append({"start": current[0]["start"], "end": prev_end,
                    "text": " ".join(x["word"] for x in current)})
    return out


def _load_one(folder):
    """Return (label, duration, phrases) for one question folder, or None."""
    label = os.path.basename(folder.rstrip("/"))
    words_path = os.path.join(folder, "words.json")
    segs_path = os.path.join(folder, "transcript.json")
    captions_path = os.path.join(folder, "captions.json")

    if os.path.exists(words_path):
        with open(words_path) as fh:
            phrases = phrases_from_words(json.load(fh))
        source = "words"
    elif os.path.exists(segs_path):
        with open(segs_path) as fh:
            phrases = json.load(fh)
        source = "segments"
    elif os.path.exists(captions_path):
        # Shipped captions are hand-corrected Roman Urdu — the best text we have
        # for a question that was captioned before word timings existed.
        with open(captions_path) as fh:
            phrases = [{"start": c[0], "end": c[1], "text": c[2]}
                       for c in json.load(fh)]
        source = "captions"
    else:
        return None

    if not phrases:
        return None
    duration = phrases[-1]["end"] - phrases[0]["start"]
    return label, duration, phrases, source


def pack(clips_dir, out_path):
    """Write the packed markdown for every question folder under `clips_dir`."""
    folders = sorted(d for d in glob.glob(os.path.join(clips_dir, "*"))
                     if os.path.isdir(d))
    entries = [e for e in (_load_one(d) for d in folders) if e]
    if not entries:
        raise SystemExit("no transcripts/captions found under %s" % clips_dir)

    lines = ["# Packed transcripts",
             "",
             "Phrase-level, grouped on pauses >= %.1fs. Use `[start-end]` ranges"
             " (seconds, body time) to address a cut." % SILENCE_BREAK,
             ""]
    for label, duration, phrases, source in entries:
        lines.append("## %s  (%.1fs, %d phrases, from %s)"
                     % (label, duration, len(phrases), source))
        for ph in phrases:
            lines.append("  [%s-%s] %s"
                         % (_fmt(ph["start"]), _fmt(ph["end"]), ph["text"].strip()))
        lines.append("")

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w") as fh:
        fh.write("\n".join(lines))
    kb = os.path.getsize(out_path) / 1024.0
    total = sum(e[1] for e in entries)
    print("packed %d question(s), %.1fs of runtime -> %s (%.1f KB)"
          % (len(entries), total, out_path, kb))
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("clips_dir", help="directory containing the Q<N> folders")
    ap.add_argument("-o", "--output", default="OUTPUT/takes_packed.md")
    args = ap.parse_args()
    pack(args.clips_dir, args.output)
