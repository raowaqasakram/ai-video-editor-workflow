"""Logging framework for AI Content Studio.

Provides a single, config-driven setup function that all modules share. Logs are
written to both the console and ``logs/processing.log`` (path and on/off state
come from ``settings.yaml``), satisfying the logging requirements in
CONFIGURATION.md and ARCHITECTURE.md (§19): every module must log with
timestamps, module name, level, and message.

Usage:
    from app.logger import setup_logging, get_logger

    setup_logging(config)              # once, at application start
    log = get_logger(__name__)         # in any module
    log.info("Started processing")
"""

from __future__ import annotations

import logging
from logging import Logger
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid a runtime import cycle; only needed for type checking
    from app.config_loader import Config

# Root logger name for the whole application. Module loggers are created as
# children (e.g. ``ai_content_studio.ocr.detector``) so a single configuration
# controls handlers and level for the entire tree.
ROOT_LOGGER_NAME = "ai_content_studio"

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Guards against attaching duplicate handlers if setup is called more than once.
_configured = False


def setup_logging(config: "Config") -> Logger:
    """Configure the application-wide logger from validated configuration.

    Idempotent: repeated calls reconfigure the same root logger without stacking
    duplicate handlers.

    Args:
        config: Loaded :class:`~app.config_loader.Config`. Reads the ``logging``
            section for ``enabled``, ``file``, and ``level``.

    Returns:
        The configured application root logger.
    """
    global _configured

    logging_cfg = config.settings.get("logging", {})
    enabled = logging_cfg.get("enabled", True)
    level_name = str(logging_cfg.get("level", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)

    logger = logging.getLogger(ROOT_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False  # keep our records out of the Python root logger

    # Clear existing handlers so reconfiguration is clean and never duplicates.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    formatter = logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # Console handler is always present so the user sees progress.
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler is added only when logging to file is enabled in config.
    if enabled:
        config.log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(config.log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    _configured = True
    logger.debug("Logging configured (level=%s, file=%s)", level_name, config.log_file)
    return logger


def get_logger(name: str | None = None) -> Logger:
    """Return a module-scoped child of the application logger.

    Args:
        name: Usually ``__name__`` of the calling module. When ``None`` the
            application root logger is returned.

    Returns:
        A :class:`logging.Logger` whose records flow through the shared handlers.
    """
    if name is None or name == ROOT_LOGGER_NAME:
        return logging.getLogger(ROOT_LOGGER_NAME)
    return logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}")
