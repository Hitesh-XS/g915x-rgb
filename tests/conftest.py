"""Keep tests away from the real configuration."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="g915x-tests-")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An empty configuration directory."""
    import g915x_rgb.auto_switch as auto
    import g915x_rgb.config as config
    import g915x_rgb.profile as profile

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(profile, "PROFILES_DIR", tmp_path / "profiles")
    rules = tmp_path / "auto_switch.json"
    monkeypatch.setattr(auto.AutoSwitchRules.save, "__defaults__", (rules,))
    monkeypatch.setattr(auto.AutoSwitchRules.load.__func__, "__defaults__", (rules,))
    return tmp_path
