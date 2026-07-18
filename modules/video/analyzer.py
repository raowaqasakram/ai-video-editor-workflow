"""Video Analyzer module (Phase 1).

Reads an input livestream video and extracts its technical metadata using
``ffprobe`` (part of the FFmpeg toolchain, a documented system requirement).
The result is written to ``video_info.json`` in the configured output folder.

Design notes:
    * ``ffprobe`` is invoked over a subprocess and asked for JSON, so no extra
      Python dependency is introduced.
    * Probe execution (:func:`_run_ffprobe`) and parsing (:func:`parse_probe`)
      are kept separate: parsing is pure and unit-testable without any media.
    * All paths come from configuration (ARCHITECTURE.md §18); nothing is
      hardcoded.

See ARCHITECTURE.md §5 for the module contract.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from app.config_loader import Config
from app.logger import get_logger

log = get_logger(__name__)

# Name of the metadata file produced by this module.
VIDEO_INFO_FILENAME = "video_info.json"


class VideoAnalyzerError(Exception):
    """Raised when a video cannot be located, probed, or parsed."""


@dataclass(frozen=True)
class VideoInfo:
    """Technical metadata extracted from an input video.

    Attributes:
        path: Absolute path to the analysed video file.
        duration: Duration in seconds (float).
        width: Frame width in pixels.
        height: Frame height in pixels.
        resolution: Convenience ``"{width}x{height}"`` string.
        fps: Frames per second (float).
        codec: Video stream codec name (e.g. ``"h264"``), or ``None`` if unknown.
        has_audio: Whether the file contains at least one audio stream.
    """

    path: str
    duration: float
    width: int
    height: int
    resolution: str
    fps: float
    codec: Optional[str]
    has_audio: bool

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable dictionary of the metadata."""
        return asdict(self)


def _parse_fps(rate: Optional[str]) -> float:
    """Convert an ffprobe frame-rate string (e.g. ``"30000/1001"``) to a float.

    Args:
        rate: The ``r_frame_rate`` / ``avg_frame_rate`` value, or ``None``.

    Returns:
        Frames per second, rounded to 3 decimals; ``0.0`` when unavailable or
        malformed (e.g. ffprobe's ``"0/0"``).
    """
    if not rate:
        return 0.0
    try:
        if "/" in rate:
            numerator, denominator = rate.split("/", 1)
            denom = float(denominator)
            if denom == 0:
                return 0.0
            return round(float(numerator) / denom, 3)
        return round(float(rate), 3)
    except (ValueError, ZeroDivisionError):
        return 0.0


def parse_probe(probe: Dict[str, Any], source_path: Path) -> VideoInfo:
    """Build a :class:`VideoInfo` from raw ffprobe JSON output.

    Pure and side-effect free so it can be unit-tested without any media files.

    Args:
        probe: Parsed ffprobe JSON (``format`` + ``streams``).
        source_path: Path of the analysed file, recorded on the result.

    Returns:
        A populated :class:`VideoInfo`.

    Raises:
        VideoAnalyzerError: If no video stream is present.
    """
    streams = probe.get("streams", [])
    fmt = probe.get("format", {})

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video_stream is None:
        raise VideoAnalyzerError(f"No video stream found in {source_path}")

    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    width = int(video_stream.get("width", 0) or 0)
    height = int(video_stream.get("height", 0) or 0)

    # Duration can live on the stream or the container; prefer the container.
    duration_raw = fmt.get("duration") or video_stream.get("duration") or 0.0
    try:
        duration = round(float(duration_raw), 3)
    except (TypeError, ValueError):
        duration = 0.0

    # Prefer average frame rate; fall back to the base rate.
    fps = _parse_fps(video_stream.get("avg_frame_rate")) or _parse_fps(
        video_stream.get("r_frame_rate")
    )

    return VideoInfo(
        path=str(source_path),
        duration=duration,
        width=width,
        height=height,
        resolution=f"{width}x{height}",
        fps=fps,
        codec=video_stream.get("codec_name"),
        has_audio=has_audio,
    )


def _run_ffprobe(video_path: Path) -> Dict[str, Any]:
    """Run ``ffprobe`` on a file and return its parsed JSON output.

    Args:
        video_path: Absolute path to the video file.

    Returns:
        The parsed ffprobe JSON mapping.

    Raises:
        VideoAnalyzerError: If ffprobe is missing, fails, or emits invalid JSON.
    """
    if shutil.which("ffprobe") is None:
        raise VideoAnalyzerError(
            "ffprobe not found on PATH. Install FFmpeg (see README.md requirements)."
        )

    command = [
        "ffprobe",
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(video_path),
    ]
    log.debug("Running ffprobe: %s", " ".join(command))
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise VideoAnalyzerError(
            f"ffprobe failed for {video_path}: {exc.stderr.strip()}"
        ) from exc

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise VideoAnalyzerError(f"Could not parse ffprobe output for {video_path}: {exc}") from exc


def resolve_input_video(config: Config, video_path: Optional[Path] = None) -> Path:
    """Determine which video file to analyse.

    Args:
        config: Application configuration (provides the input folder).
        video_path: Explicit path to a video; when ``None`` the configured input
            folder is searched for a file matching the configured input format.

    Returns:
        Absolute path to the video to analyse.

    Raises:
        VideoAnalyzerError: If no suitable video is found or the path is invalid.
    """
    if video_path is not None:
        candidate = Path(video_path)
        if not candidate.is_absolute():
            candidate = config.project_root / candidate
        if not candidate.is_file():
            raise VideoAnalyzerError(f"Input video not found: {candidate}")
        return candidate

    input_dir = config.input_dir
    if not input_dir.is_dir():
        raise VideoAnalyzerError(f"Input folder does not exist: {input_dir}")

    extension = str(config.settings.get("video", {}).get("input_format", "mp4")).lstrip(".")
    matches = sorted(p for p in input_dir.glob(f"*.{extension}") if p.is_file())
    if not matches:
        raise VideoAnalyzerError(
            f"No '*.{extension}' video found in {input_dir}. "
            f"Place your livestream there (e.g. {input_dir / ('livestream.' + extension)})."
        )
    if len(matches) > 1:
        log.warning("Multiple input videos found; using the first: %s", matches[0].name)
    return matches[0]


def write_video_info(info: VideoInfo, config: Config) -> Path:
    """Write video metadata to ``video_info.json`` in the output folder.

    Args:
        info: The metadata to serialise.
        config: Application configuration (provides the output folder).

    Returns:
        Path to the written JSON file.
    """
    config.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = config.output_dir / VIDEO_INFO_FILENAME
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(info.to_dict(), handle, indent=2)
    return output_path


def analyze_video(config: Config, video_path: Optional[Path] = None) -> VideoInfo:
    """Analyse an input video and persist its metadata to ``video_info.json``.

    This is the module's public entry point, called by the pipeline's analyze
    stage.

    Args:
        config: Application configuration.
        video_path: Optional explicit path to the video; defaults to searching
            the configured input folder.

    Returns:
        The extracted :class:`VideoInfo`.

    Raises:
        VideoAnalyzerError: On any failure locating, probing, or parsing.
    """
    source = resolve_input_video(config, video_path)
    log.info("Analyzing video: %s", source)

    probe = _run_ffprobe(source)
    info = parse_probe(probe, source)

    output_path = write_video_info(info, config)
    log.info(
        "Video analyzed: %s, %.2fs, %.3f fps, codec=%s, audio=%s -> %s",
        info.resolution,
        info.duration,
        info.fps,
        info.codec,
        info.has_audio,
        output_path,
    )
    return info
