"""Application configuration: last-used profile and device options."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "g915x-rgb"
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULT_OPTIONS: dict[str, Any] = {
    "off_when_locked": True,
    "idle_minutes": 0,          # 0 = never dim
    "battery_gkeys": False,
}


def write_json(path: Path, data: Any) -> None:
    """Write JSON so that a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_config() -> dict:
    try:
        data = json.loads(CONFIG_PATH.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _update(**changes: Any) -> None:
    write_json(CONFIG_PATH, {**load_config(), **changes})


def get_last_profile() -> str | None:
    return load_config().get("last_profile")


def set_last_profile(profile_name: str) -> None:
    if get_last_profile() != profile_name:
        _update(last_profile=profile_name)


def get_options() -> dict[str, Any]:
    saved = load_config().get("options")
    saved = saved if isinstance(saved, dict) else {}
    return {k: saved[k] if type(saved.get(k)) is type(v) else v
            for k, v in DEFAULT_OPTIONS.items()}


def set_option(name: str, value: Any) -> bool:
    if name not in DEFAULT_OPTIONS or type(value) is not type(DEFAULT_OPTIONS[name]):
        return False
    _update(options={**get_options(), name: value})
    return True
