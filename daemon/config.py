"""Daemon configuration: defaults < $STATE_DIR/config.json < env vars.

STATE_DIR resolution order (first wins):
  1. $BUDDY_CLI_HOME            — explicit override
  2. $CLAUDE_PLUGIN_DATA        — set by Claude Code when running as a plugin
                                  (resolves to ~/.claude/plugins/data/<plugin-id>/)
  3. <repo>/.dev-state/         — fallback for `python -m plugin.daemon.daemon`
                                  invocations during development
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


def _repo_dev_state() -> Path:
    # plugin/daemon/config.py → parents[2] = <repo root>
    return Path(__file__).resolve().parents[2] / ".dev-state"


STATE_DIR = Path(
    os.environ.get("BUDDY_CLI_HOME")
    or os.environ.get("CLAUDE_PLUGIN_DATA")
    or str(_repo_dev_state())
)
LOG_DIR = STATE_DIR / "logs"
RUN_DIR = STATE_DIR / "run"
CONFIG_PATH = STATE_DIR / "config.json"
LOG_PATH = LOG_DIR / "daemon.log"
PID_PATH = RUN_DIR / "daemon.pid"


@dataclass
class Config:
    http_port: int = 9876
    decision_timeout_s: float = 30.0
    heartbeat_interval_s: float = 1.0
    inter_write_gap_s: float = 0.2
    ble_max_queue: int = 50
    ble_reconnect_min_s: float = 1.0
    ble_reconnect_max_s: float = 30.0
    owner_name: Optional[str] = None
    log_level: str = "INFO"


def _load_file_overrides(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open() as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _env_overrides() -> dict:
    out: dict = {}
    if (v := os.environ.get("BUDDY_CLI_PORT")):
        try:
            out["http_port"] = int(v)
        except ValueError:
            pass
    if (v := os.environ.get("BUDDY_CLI_DECISION_TIMEOUT")):
        try:
            out["decision_timeout_s"] = float(v)
        except ValueError:
            pass
    if (v := os.environ.get("BUDDY_CLI_OWNER")):
        out["owner_name"] = v
    if (v := os.environ.get("BUDDY_CLI_LOG_LEVEL")):
        out["log_level"] = v
    return out


def load() -> Config:
    cfg = Config()
    overrides = _load_file_overrides(CONFIG_PATH)
    overrides.update(_env_overrides())
    valid_keys = set(asdict(cfg).keys())
    for k, v in overrides.items():
        if k in valid_keys:
            setattr(cfg, k, v)
    return cfg


def ensure_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
