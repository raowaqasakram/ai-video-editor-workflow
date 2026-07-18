"""StreamYard question detection for this creator's livestreams.

Empirical approach discovered on the 17 July 2026 stream (1280x720, StreamYard):

  * The question overlay is a WHITE rounded banner across the bottom of the frame
    with black text, plus a blue asker-handle pill just above it. When no question
    is shown, that band shows the couch / a small LIKE-SUBSCRIBE graphic.
  * PRESENCE is detected cheaply by the "white fraction" of the bottom band
    (>= ~0.25 => banner up). Non-banner frames score ~0.03; banner frames 0.37-0.85.
  * The banner often stays up continuously while the TEXT swaps between questions,
    so presence alone can't split adjacent questions. A grayscale frame-diff of the
    banner region spikes (>~0.09) at text changes => internal boundaries.
  * Pixel-diff CANNOT catch same-asker back-to-back swaps or sub-threshold changes,
    so boundaries MUST be verified by eye (extract a frame per segment and read it),
    and the question TEXT read with Tesseract (banner is clean black-on-white).

This script does the automated scan + segmentation and (optionally) OCRs a
representative banner frame per segment. Treat its output as a DRAFT to verify.

Usage:
    python pipeline/detect_questions_streamyard.py "INPUT/<day>/<stream>.mp4" out.json
"""
from __future__ import annotations
import json, sys
import cv2
import numpy as np

# --- Tunables (empirical for 1280x720 StreamYard; scale ROIs if resolution differs)
SAMPLE_SEC = 1.5          # scan interval
WHITE_THR = 0.25          # bottom-band white fraction => banner present
SPIKE = 0.09              # banner-region frame-diff => text changed (new question)
CLUSTER_SEC = 6.0         # merge spikes within this window
GAPFILL_SEC = 4.5         # bridge brief presence dropouts
MIN_DUR_SEC = 20.0        # discard sub-20s segments as noise
BAND = (0.84, 0.99, 0.03, 0.97)   # whiteness band  (y0,y1,x0,x1 as fractions)
TEXT = (0.85, 0.99, 0.06, 0.94)   # text region for diff / OCR


def _white_fraction(frame):
    h, w = frame.shape[:2]
    y0, y1, x0, x1 = BAND
    band = frame[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]
    return float(np.all(band > 195, axis=2).mean())


def _text_gray(frame):
    h, w = frame.shape[:2]
    y0, y1, x0, x1 = TEXT
    return cv2.cvtColor(frame[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)], cv2.COLOR_BGR2GRAY)


def scan(path):
    """Return per-sample [timestamp, white_fraction, banner_diff]."""
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    step = int(round(SAMPLE_SEC * fps))
    prev, out, idx = None, [], 0
    while idx < total:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, fr = cap.read()
        if not ok:
            break
        gray = _text_gray(fr)
        diff = (float(np.mean(np.abs(gray.astype(np.int16) - prev.astype(np.int16))) / 255.0)
                if prev is not None and prev.shape == gray.shape else 1.0)
        prev = gray
        out.append([round(idx / fps, 2), round(_white_fraction(fr), 3), round(diff, 4)])
        idx += step
    cap.release()
    return out


def segment(samples):
    """Presence runs split at text-change spikes -> [(start,end)] seconds."""
    t = [s[0] for s in samples]; wf = [s[1] for s in samples]; df = [s[2] for s in samples]
    n = len(samples)
    present = [w >= WHITE_THR for w in wf]
    i = 0
    while i < n:  # gap-fill brief dropouts
        if not present[i]:
            j = i
            while j < n and not present[j]:
                j += 1
            if 0 < i and j < n and t[j] - t[i - 1] <= GAPFILL_SEC:
                for k in range(i, j):
                    present[k] = True
            i = j
        else:
            i += 1
    runs, s = [], None
    for i in range(n):
        if present[i] and s is None:
            s = i
        if (not present[i] or i == n - 1) and s is not None:
            runs.append((s, i - 1 if not present[i] else i)); s = None
    segs = []
    for a, b in runs:
        pts, last = [a], -99
        for i in range(a + 1, b + 1):
            if df[i] > SPIKE and (t[i] - t[last] if last > 0 else 99) > CLUSTER_SEC:
                pts.append(i); last = i
        pts.append(b + 1)
        for k in range(len(pts) - 1):
            i0, i1 = pts[k], pts[k + 1] - 1
            if i1 >= i0:
                segs.append((t[i0], t[i1]))
    return [(a, b) for a, b in segs if b - a >= MIN_DUR_SEC]


def ocr_banner(path, ts):
    """Read the banner text at a timestamp with Tesseract (clean black-on-white)."""
    import pytesseract
    cap = cv2.VideoCapture(path)
    cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000)
    ok, fr = cap.read(); cap.release()
    if not ok:
        return ""
    h, w = fr.shape[:2]
    y0, y1, x0, x1 = TEXT
    crop = fr[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return " ".join(pytesseract.image_to_string(binary, config="--psm 6").split())


def mmss(x):
    return f"{int(x // 60):02d}:{int(x % 60):02d}"


if __name__ == "__main__":
    path, out = sys.argv[1], sys.argv[2]
    segs = segment(scan(path))
    result = []
    for i, (a, b) in enumerate(segs, 1):
        text = ocr_banner(path, a + min(18, (b - a) * 0.4))
        result.append({"n": i, "start": mmss(a), "end": mmss(b),
                       "start_s": round(a, 1), "end_s": round(b, 1),
                       "draft_question": text, "verified": False})
        print(f"  Q{i:02d} {mmss(a)}-{mmss(b)}  {text[:70]!r}")
    json.dump(result, open(out, "w"), ensure_ascii=False, indent=2)
    print(f"\n{len(result)} draft segments -> {out}   (VERIFY boundaries+text by eye)")
