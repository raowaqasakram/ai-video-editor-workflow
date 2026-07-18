"""Pipeline orchestration skeleton for AI Content Studio.

Defines the ordered processing stages from ARCHITECTURE.md (§4) and runs them in
sequence. In Phase 0 every stage is a logged no-op stub: the orchestration
contract, ordering, and per-stage error isolation exist now, and later phases
replace each stub with a real implementation (OCR, clipping, Whisper, etc.).

Error handling follows ARCHITECTURE.md (§19): a stage failure is logged and, for
the full pipeline, does not abort the remaining stages.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List

from app.config_loader import Config
from app.logger import get_logger

log = get_logger(__name__)

# A stage is a callable that receives the validated config and performs one step.
StageFunc = Callable[[Config], None]


@dataclass(frozen=True)
class Stage:
    """A single named pipeline step.

    Attributes:
        name: Human-readable stage name used in logs and CLI selection.
        run: The callable that executes the stage.
    """

    name: str
    run: StageFunc


# --- Stage stubs ---------------------------------------------------------------
# Each stub only logs for now. Signatures are stable so Phase 1+ can swap in real
# logic without touching the orchestrator. None of these implement OCR or video
# processing yet — that is deliberate for Phase 0.


def _stage_analyze(config: Config) -> None:
    """Inspect the input video and emit ``video_info.json`` (Phase 1)."""
    # Imported lazily so unrelated commands never pay for the video module.
    from modules.video.analyzer import analyze_video

    analyze_video(config)


def _stage_ocr(config: Config) -> None:
    """Detect StreamYard question overlays into ``questions.json`` (Phase 2)."""
    # Imported lazily so unrelated commands never pay for the OCR/CV stack.
    from modules.ocr.detector import detect_questions

    detect_questions(config)


def _stage_clips(config: Config) -> None:
    """Extract per-question clips with FFmpeg (Phase 3)."""
    log.info("[clips] not yet implemented (Phase 3: Clip Extraction)")


def _stage_transcribe(config: Config) -> None:
    """Generate transcript and subtitles with Whisper (Phase 4)."""
    log.info("[transcribe] not yet implemented (Phase 4: Speech Intelligence)")


def _stage_hooks(config: Config) -> None:
    """Find the strongest opening moment for each clip (Phase 5)."""
    log.info("[hooks] not yet implemented (Phase 5: Hook Optimization)")


def _stage_captions(config: Config) -> None:
    """Render professional branded captions (Phase 6)."""
    log.info("[captions] not yet implemented (Phase 6: Caption Engine)")


def _stage_enhance(config: Config) -> None:
    """Apply audio/video enhancement (Phase 7)."""
    log.info("[enhance] not yet implemented (Phase 7: Video Enhancement)")


def _stage_social(config: Config) -> None:
    """Create 1080x1920 vertical versions (Phase 8)."""
    log.info("[social] not yet implemented (Phase 8: Vertical Video Generator)")


def _stage_metadata(config: Config) -> None:
    """Generate titles, descriptions, hashtags, thumbnail text (Phase 9)."""
    log.info("[metadata] not yet implemented (Phase 9: AI Metadata Generator)")


def _stage_scoring(config: Config) -> None:
    """Score clips for prioritisation (Phase 10)."""
    log.info("[scoring] not yet implemented (Phase 10: Content Scoring)")


def _stage_dashboard(config: Config) -> None:
    """Generate the review dashboard (Phase 11)."""
    log.info("[dashboard] not yet implemented (Phase 11: Review Dashboard)")


# Ordered full pipeline. Mirrors ARCHITECTURE.md §4 top-to-bottom.
PIPELINE: List[Stage] = [
    Stage("analyze", _stage_analyze),
    Stage("ocr", _stage_ocr),
    Stage("clips", _stage_clips),
    Stage("transcribe", _stage_transcribe),
    Stage("hooks", _stage_hooks),
    Stage("captions", _stage_captions),
    Stage("enhance", _stage_enhance),
    Stage("social", _stage_social),
    Stage("metadata", _stage_metadata),
    Stage("scoring", _stage_scoring),
    Stage("dashboard", _stage_dashboard),
]

# Lookup for running an individual stage by name (used by targeted CLI commands).
_STAGE_BY_NAME = {stage.name: stage for stage in PIPELINE}


def run_stage(name: str, config: Config) -> None:
    """Run a single named stage.

    Args:
        name: The stage name (see :data:`PIPELINE`).
        config: Loaded application configuration.

    Raises:
        KeyError: If ``name`` is not a known stage.
    """
    stage = _STAGE_BY_NAME[name]
    log.info("Running stage: %s", stage.name)
    stage.run(config)


def run_pipeline(config: Config) -> int:
    """Run all stages in order with per-stage error isolation.

    A stage that raises is logged and skipped so remaining stages still run,
    per ARCHITECTURE.md §19.

    Args:
        config: Loaded application configuration.

    Returns:
        The number of stages that failed (0 means a clean run).
    """
    log.info("Starting full pipeline (%d stages)", len(PIPELINE))
    failures = 0
    for stage in PIPELINE:
        try:
            log.info("Running stage: %s", stage.name)
            stage.run(config)
        except Exception:  # noqa: BLE001 - deliberately continue on any stage error
            failures += 1
            log.exception("Stage '%s' failed; continuing with remaining stages", stage.name)

    if failures:
        log.warning("Pipeline finished with %d failed stage(s)", failures)
    else:
        log.info("Pipeline finished successfully")
    return failures
