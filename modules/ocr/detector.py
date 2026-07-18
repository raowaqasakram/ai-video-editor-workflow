"""StreamYard question overlay detection (Phase 2).

Detects when a viewer question appears and disappears on screen, extracts the
question text, and writes a timeline to ``questions.json``.

Pipeline:
    1. Sample frames efficiently (every ``frame_interval_seconds``) with OpenCV.
    2. Optionally crop to a configured region of interest (the overlay zone).
    3. Run OCR on each sampled frame via a swappable :class:`OCREngine`.
    4. Build a question timeline with a pure state machine (:func:`build_timeline`).
    5. Persist ``questions.json``.

Design notes:
    * OpenCV and PaddleOCR are imported lazily, so importing this module (and the
      unit tests) never requires those heavy dependencies.
    * The OCR engine is injectable, keeping detection logic testable without a
      real neural model and allowing EasyOCR/Tesseract fallbacks later
      (CLAUDE.md OCR preference order).
    * :func:`build_timeline` is pure and side-effect free — the heart of the
      module — so all edge cases are unit-testable with scripted samples.

See ARCHITECTURE.md §6-§7 for the module contract.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Protocol, Tuple

from app.config_loader import Config
from app.logger import get_logger

log = get_logger(__name__)

QUESTIONS_FILENAME = "questions.json"

# Fractional region [x, y, w, h] with values in 0-1.
Region = Tuple[float, float, float, float]


class OCRDetectionError(Exception):
    """Raised when the video cannot be opened or OCR cannot be performed."""


@dataclass(frozen=True)
class OCRReading:
    """A single OCR result for one image/frame.

    Attributes:
        text: Recognised text (may be empty when nothing is detected).
        confidence: Mean confidence in the range 0-1.
    """

    text: str
    confidence: float


@dataclass(frozen=True)
class FrameSample:
    """OCR result for one sampled frame at a given timestamp.

    Attributes:
        timestamp: Seconds from the start of the video.
        text: Normalised recognised text ("" when the overlay is absent).
        confidence: Mean OCR confidence for the reading.
    """

    timestamp: float
    text: str
    confidence: float


@dataclass
class DetectedQuestion:
    """A detected question overlay segment.

    Attributes:
        question: Best (highest-confidence) recognised question text.
        start: Start timestamp formatted ``HH:MM:SS``.
        end: End timestamp formatted ``HH:MM:SS``.
        confidence: Mean confidence across the segment (0-1, rounded).
        start_seconds: Raw start time in seconds (for downstream clipping).
        end_seconds: Raw end time in seconds.
        needs_review: True when confidence is below the configured threshold.
    """

    question: str
    start: str
    end: str
    confidence: float
    start_seconds: float
    end_seconds: float
    needs_review: bool

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable dictionary of the detection."""
        return asdict(self)


class OCREngine(Protocol):
    """Protocol for OCR backends. Implementations recognise text in an image."""

    def read(self, image: Any) -> OCRReading:  # pragma: no cover - interface
        """Recognise text in ``image`` and return an :class:`OCRReading`."""
        ...


# --- Text + time helpers (pure) ------------------------------------------------


def normalize_text(text: str) -> str:
    """Collapse whitespace and strip surrounding noise from OCR output.

    Args:
        text: Raw OCR text.

    Returns:
        Cleaned single-line text (may be empty).
    """
    if not text:
        return ""
    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned


def format_timestamp(seconds: float) -> str:
    """Format a number of seconds as ``HH:MM:SS``.

    Args:
        seconds: Non-negative time offset in seconds.

    Returns:
        Zero-padded ``HH:MM:SS`` string.
    """
    total = int(round(max(0.0, seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _similar(a: str, b: str) -> float:
    """Return a 0-1 similarity ratio between two strings (case-insensitive)."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


# --- Timeline construction (pure core) -----------------------------------------


@dataclass
class _Segment:
    """Mutable accumulator for consecutive present samples."""

    samples: List[FrameSample] = field(default_factory=list)

    @property
    def best_text(self) -> str:
        """Highest-confidence text seen so far in the segment."""
        return max(self.samples, key=lambda s: s.confidence).text

    @property
    def mean_confidence(self) -> float:
        """Mean confidence across the segment's samples."""
        return sum(s.confidence for s in self.samples) / len(self.samples)


def build_timeline(
    samples: List[FrameSample],
    *,
    confidence_threshold: float,
    min_duration_seconds: float,
    similarity_threshold: float,
) -> List[DetectedQuestion]:
    """Convert ordered per-frame OCR samples into a question timeline.

    Pure and deterministic: no I/O, fully unit-testable. A segment begins when a
    non-empty reading follows an absent one (or a sufficiently different reading)
    and ends when the overlay disappears or the text changes.

    Args:
        samples: Frame samples ordered by ascending timestamp.
        confidence_threshold: Below this mean confidence a question is flagged
            ``needs_review``.
        min_duration_seconds: Segments shorter than this are discarded as noise.
        similarity_threshold: Below this text similarity a new reading is treated
            as a new question rather than a continuation.

    Returns:
        Detected questions in chronological order.
    """
    ordered = sorted(samples, key=lambda s: s.timestamp)
    segments: List[_Segment] = []
    current: Optional[_Segment] = None

    for sample in ordered:
        present = bool(sample.text)
        if not present:
            if current is not None:
                segments.append(current)
                current = None
            continue

        if current is None:
            current = _Segment(samples=[sample])
        elif _similar(sample.text, current.best_text) >= similarity_threshold:
            current.samples.append(sample)
        else:
            # A different question appeared with no absent gap between them.
            segments.append(current)
            current = _Segment(samples=[sample])

    if current is not None:
        segments.append(current)

    return _segments_to_questions(
        segments,
        confidence_threshold=confidence_threshold,
        min_duration_seconds=min_duration_seconds,
    )


def _segments_to_questions(
    segments: List[_Segment],
    *,
    confidence_threshold: float,
    min_duration_seconds: float,
) -> List[DetectedQuestion]:
    """Finalise accumulated segments into :class:`DetectedQuestion` records."""
    questions: List[DetectedQuestion] = []
    for segment in segments:
        start_seconds = segment.samples[0].timestamp
        end_seconds = segment.samples[-1].timestamp
        duration = end_seconds - start_seconds

        if duration < min_duration_seconds:
            log.info(
                "Skipping transient detection (%.2fs < %.2fs): %r",
                duration, min_duration_seconds, segment.best_text,
            )
            continue

        confidence = round(segment.mean_confidence, 3)
        needs_review = confidence < confidence_threshold
        question = DetectedQuestion(
            question=segment.best_text,
            start=format_timestamp(start_seconds),
            end=format_timestamp(end_seconds),
            confidence=confidence,
            start_seconds=round(start_seconds, 3),
            end_seconds=round(end_seconds, 3),
            needs_review=needs_review,
        )
        questions.append(question)
        log.info(
            "Question detected [%s -> %s] conf=%.2f%s: %r",
            question.start, question.end, confidence,
            " (NEEDS REVIEW)" if needs_review else "", question.question,
        )
    return questions


# --- OpenCV frame sampling -----------------------------------------------------


def _crop_region(frame: Any, region: Optional[Region]) -> Any:
    """Crop a frame to a fractional region of interest, if configured."""
    if region is None:
        return frame
    fx, fy, fw, fh = region
    height, width = frame.shape[:2]
    x1 = max(0, int(fx * width))
    y1 = max(0, int(fy * height))
    x2 = min(width, int((fx + fw) * width))
    y2 = min(height, int((fy + fh) * height))
    if x2 <= x1 or y2 <= y1:
        return frame
    return frame[y1:y2, x1:x2]


def iter_frame_samples(
    video_path: Path,
    interval_seconds: float,
    region: Optional[Region] = None,
) -> Iterator[Tuple[float, Any]]:
    """Yield ``(timestamp_seconds, frame)`` pairs sampled at a fixed interval.

    Frames are seeked by index (not decoded sequentially) so only the sampled
    frames are read. OpenCV is imported lazily.

    Args:
        video_path: Path to the video file.
        interval_seconds: Seconds between sampled frames.
        region: Optional fractional ROI to crop each frame to.

    Yields:
        Tuples of timestamp (seconds) and the (optionally cropped) frame.

    Raises:
        OCRDetectionError: If the video cannot be opened.
    """
    import cv2  # lazy: heavy import only when actually sampling

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise OCRDetectionError(f"Could not open video: {video_path}")

    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 0.0
        total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if fps <= 0:
            raise OCRDetectionError(f"Invalid FPS reported for {video_path}")

        step = max(1, int(round(interval_seconds * fps)))
        index = 0
        while total_frames == 0 or index < total_frames:
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            yield index / fps, _crop_region(frame, region)
            index += step
    finally:
        capture.release()


# --- PaddleOCR engine (default backend) ----------------------------------------


class PaddleOCREngine:
    """Default OCR backend using PaddleOCR (lazily initialised)."""

    def __init__(self, language: str = "en") -> None:
        """Store configuration; the model is loaded on first use.

        Args:
            language: PaddleOCR language code.
        """
        self._language = language
        self._ocr = None  # loaded on first read()

    def _ensure_model(self) -> None:
        """Instantiate the PaddleOCR model on first use."""
        if self._ocr is not None:
            return
        try:
            from paddleocr import PaddleOCR  # lazy: heavy optional dependency
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise OCRDetectionError(
                "PaddleOCR is not installed. Install it (see requirements.txt) "
                "or inject a different OCREngine."
            ) from exc
        self._ocr = PaddleOCR(use_angle_cls=True, lang=self._language, show_log=False)

    def read(self, image: Any) -> OCRReading:
        """Recognise text in ``image`` and aggregate lines into one reading."""
        self._ensure_model()
        assert self._ocr is not None
        try:
            raw = self._ocr.ocr(image, cls=True)
        except Exception as exc:  # pragma: no cover - backend runtime error
            log.warning("PaddleOCR failed on a frame: %s", exc)
            return OCRReading(text="", confidence=0.0)

        lines: List[str] = []
        confidences: List[float] = []
        # PaddleOCR returns [[ [box, (text, conf)], ... ]] per image.
        for page in raw or []:
            for entry in page or []:
                try:
                    text, conf = entry[1]
                except (IndexError, TypeError, ValueError):
                    continue
                if text:
                    lines.append(str(text))
                    confidences.append(float(conf))

        if not lines:
            return OCRReading(text="", confidence=0.0)
        mean_conf = sum(confidences) / len(confidences)
        return OCRReading(text=normalize_text(" ".join(lines)), confidence=mean_conf)


class TesseractOCREngine:
    """OCR backend using Tesseract via ``pytesseract``.

    Well suited to StreamYard's clean, high-contrast question banner: the image
    is converted to grayscale and binarised (Otsu) before recognition, which
    sharpens the black-on-white text and suppresses background. Per-word
    confidences from Tesseract are aggregated into a single reading.
    """

    def __init__(self, language: str = "eng", psm: int = 6) -> None:
        """Configure the engine.

        Args:
            language: Tesseract language code (``eng`` covers English and
                Roman-Urdu, which is written in Latin script).
            psm: Tesseract page-segmentation mode; 6 = assume a uniform block of
                text, which matches the banner well.
        """
        self._language = language
        self._config = f"--psm {psm}"
        self._checked = False

    def _ensure_available(self) -> None:
        """Verify pytesseract and the tesseract binary are usable."""
        if self._checked:
            return
        try:
            import pytesseract  # lazy: optional dependency
            pytesseract.get_tesseract_version()
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise OCRDetectionError(
                "pytesseract is not installed. Run: pip install pytesseract"
            ) from exc
        except Exception as exc:  # pragma: no cover - binary missing/misconfigured
            raise OCRDetectionError(
                "Tesseract binary not found. Install it (e.g. brew install tesseract)."
            ) from exc
        self._checked = True

    def _preprocess(self, image: Any) -> Any:
        """Grayscale + Otsu-threshold the image to sharpen banner text."""
        import cv2

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return binary

    def read(self, image: Any) -> OCRReading:
        """Recognise text in ``image`` and aggregate words into one reading."""
        self._ensure_available()
        import pytesseract

        try:
            processed = self._preprocess(image)
            data = pytesseract.image_to_data(
                processed,
                lang=self._language,
                config=self._config,
                output_type=pytesseract.Output.DICT,
            )
        except Exception as exc:  # pragma: no cover - backend runtime error
            log.warning("Tesseract failed on a frame: %s", exc)
            return OCRReading(text="", confidence=0.0)

        words: List[str] = []
        confidences: List[float] = []
        for word, conf in zip(data.get("text", []), data.get("conf", [])):
            try:
                conf_val = float(conf)
            except (TypeError, ValueError):
                continue
            token = word.strip()
            if token and conf_val >= 0:
                words.append(token)
                confidences.append(conf_val / 100.0)  # Tesseract reports 0-100

        if not words:
            return OCRReading(text="", confidence=0.0)
        mean_conf = sum(confidences) / len(confidences)
        return OCRReading(text=normalize_text(" ".join(words)), confidence=mean_conf)


# --- Configuration + orchestration ---------------------------------------------


def _read_ocr_config(config: Config) -> Dict[str, Any]:
    """Extract OCR settings with safe defaults."""
    ocr_cfg = config.settings.get("ocr", {})
    region_raw = ocr_cfg.get("region")
    region: Optional[Region] = None
    if region_raw:
        if len(region_raw) != 4:
            log.warning("ocr.region must have 4 values [x,y,w,h]; ignoring: %r", region_raw)
        else:
            region = tuple(float(v) for v in region_raw)  # type: ignore[assignment]

    return {
        "interval": float(ocr_cfg.get("frame_interval_seconds", 1)),
        "confidence_threshold": float(ocr_cfg.get("confidence_threshold", 0.80)),
        "min_duration": float(ocr_cfg.get("min_question_duration_seconds", 2)),
        "similarity_threshold": float(ocr_cfg.get("text_similarity_threshold", 0.6)),
        "language": str(ocr_cfg.get("language", "en")),
        "region": region,
    }


def ocr_frames(
    video_path: Path,
    engine: OCREngine,
    interval_seconds: float,
    region: Optional[Region],
) -> List[FrameSample]:
    """Sample and OCR every frame at the given interval.

    Args:
        video_path: Video to analyse.
        engine: OCR backend.
        interval_seconds: Sampling interval.
        region: Optional fractional ROI.

    Returns:
        Frame samples in timestamp order.
    """
    samples: List[FrameSample] = []
    for timestamp, frame in iter_frame_samples(video_path, interval_seconds, region):
        reading = engine.read(frame)
        samples.append(
            FrameSample(
                timestamp=timestamp,
                text=normalize_text(reading.text),
                confidence=reading.confidence,
            )
        )
    log.info("Sampled and OCR'd %d frames from %s", len(samples), video_path.name)
    return samples


def write_questions(questions: List[DetectedQuestion], config: Config) -> Path:
    """Write the detected questions to ``questions.json`` in the output folder.

    Args:
        questions: Detected questions to serialise.
        config: Application configuration (provides the output folder).

    Returns:
        Path to the written JSON file.
    """
    config.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = config.output_dir / QUESTIONS_FILENAME
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump([q.to_dict() for q in questions], handle, indent=2)
    return output_path


def detect_questions(
    config: Config,
    engine: Optional[OCREngine] = None,
    video_path: Optional[Path] = None,
) -> List[DetectedQuestion]:
    """Detect StreamYard question overlays and write ``questions.json``.

    Public entry point, called by the pipeline's OCR stage.

    Args:
        config: Application configuration.
        engine: OCR backend; defaults to :class:`PaddleOCREngine`. Injectable for
            testing and for swapping OCR backends.
        video_path: Optional explicit video path; defaults to resolving the input
            folder via the Video Analyzer's resolver.

    Returns:
        The detected questions.

    Raises:
        OCRDetectionError: On failure opening the video or performing OCR.
    """
    # Reuse the analyzer's config-driven input resolution to stay DRY.
    from modules.video.analyzer import resolve_input_video

    source = resolve_input_video(config, video_path)
    params = _read_ocr_config(config)
    ocr_engine = engine or PaddleOCREngine(language=params["language"])

    log.info(
        "Detecting questions in %s (interval=%.2fs, region=%s)",
        source.name, params["interval"], params["region"],
    )

    samples = ocr_frames(source, ocr_engine, params["interval"], params["region"])
    questions = build_timeline(
        samples,
        confidence_threshold=params["confidence_threshold"],
        min_duration_seconds=params["min_duration"],
        similarity_threshold=params["similarity_threshold"],
    )

    output_path = write_questions(questions, config)
    flagged = sum(1 for q in questions if q.needs_review)
    log.info(
        "Detected %d question(s) (%d flagged for review) -> %s",
        len(questions), flagged, output_path,
    )
    return questions
