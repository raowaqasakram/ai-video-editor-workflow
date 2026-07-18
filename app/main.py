"""Command-line entry point for AI Content Studio.

Wires together configuration loading, logging setup, and the processing
pipeline behind the subcommands documented in README.md:

    process    Run the full pipeline (all stages)
    analyze    Inspect the input video only (Phase 1)
    ocr        Detect StreamYard questions (Phase 2)
    clips      Extract per-question clips (Phase 3)
    social     Create vertical social versions (Phase 8)
    metadata   Generate titles/descriptions/hashtags (Phase 9)

In Phase 0 the stages are no-op stubs; this file provides the stable CLI surface
so ``python app/main.py --help`` works and later phases only fill in behaviour.

Run from the project root:
    python app/main.py <command>
    python app/main.py --help
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

# Support both ``python app/main.py`` (script) and ``python -m app.main`` (module)
# by ensuring the project root is importable before importing app packages.
if __package__ in (None, ""):
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config_loader import Config, ConfigError, load_config
from app.logger import get_logger, setup_logging
from app.pipeline import run_pipeline, run_stage

# Maps CLI command -> the pipeline stage it triggers. ``process`` is handled
# separately because it runs the whole pipeline rather than a single stage.
_COMMAND_TO_STAGE = {
    "analyze": "analyze",
    "ocr": "ocr",
    "clips": "clips",
    "social": "social",
    "metadata": "metadata",
}


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser with all supported subcommands.

    Returns:
        A fully configured :class:`argparse.ArgumentParser`.
    """
    parser = argparse.ArgumentParser(
        prog="ai-content-studio",
        description="Convert StreamYard livestreams into short-form developer content.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="command")

    commands = {
        "process": "Run the full processing pipeline (all stages).",
        "analyze": "Analyze the input video and emit video metadata (Phase 1).",
        "ocr": "Detect StreamYard audience questions (Phase 2).",
        "clips": "Extract per-question clips (Phase 3).",
        "social": "Create vertical 1080x1920 social versions (Phase 8).",
        "metadata": "Generate titles, descriptions, and hashtags (Phase 9).",
    }
    for name, help_text in commands.items():
        subparsers.add_parser(name, help=help_text, description=help_text)

    return parser


def _dispatch(command: str, config: Config) -> int:
    """Route a parsed command to the pipeline.

    Args:
        command: The validated subcommand name.
        config: Loaded application configuration.

    Returns:
        Process exit code (0 on success, non-zero on stage failures).
    """
    if command == "process":
        return 1 if run_pipeline(config) else 0

    stage_name = _COMMAND_TO_STAGE[command]
    run_stage(stage_name, config)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """Application entry point.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``). Injectable
            for testing.

    Returns:
        Process exit code.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    # Load and validate configuration before doing anything else.
    try:
        config = load_config()
    except ConfigError as exc:
        # Logging is not configured yet, so report the failure directly.
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    # Ensure working directories exist, then bring up logging.
    config.ensure_directories()
    setup_logging(config)
    log = get_logger(__name__)

    log.info("AI Content Studio starting (command=%s)", args.command)
    try:
        exit_code = _dispatch(args.command, config)
    except Exception:  # noqa: BLE001 - top-level guard: log and fail cleanly
        log.exception("Unexpected error while running command '%s'", args.command)
        return 1

    log.info("AI Content Studio finished (command=%s, exit=%d)", args.command, exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
