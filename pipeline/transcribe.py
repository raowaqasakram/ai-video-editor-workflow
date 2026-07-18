"""Local Whisper transcription -> timed segments (JSON + SRT), no network/API.

Reusable per video. Produces an Urdu-language draft that is then hand-corrected to
Roman Urdu for captions (see caption policy). Kept a separate step so the reel
assembly stays code-only and cheap to re-run.
"""
import json
import os
import sys


def fmt_ts(t):
    h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def transcribe(video, out_dir, model="small", language="ur"):
    import whisper
    os.makedirs(out_dir, exist_ok=True)
    mdl = whisper.load_model(model)
    res = mdl.transcribe(video, language=language, condition_on_previous_text=False,
                         no_speech_threshold=0.5, compression_ratio_threshold=2.2,
                         verbose=False)
    segs = [{"start": round(s["start"], 2), "end": round(s["end"], 2),
             "text": s["text"].strip()} for s in res["segments"]]
    with open(os.path.join(out_dir, "transcript.json"), "w") as f:
        json.dump(segs, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "transcript.srt"), "w") as f:
        for i, s in enumerate(segs, 1):
            f.write(f"{i}\n{fmt_ts(s['start'])} --> {fmt_ts(s['end'])}\n{s['text']}\n\n")
    print(f"transcribed {len(segs)} segments -> {out_dir}")
    return segs


if __name__ == "__main__":
    video = sys.argv[1]
    out_dir = sys.argv[2]
    model = sys.argv[3] if len(sys.argv) > 3 else "small"
    transcribe(video, out_dir, model=model)
