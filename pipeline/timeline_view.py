"""One PNG that shows what a stretch of video actually contains.

A filmstrip, the audio envelope underneath it, word labels from the transcript,
and shaded bands wherever there is a silence. It answers "are the captions
landing on the right words?", "did the share detector pick the right boundary?"
and "is there dead air here?" in a single glance, without scrubbing a player.

This is a **drill-down**, not a scan: render it at the two or three moments you
are unsure about. Rendering one per caption defeats the purpose.

Usage:
    python3 pipeline/timeline_view.py <video> <start> <end>
    python3 pipeline/timeline_view.py <video> 12 24 --words words.json -o look.png
"""
import argparse
import glob
import os
import subprocess
import sys
import tempfile
import wave

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import overlays as ro  # noqa: E402  (brand font loader)

# Dark palette so the strip reads like a monitoring view, not a brand asset.
BG = (18, 18, 22)
FG = (235, 235, 235)
DIM = (110, 110, 120)
WAVE = (0, 174, 239)            # brand accent
SILENCE = (60, 90, 130, 130)

MARGIN = 50
FRAME_H = 180
WAVE_H = 220
SILENCE_MIN = 0.4               # shade gaps at least this long


def _extract_frames(video, start, end, n, dest):
    """Grab `n` evenly spaced frames across [start, end] as small JPEGs."""
    times = ([(start + end) / 2.0] if n < 2 else
             [start + i * (end - start) / (n - 1) for i in range(n)])
    paths = []
    for i, t in enumerate(times):
        out = os.path.join(dest, "f_%03d.jpg" % i)
        subprocess.run(["ffmpeg", "-y", "-ss", "%.3f" % t, "-i", video,
                        "-frames:v", "1", "-q:v", "4", "-vf", "scale=320:-2", out],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        paths.append(out)
    return paths


def _envelope(video, start, end, samples):
    """RMS audio envelope over [start, end], normalised to 0..1."""
    fd, wav = tempfile.mkstemp(suffix=".wav", prefix="tlv_")
    os.close(fd)
    try:
        res = subprocess.run(
            ["ffmpeg", "-y", "-ss", "%.3f" % start, "-i", video,
             "-t", "%.3f" % (end - start), "-vn", "-ac", "1", "-ar", "16000",
             "-c:a", "pcm_s16le", wav],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode != 0 or not os.path.getsize(wav):
            return np.zeros(samples)
        with wave.open(wav, "rb") as wf:
            pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
        if pcm.size == 0:
            return np.zeros(samples)
        pcm = pcm.astype(np.float32) / 32768.0
        window = max(1, pcm.size // samples)
        usable = (pcm.size // window) * window
        env = np.sqrt(np.mean(pcm[:usable].reshape(-1, window) ** 2, axis=1))
        env = np.pad(env, (0, max(0, samples - env.size)))[:samples]
        return env / env.max() if env.max() > 0 else env
    finally:
        if os.path.exists(wav):
            os.remove(wav)


def _words_in(words, start, end):
    return [w for w in words
            if w.get("end") is not None and w.get("start") is not None
            and w["end"] > start and w["start"] < end]


def _silences(words, start, end, threshold=SILENCE_MIN):
    """Gaps between the given words, including lead-in and tail."""
    gaps, prev = [], start
    for w in words:
        if w["start"] - prev >= threshold:
            gaps.append((prev, w["start"]))
        prev = max(prev, w["end"])
    if end - prev >= threshold:
        gaps.append((prev, end))
    return gaps


def render(video, start, end, out_path, n_frames=10, words=None):
    """Write the composite PNG. `words` is a words.json list, or None."""
    words = _words_in(words or [], start, end)
    tmp = tempfile.mkdtemp(prefix="timeline_view_")
    try:
        frames = _extract_frames(video, start, end, n_frames, tmp)
        imgs = []
        for path in frames:
            img = Image.open(path).convert("RGB")
            imgs.append(img.resize((int(FRAME_H * img.width / img.height), FRAME_H),
                                   Image.LANCZOS))

        strip_w = sum(i.width for i in imgs) + 4 * (len(imgs) - 1)
        canvas_w = max(1400, strip_w) + 2 * MARGIN
        strip_y = MARGIN + 26
        wave_y = strip_y + FRAME_H + 26
        canvas_h = wave_y + WAVE_H + 70

        canvas = Image.new("RGB", (canvas_w, canvas_h), BG)
        draw = ImageDraw.Draw(canvas, "RGBA")
        head_f, small_f = ro.font(22, "Semibold"), ro.font(15, "Regular")

        draw.text((MARGIN, 14),
                  "%s   %.2fs - %.2fs   (%.2fs, %d frames)"
                  % (os.path.basename(video), start, end, end - start, len(imgs)),
                  fill=FG, font=head_f)

        # Filmstrip, scaled down if it would overflow the canvas.
        avail = canvas_w - 2 * MARGIN
        scale = min(1.0, avail / float(strip_w))
        x = MARGIN
        for img in imgs:
            w, h = int(img.width * scale), int(FRAME_H * scale)
            canvas.paste(img.resize((w, h), Image.LANCZOS),
                         (x, strip_y + (FRAME_H - h) // 2))
            x += w + max(2, int(4 * scale))
        x0, x1 = MARGIN, x - max(2, int(4 * scale))
        span = max(1, x1 - x0)

        def to_x(t):
            return int(x0 + span * (t - start) / max(1e-6, end - start))

        draw.rectangle((x0, wave_y, x1, wave_y + WAVE_H), fill=(28, 28, 34))
        for gs, ge in _silences(words, start, end):
            draw.rectangle((to_x(gs), wave_y, to_x(ge), wave_y + WAVE_H), fill=SILENCE)

        env = _envelope(video, start, end, span)
        mid, amp = wave_y + WAVE_H // 2, WAVE_H // 2 - 8
        top = [(x0 + i, mid - int(v * amp)) for i, v in enumerate(env)]
        bot = [(x0 + i, mid + int(v * amp)) for i, v in enumerate(env)]
        if top:
            draw.polygon(top + bot[::-1], fill=(WAVE[0], WAVE[1], WAVE[2], 70))
            draw.line(top, fill=WAVE, width=1)
            draw.line(bot, fill=WAVE, width=1)

        # Word labels, thinned so they never overlap into mush.
        last_x = -9999
        for w in words:
            cx = (to_x(w["start"]) + to_x(w["end"])) // 2
            if cx - last_x < 30:
                continue
            draw.line((cx, wave_y - 5, cx, wave_y), fill=DIM, width=1)
            draw.text((cx + 2, wave_y - 22), w.get("word", ""), fill=FG, font=small_f)
            last_x = cx

        # Time ruler.
        ruler = wave_y + WAVE_H + 4
        for i in range(7):
            t = start + (end - start) * i / 6.0
            tx = x0 + span * i // 6
            draw.line((tx, ruler, tx, ruler + 6), fill=DIM, width=1)
            draw.text((tx - 18, ruler + 9), "%.2fs" % t, fill=DIM, font=small_f)
        if words:
            draw.text((x0, ruler + 34),
                      "shaded = silence >= %.1fs" % SILENCE_MIN, fill=DIM, font=small_f)

        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        canvas.save(out_path, "PNG", optimize=True)
        print("timeline view -> %s (%d KB)"
              % (out_path, os.path.getsize(out_path) // 1024))
        return out_path
    finally:
        for path in glob.glob(os.path.join(tmp, "*")):
            os.remove(path)
        os.rmdir(tmp)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("video")
    ap.add_argument("start", type=float)
    ap.add_argument("end", type=float)
    ap.add_argument("-o", "--output", default=None)
    ap.add_argument("--n-frames", type=int, default=10)
    ap.add_argument("--words", default=None, help="words.json for labels + silences")
    args = ap.parse_args()
    if args.end <= args.start:
        raise SystemExit("end must be greater than start")

    word_list = None
    if args.words and os.path.exists(args.words):
        import json
        with open(args.words) as fh:
            word_list = json.load(fh)

    out = args.output or os.path.join(
        os.path.dirname(os.path.abspath(args.video)), "_verify",
        "%s_%.2f-%.2f.png" % (os.path.splitext(os.path.basename(args.video))[0],
                              args.start, args.end))
    render(args.video, args.start, args.end, out,
           n_frames=args.n_frames, words=word_list)
