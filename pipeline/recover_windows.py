"""Recover a stretch of audio that Whisper lost, without reloading the model per try.

`large-v3` can fail completely on a stretch of otherwise-fine audio: on 1 Aug Q6 it
emitted one phrase looped across **9-43s** — 34 seconds of the answer — while the
audio ran -15..-25 dBFS throughout. Re-feeding just that slice reproduces the loop,
so it is not `condition_on_previous_text` carryover; the model loses that audio.

The recovery (see the memory `reference-whisper-hallucination-recovery`) is to decode
**short isolated windows**, each in `ur` AND `en`, because on code-switched audio the
English pass is often the one that resolves a phrase. That works — but done by hand it
means one throwaway script per attempt, each paying the ~60-90s `large-v3` load again.
This module loads each model **once** and sweeps every window, so a whole recovery pass
is one command.

Diagnose before decoding: `--rms` prints a per-second level profile. Silence is a real
answer; healthy levels with an empty or looping transcript means hallucination.

    # 1. is there actually speech there?
    python3 pipeline/recover_windows.py <clip> --rms 0 50

    # 2. sweep the lost stretch (overlapping windows, ur + en)
    python3 pipeline/recover_windows.py <clip> --range 9 45

    # 3. tighten the spots still unclear, optionally with a second opinion
    python3 pipeline/recover_windows.py <clip> --windows 24:28 25.5:29.5 27:31 \
        --models large-v3 medium

Output is printed and written to `<clip_dir>/recovered.json` (or `--out`), with word
timings from the `ur` pass so recovered text can be captioned at the right moments.

Two smaller models **independently agreeing** is what makes text safe to caption — that
is the bar, not any single model's confidence. Never caption audio you cannot hear.
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np

SR = 16000

# A temperature ladder plus relaxed thresholds: the defaults give up on exactly the
# kind of short, noisy, code-switched window this module exists to decode.
DECODE = dict(condition_on_previous_text=False,
              temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
              no_speech_threshold=0.4,
              compression_ratio_threshold=2.0)

WIN = 6.5      # default window length, seconds
HOP = 5.0      # default step — the overlap is what makes windows joinable


def load_audio(path):
    """Decode the whole clip once, as mono float32 at 16k (what Whisper wants)."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR),
         "-f", "s16le", "-"], capture_output=True).stdout
    if not raw:
        sys.exit(f"could not decode audio from {path}")
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def rms_profile(audio, start, end):
    """Per-second dBFS. Healthy speech sits around -15..-25 dBFS."""
    for s in range(int(start), int(min(end, len(audio) / SR))):
        seg = audio[s * SR:(s + 1) * SR]
        db = 20 * np.log10(np.sqrt((seg ** 2).mean()) + 1e-9)
        print(f"{s:5d}s {db:7.1f} {'#' * int(max(0.0, db + 60) / 2)}")


def sweep(audio, windows, models, langs):
    """Decode every window with every model, in every language. Model loads once."""
    import whisper

    rows = {(s, e): {"start": s, "end": e} for s, e in windows}
    for name in models:
        print(f"\n===== {name} =====", flush=True)
        model = whisper.load_model(name)
        for (s, e) in windows:
            chunk = audio[int(s * SR):int(e * SR)]
            if not len(chunk):
                continue
            for lang in langs:
                r = model.transcribe(chunk, language=lang,
                                     word_timestamps=(lang == "ur"), **DECODE)
                text = r["text"].strip()
                rows[(s, e)][f"{name}|{lang}"] = text
                if lang == "ur":
                    rows[(s, e)][f"{name}|words"] = [
                        {"start": round(s + w["start"], 2),
                         "end": round(s + w["end"], 2),
                         "word": w["word"].strip()}
                        for seg in r.get("segments", [])
                        for w in (seg.get("words") or [])
                        if w.get("start") is not None]
                print(f"[{s:6.2f}-{e:6.2f}] {lang}: {text}", flush=True)
        del model
    return [rows[k] for k in windows]


def parse_windows(args):
    if args.windows:
        out = []
        for w in args.windows:
            s, _, e = w.partition(":")
            out.append((float(s), float(e)))
        return sorted(out)
    lo, hi = args.range
    out, t = [], float(lo)
    while t < hi:
        out.append((round(t, 2), round(min(t + args.win, hi), 2)))
        t += args.hop
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("clip")
    p.add_argument("--range", nargs=2, type=float, metavar=("START", "END"),
                   help="sweep this stretch with overlapping windows")
    p.add_argument("--windows", nargs="+", metavar="START:END",
                   help="decode exactly these windows instead")
    p.add_argument("--rms", nargs=2, type=float, metavar=("START", "END"),
                   help="print a per-second level profile and exit (diagnose first)")
    p.add_argument("--models", nargs="+", default=["large-v3"],
                   help="large-v3 first; add medium/small only as a second opinion")
    p.add_argument("--langs", nargs="+", default=["ur", "en"])
    p.add_argument("--win", type=float, default=WIN)
    p.add_argument("--hop", type=float, default=HOP)
    p.add_argument("--out")
    args = p.parse_args()

    audio = load_audio(args.clip)

    if args.rms:
        rms_profile(audio, *args.rms)
        return
    if not (args.range or args.windows):
        p.error("give --range, --windows or --rms")

    rows = sweep(audio, parse_windows(args), args.models, args.langs)

    out = args.out or os.path.join(os.path.dirname(args.clip) or ".", "recovered.json")
    json.dump(rows, open(out, "w"), indent=1, ensure_ascii=False)
    print(f"\nwrote {out}")
    print("Caption only what two independent decodes agree on — never fabricate.")


if __name__ == "__main__":
    main()
