"""Logging configuration shared by the GUI, daemon, and CLI tools.

The daemon runs under systemd, so plain stdout/stderr lines land in the
journal. The GUI also gets stderr output. Keep formatting minimal so
``journalctl`` doesn't double-stamp the lines.
"""

from __future__ import annotations

import logging
import os
import sys

_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"
_DATEFMT = "%H:%M:%S"


def configure(component: str, *, level: int | str | None = None) -> logging.Logger:
    """Initialize logging for one of {"gui", "daemon", "cli"}.

    Idempotent: repeated calls reconfigure the root logger's handlers
    rather than appending duplicates, so unit tests and re-entry are safe.
    """
    if level is None:
        level = os.environ.get("G915X_LOG_LEVEL", "INFO").upper()

    root = logging.getLogger()
    root.setLevel(level)

    # Drop any handlers we added previously so successive calls don't
    # multiply log output.
    for handler in list(root.handlers):
        if getattr(handler, "_g915x_owned", False):
            root.removeHandler(handler)

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
    handler._g915x_owned = True  # type: ignore[attr-defined]
    root.addHandler(handler)

    return logging.getLogger(f"g915x.{component}")
