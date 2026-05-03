"""Rotating-file + stderr logger configuration."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure(log_path: Path, level: str = "INFO") -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

    fh = RotatingFileHandler(log_path, maxBytes=1_048_576, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(fh)
    root.addHandler(sh)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
