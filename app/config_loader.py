"""Configuration loader for AI Content Studio.

This module is the single source of truth for configuration. It loads the two
YAML files under ``config/`` (``settings.yaml`` and ``branding.yaml``),
validates that required keys are present, and resolves every filesystem path
relative to the project root.

No other module should read YAML or construct paths directly. This enforces the
configuration-driven design mandated by ARCHITECTURE.md (§18) and
CONFIGURATION.md: any visual or processing change must be possible by editing
YAML files only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

import yaml

# Project root is the parent of the ``app/`` directory that contains this file.
# Derived from ``__file__`` so the application is location-independent and never
# depends on the current working directory.
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
CONFIG_DIR: Path = PROJECT_ROOT / "config"

SETTINGS_FILE: Path = CONFIG_DIR / "settings.yaml"
BRANDING_FILE: Path = CONFIG_DIR / "branding.yaml"

# Top-level keys each config file must define. Kept intentionally small: we only
# assert on structure the loader itself relies on to resolve paths and behaviour.
REQUIRED_SETTINGS_KEYS = ("processing", "video", "storage", "logging")
REQUIRED_STORAGE_KEYS = ("input_folder", "output_folder", "approved_folder")
REQUIRED_BRANDING_KEYS = ("creator", "brand")


class ConfigError(Exception):
    """Raised when configuration is missing, unreadable, or invalid."""


@dataclass(frozen=True)
class Config:
    """Immutable, validated view of the application configuration.

    Attributes:
        settings: Parsed contents of ``config/settings.yaml``.
        branding: Parsed contents of ``config/branding.yaml``.
        project_root: Absolute path to the project root directory.
    """

    settings: Dict[str, Any] = field(default_factory=dict)
    branding: Dict[str, Any] = field(default_factory=dict)
    project_root: Path = PROJECT_ROOT

    # --- Resolved directories (absolute, derived from settings) -------------

    @property
    def input_dir(self) -> Path:
        """Absolute path to the input folder (livestream source videos)."""
        return self._resolve(self.settings["storage"]["input_folder"])

    @property
    def output_dir(self) -> Path:
        """Absolute path to the output folder (generated per-question clips)."""
        return self._resolve(self.settings["storage"]["output_folder"])

    @property
    def approved_dir(self) -> Path:
        """Absolute path to the approved / ready-to-upload folder."""
        return self._resolve(self.settings["storage"]["approved_folder"])

    @property
    def temp_dir(self) -> Path:
        """Absolute path to the temporary working folder.

        Falls back to ``TEMP`` when ``temp_folder`` is not set in config.
        """
        return self._resolve(self.settings["storage"].get("temp_folder", "TEMP"))

    @property
    def log_file(self) -> Path:
        """Absolute path to the processing log file."""
        return self._resolve(self.settings["logging"].get("file", "logs/processing.log"))

    def _resolve(self, relative: str) -> Path:
        """Resolve a config-relative path against the project root.

        Absolute paths in config are honoured as-is; relative paths are anchored
        to :attr:`project_root` so behaviour never depends on the caller's CWD.
        """
        candidate = Path(relative)
        return candidate if candidate.is_absolute() else self.project_root / candidate

    def ensure_directories(self) -> None:
        """Create the standard working directories if they do not exist.

        Safe to call repeatedly (idempotent). The log file's parent directory is
        created as well so logging can start immediately.
        """
        for directory in (self.input_dir, self.output_dir, self.approved_dir, self.temp_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)


def _load_yaml(path: Path) -> Dict[str, Any]:
    """Load a single YAML file into a dictionary.

    Args:
        path: Absolute path to the YAML file.

    Returns:
        The parsed mapping.

    Raises:
        ConfigError: If the file is missing, unparseable, or not a mapping.
    """
    if not path.exists():
        raise ConfigError(f"Configuration file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse YAML file {path}: {exc}") from exc

    if data is None:
        raise ConfigError(f"Configuration file is empty: {path}")
    if not isinstance(data, dict):
        raise ConfigError(f"Configuration root must be a mapping in {path}")
    return data


def _validate(settings: Dict[str, Any], branding: Dict[str, Any]) -> None:
    """Validate that required top-level keys exist in the loaded config.

    Raises:
        ConfigError: If any required key is missing.
    """
    for key in REQUIRED_SETTINGS_KEYS:
        if key not in settings:
            raise ConfigError(f"Missing required key '{key}' in settings.yaml")

    storage = settings.get("storage", {})
    for key in REQUIRED_STORAGE_KEYS:
        if key not in storage:
            raise ConfigError(f"Missing required key 'storage.{key}' in settings.yaml")

    for key in REQUIRED_BRANDING_KEYS:
        if key not in branding:
            raise ConfigError(f"Missing required key '{key}' in branding.yaml")


def load_config(
    settings_file: Path = SETTINGS_FILE,
    branding_file: Path = BRANDING_FILE,
) -> Config:
    """Load and validate the full application configuration.

    Args:
        settings_file: Path to the settings YAML (defaults to config/settings.yaml).
        branding_file: Path to the branding YAML (defaults to config/branding.yaml).

    Returns:
        A validated, immutable :class:`Config` instance.

    Raises:
        ConfigError: If either file is missing, invalid, or fails validation.
    """
    settings = _load_yaml(settings_file)
    branding = _load_yaml(branding_file)
    _validate(settings, branding)
    return Config(settings=settings, branding=branding, project_root=PROJECT_ROOT)
