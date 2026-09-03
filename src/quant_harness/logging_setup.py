"""Logging setup for unattended runs.

Cron output goes to logs/daily.log; timestamps and levels make "did the
21:05 run actually fetch, and when" answerable without guessing. Library
modules log via `get_logger(__name__)`; the CLI configures the handlers.
"""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def configure_logging(verbose: bool = False) -> None:
    """Configure root handlers once: INFO to stderr for humans, and let cron
    redirection capture the same stream with timestamps and levels."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    root = logging.getLogger("quant_harness")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.addHandler(handler)
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"quant_harness.{name}")
