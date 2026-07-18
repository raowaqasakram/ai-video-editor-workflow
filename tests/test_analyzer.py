"""Tests for the Video Analyzer module (Phase 1).

The pure parsing logic is tested against sample ffprobe JSON so no media files
are required. A single end-to-end test generates a real 1-second clip with
FFmpeg and is skipped automatically when FFmpeg is unavailable.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict

import pytest

from app.config_loader import Config
from modules.video.analyzer import (
    VIDEO_INFO_FILENAME,
    VideoAnalyzerError,
    VideoInfo,
    _parse_fps,
    analyze_video,
    parse_probe,
    resolve_input_video,
    write_video_info,
)

# --- Fixtures ------------------------------------------------------------------


def _make_config(tmp_path: Path) -> Config:
    """Build a Config rooted at a temp directory with standard storage folders."""
    settings: Dict[str, Any] = {
        "processing": {"mode": "test"},
        "video": {"input_format": "mp4"},
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


SAMPLE_PROBE: Dict[str, Any] = {
    "streams": [
        {
            "codec_type": "video",
            "codec_name": "h264",
            "width": 1920,
            "height": 1080,
            "avg_frame_rate": "30/1",
            "r_frame_rate": "30/1",
        },
        {"codec_type": "audio", "codec_name": "aac"},
    ],
    "format": {"duration": "5400.000000"},
}


# --- Pure parsing ---------------------------------------------------------------


@pytest.mark.parametrize(
    "rate, expected",
    [
        ("30/1", 30.0),
        ("30000/1001", 29.97),
        ("25", 25.0),
        ("0/0", 0.0),
        ("", 0.0),
        (None, 0.0),
        ("bad", 0.0),
    ],
)
def test_parse_fps(rate, expected):
    assert _parse_fps(rate) == expected


def test_parse_probe_full_metadata():
    info = parse_probe(SAMPLE_PROBE, Path("/videos/livestream.mp4"))
    assert isinstance(info, VideoInfo)
    assert info.width == 1920
    assert info.height == 1080
    assert info.resolution == "1920x1080"
    assert info.fps == 30.0
    assert info.duration == 5400.0
    assert info.codec == "h264"
    assert info.has_audio is True
    assert info.path == "/videos/livestream.mp4"


def test_parse_probe_without_audio():
    probe = {"streams": [SAMPLE_PROBE["streams"][0]], "format": {"duration": "10"}}
    info = parse_probe(probe, Path("silent.mp4"))
    assert info.has_audio is False
    assert info.duration == 10.0


def test_parse_probe_no_video_stream_raises():
    probe = {"streams": [{"codec_type": "audio", "codec_name": "aac"}], "format": {}}
    with pytest.raises(VideoAnalyzerError, match="No video stream"):
        parse_probe(probe, Path("audio_only.m4a"))


def test_video_info_to_dict_is_json_serialisable():
    info = parse_probe(SAMPLE_PROBE, Path("x.mp4"))
    dumped = json.dumps(info.to_dict())
    assert "1920x1080" in dumped


# --- Input resolution -----------------------------------------------------------


def test_resolve_input_video_finds_file_in_input_folder(tmp_path):
    config = _make_config(tmp_path)
    video = config.input_dir / "livestream.mp4"
    video.write_bytes(b"fake")
    assert resolve_input_video(config) == video


def test_resolve_input_video_missing_raises(tmp_path):
    config = _make_config(tmp_path)
    with pytest.raises(VideoAnalyzerError, match="No '\\*.mp4' video"):
        resolve_input_video(config)


def test_resolve_input_video_explicit_path(tmp_path):
    config = _make_config(tmp_path)
    custom = tmp_path / "elsewhere.mp4"
    custom.write_bytes(b"fake")
    assert resolve_input_video(config, custom) == custom


def test_resolve_input_video_explicit_missing_raises(tmp_path):
    config = _make_config(tmp_path)
    with pytest.raises(VideoAnalyzerError, match="Input video not found"):
        resolve_input_video(config, tmp_path / "nope.mp4")


# --- Output writing -------------------------------------------------------------


def test_write_video_info_creates_json(tmp_path):
    config = _make_config(tmp_path)
    info = parse_probe(SAMPLE_PROBE, Path("x.mp4"))
    out = write_video_info(info, config)
    assert out == config.output_dir / VIDEO_INFO_FILENAME
    data = json.loads(out.read_text())
    assert data["resolution"] == "1920x1080"
    assert data["has_audio"] is True


# --- End-to-end (requires FFmpeg) ----------------------------------------------


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg not installed")
def test_analyze_video_end_to_end(tmp_path):
    config = _make_config(tmp_path)
    video = config.input_dir / "livestream.mp4"
    # Generate a deterministic 1-second 320x240 25fps clip with a silent track.
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=1",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=mono:sample_rate=44100",
            "-shortest", "-pix_fmt", "yuv420p",
            str(video),
        ],
        check=True,
        capture_output=True,
    )

    info = analyze_video(config)
    assert info.width == 320
    assert info.height == 240
    assert info.resolution == "320x240"
    assert info.fps == 25.0
    assert 0.9 <= info.duration <= 1.2
    assert info.has_audio is True
    assert (config.output_dir / VIDEO_INFO_FILENAME).is_file()
