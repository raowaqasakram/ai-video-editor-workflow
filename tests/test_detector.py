"""Tests for the StreamYard OCR question detector (Phase 2).

The pure timeline state machine is exhaustively tested with scripted samples
(no media, no OCR model). One end-to-end test generates a real video with a
drawn overlay and drives the full OpenCV sampling pipeline using a fake OCR
engine that "reads" the overlay by its brightness — so PaddleOCR is not needed
to validate the plumbing. FFmpeg-dependent tests skip when FFmpeg is absent.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

import pytest

from app.config_loader import Config
from modules.ocr.detector import (
    QUESTIONS_FILENAME,
    DetectedQuestion,
    FrameSample,
    OCRReading,
    build_timeline,
    detect_questions,
    format_timestamp,
    normalize_text,
    write_questions,
)

# --- Helpers -------------------------------------------------------------------


def _make_config(tmp_path: Path, ocr: Dict[str, Any]) -> Config:
    settings: Dict[str, Any] = {
        "processing": {"mode": "test"},
        "video": {"input_format": "mp4"},
        "ocr": ocr,
        "storage": {
            "input_folder": "INPUT",
            "output_folder": "OUTPUT",
            "approved_folder": "READY_TO_UPLOAD",
            "temp_folder": "TEMP",
        },
        "logging": {"enabled": False, "file": "logs/processing.log"},
    }
    branding = {"creator": {"name": "Test"}, "brand": {"style": "test"}}
    config = Config(settings=settings, branding=branding, project_root=tmp_path)
    config.ensure_directories()
    return config


def _samples(*pairs: tuple) -> List[FrameSample]:
    """Build FrameSamples from (timestamp, text, confidence) tuples."""
    return [FrameSample(timestamp=t, text=txt, confidence=c) for t, txt, c in pairs]


DEFAULTS = dict(confidence_threshold=0.8, min_duration_seconds=1.5, similarity_threshold=0.6)


# --- Pure helpers --------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [("  How   can\nI learn AI ", "How can I learn AI"), ("", ""), ("x", "x")],
)
def test_normalize_text(raw, expected):
    assert normalize_text(raw) == expected


@pytest.mark.parametrize(
    "seconds, expected",
    [(0, "00:00:00"), (75, "00:01:15"), (3661, "01:01:01"), (735, "00:12:15"), (-5, "00:00:00")],
)
def test_format_timestamp(seconds, expected):
    assert format_timestamp(seconds) == expected


# --- Timeline state machine ----------------------------------------------------


def test_single_question_segment():
    samples = _samples(
        (0.0, "", 0.0),
        (1.0, "Should I learn Java in 2026?", 0.95),
        (2.0, "Should I learn Java in 2026?", 0.90),
        (3.0, "Should I learn Java in 2026?", 0.92),
        (4.0, "", 0.0),
    )
    questions = build_timeline(samples, **DEFAULTS)
    assert len(questions) == 1
    q = questions[0]
    assert q.question == "Should I learn Java in 2026?"
    assert q.start == "00:00:01"
    assert q.end == "00:00:03"
    assert q.confidence == pytest.approx(0.923, abs=0.001)
    assert q.needs_review is False


def test_two_questions_separated_by_absence():
    samples = _samples(
        (1.0, "Question one about Docker", 0.9),
        (2.0, "Question one about Docker", 0.9),
        (3.0, "Question one about Docker", 0.9),
        (4.0, "", 0.0),
        (5.0, "Different question on Kubernetes", 0.9),
        (6.0, "Different question on Kubernetes", 0.9),
        (7.0, "Different question on Kubernetes", 0.9),
    )
    questions = build_timeline(samples, **DEFAULTS)
    assert len(questions) == 2
    assert "Docker" in questions[0].question
    assert "Kubernetes" in questions[1].question


def test_back_to_back_questions_without_gap_split_by_dissimilarity():
    samples = _samples(
        (1.0, "How do I learn Spring Boot fast", 0.9),
        (2.0, "How do I learn Spring Boot fast", 0.9),
        (3.0, "How do I learn Spring Boot fast", 0.9),
        (4.0, "What is the best cloud provider", 0.9),
        (5.0, "What is the best cloud provider", 0.9),
        (6.0, "What is the best cloud provider", 0.9),
    )
    questions = build_timeline(samples, **DEFAULTS)
    assert len(questions) == 2


def test_ocr_jitter_stays_single_question():
    # Minor per-frame OCR noise should not split one overlay into many.
    samples = _samples(
        (1.0, "Should I learn Java in 2026?", 0.9),
        (2.0, "Shou1d I learn Java in 2026?", 0.85),  # OCR mistake
        (3.0, "Should I learn Java in 2026", 0.88),
    )
    questions = build_timeline(samples, **DEFAULTS)
    assert len(questions) == 1
    # Highest-confidence reading wins the text.
    assert questions[0].question == "Should I learn Java in 2026?"


def test_transient_detection_is_discarded():
    # A single blip shorter than min_duration is treated as noise.
    samples = _samples(
        (0.0, "", 0.0),
        (1.0, "spurious text", 0.9),
        (2.0, "", 0.0),
    )
    questions = build_timeline(samples, **DEFAULTS)
    assert questions == []


def test_low_confidence_is_flagged_not_dropped():
    samples = _samples(
        (1.0, "Hw cn I lern AI", 0.55),
        (2.0, "Hw cn I lern AI", 0.60),
        (3.0, "Hw cn I lern AI", 0.58),
    )
    questions = build_timeline(samples, **DEFAULTS)
    assert len(questions) == 1
    assert questions[0].needs_review is True


def test_segment_open_at_end_of_video_is_closed():
    samples = _samples(
        (1.0, "Trailing question about AWS", 0.9),
        (2.0, "Trailing question about AWS", 0.9),
        (3.0, "Trailing question about AWS", 0.9),
    )
    questions = build_timeline(samples, **DEFAULTS)
    assert len(questions) == 1
    assert questions[0].end == "00:00:03"


def test_unordered_samples_are_sorted():
    samples = _samples(
        (3.0, "Ordered question test", 0.9),
        (1.0, "Ordered question test", 0.9),
        (2.0, "Ordered question test", 0.9),
    )
    questions = build_timeline(samples, **DEFAULTS)
    assert len(questions) == 1
    assert questions[0].start == "00:00:01"
    assert questions[0].end == "00:00:03"


# --- Output --------------------------------------------------------------------


def test_write_questions_matches_expected_schema(tmp_path):
    config = _make_config(tmp_path, ocr={})
    q = DetectedQuestion(
        question="Should I learn Java in 2026?",
        start="00:12:15", end="00:15:40", confidence=0.92,
        start_seconds=735.0, end_seconds=940.0, needs_review=False,
    )
    out = write_questions([q], config)
    assert out == config.output_dir / QUESTIONS_FILENAME
    data = json.loads(out.read_text())
    assert data[0]["question"] == "Should I learn Java in 2026?"
    assert data[0]["start"] == "00:12:15"
    assert data[0]["confidence"] == 0.92


# --- End-to-end: real video + fake brightness-based OCR engine -----------------


class _BrightnessOCREngine:
    """Fake OCR engine: 'reads' the fixed question when the ROI is bright.

    Stands in for a neural OCR model so the OpenCV sampling + timeline pipeline
    can be exercised end-to-end without PaddleOCR.
    """

    def __init__(self, text: str) -> None:
        self._text = text

    def read(self, image: Any) -> OCRReading:
        import numpy as np

        if float(np.mean(image)) > 180.0:  # white overlay box present
            return OCRReading(text=self._text, confidence=0.93)
        return OCRReading(text="", confidence=0.0)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg not installed")
def test_detect_questions_end_to_end(tmp_path):
    config = _make_config(
        tmp_path,
        ocr={
            "frame_interval_seconds": 0.5,
            "confidence_threshold": 0.80,
            "min_question_duration_seconds": 1.5,
            "text_similarity_threshold": 0.6,
            # Crop to the lower-left overlay zone where the box is drawn.
            "region": [0.03, 0.75, 0.47, 0.17],
        },
    )
    video = config.input_dir / "livestream.mp4"
    # 8s 640x480 clip with a white overlay box visible from t=2s to t=6s.
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=gray:size=640x480:rate=10:duration=8",
            "-vf", "drawbox=x=20:y=360:w=300:h=80:color=white:t=fill:enable='between(t,2,6)'",
            "-pix_fmt", "yuv420p",
            str(video),
        ],
        check=True, capture_output=True,
    )

    engine = _BrightnessOCREngine("Should I learn Java in 2026?")
    questions = detect_questions(config, engine=engine, video_path=video)

    assert len(questions) == 1
    q = questions[0]
    assert q.question == "Should I learn Java in 2026?"
    assert q.needs_review is False
    # Overlay window is [2,6]; boundaries accurate to within the 0.5s interval.
    assert 1.5 <= q.start_seconds <= 2.5
    assert 5.5 <= q.end_seconds <= 6.5
    assert (config.output_dir / QUESTIONS_FILENAME).is_file()
