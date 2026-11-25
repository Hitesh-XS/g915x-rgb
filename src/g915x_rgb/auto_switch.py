"""Per-window profile auto-switching.

Provides:

* :class:`AutoSwitchRules` — file-backed list of (matcher → profile) rules,
  evaluated top-to-bottom for each active-window event.
* :class:`KWinSource` — installs a small KWin JS script that reports
  active-window changes to the daemon over D-Bus. The daemon plumbs the
  resulting class/title pair into the rule matcher.

Only KWin (KDE Plasma 5/6) is wired up in this version; other compositors
fall through with a logged warning. The :class:`ActiveWindowSource`
Protocol is shaped so Hyprland or Sway adapters can drop in later
without changes elsewhere.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .profile import RULES_PATH

logger = logging.getLogger("g915x.auto_switch")

DEFAULT_TRANSITION = "fade"

ACTIVE_WINDOW_HANDLER = Callable[[str, str], None]


# =====================================================================
# Rules
# =====================================================================

@dataclass
class Rule:
    """A single auto-switch rule.

    At least one of ``class_regex`` or ``title_contains`` must be set;
    both must match (when present) for the rule to apply.
    """

    profile: str
    class_regex: str | None = None
    title_contains: str | None = None
    transition: str | None = None       # overrides the default transition

    def matches(self, win_class: str, title: str) -> bool:
        if self.class_regex is not None:
            try:
                if not re.search(self.class_regex, win_class, re.IGNORECASE):
                    return False
            except re.error:
                return False
        if self.title_contains is not None:
            if self.title_contains.lower() not in title.lower():
                return False
        return self.class_regex is not None or self.title_contains is not None

    def to_dict(self) -> dict:
        d: dict = {"profile": self.profile}
        if self.class_regex is not None:
            d["class_regex"] = self.class_regex
        if self.title_contains is not None:
            d["title_contains"] = self.title_contains
        if self.transition is not None:
            d["transition"] = self.transition
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Rule":
        return cls(
            profile=d["profile"],
            class_regex=d.get("class_regex"),
            title_contains=d.get("title_contains"),
            transition=d.get("transition"),
        )


@dataclass
class AutoSwitchRules:
    enabled: bool = False
    transition: str = DEFAULT_TRANSITION
    rules: list[Rule] = field(default_factory=list)

    def match(self, win_class: str, title: str) -> Rule | None:
        for rule in self.rules:
            if rule.matches(win_class, title):
                return rule
        return None

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "transition": self.transition,
            "rules": [r.to_dict() for r in self.rules],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AutoSwitchRules":
        return cls(
            enabled=bool(d.get("enabled", False)),
            transition=d.get("transition", DEFAULT_TRANSITION),
            rules=[Rule.from_dict(r) for r in d.get("rules", [])],
        )

    def save(self, path: Path = RULES_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: Path = RULES_PATH) -> "AutoSwitchRules":
        if not path.exists():
            return cls()
        try:
            with open(path) as f:
                return cls.from_dict(json.load(f))
        except (OSError, json.JSONDecodeError, KeyError) as e:
            logger.warning("could not load auto-switch rules from %s: %s", path, e)
            return cls()


# =====================================================================
# Active window source — Protocol + KWin implementation
# =====================================================================

class ActiveWindowSource(Protocol):
    name: str

    def start(self, on_change: ACTIVE_WINDOW_HANDLER) -> bool: ...
    def stop(self) -> None: ...


class KWinSource:
    """Installs a KWin script that pushes active-window changes via DBus.

    The KWin script calls ``org.g915xrgb.Controller.OnActiveWindowChanged``
    whenever the active window changes. The daemon's DBus service
    implements that method and forwards the call here via ``ingest``.
    """

    name = "kwin"

    def __init__(self, script_path: Path) -> None:
        self._script_path = script_path
        self._on_change: ACTIVE_WINDOW_HANDLER | None = None
        self._script_id: int | None = None

    def start(self, on_change: ACTIVE_WINDOW_HANDLER) -> bool:
        self._on_change = on_change

        if not self._script_path.exists():
            logger.warning("KWin script missing: %s", self._script_path)
            return False

        try:
            import dbus
        except ImportError:
            logger.warning("dbus-python not available; auto-switch disabled")
            return False

        try:
            bus = dbus.SessionBus()
            scripting = bus.get_object("org.kde.KWin", "/Scripting")
            iface = dbus.Interface(scripting, "org.kde.kwin.Scripting")
            self._script_id = int(iface.loadScript(str(self._script_path)))
            iface.start()
            logger.info("KWin auto-switch script loaded (id=%s)", self._script_id)
            return True
        except dbus.DBusException as e:
            logger.warning("KWin scripting unavailable: %s", e)
            return False

    def ingest(self, win_class: str, title: str) -> None:
        """Called by the daemon's DBus handler when KWin reports a change."""
        if self._on_change is not None:
            self._on_change(win_class, title)

    def stop(self) -> None:
        if self._script_id is None:
            return
        try:
            import dbus
            bus = dbus.SessionBus()
            scripting = bus.get_object("org.kde.KWin", "/Scripting")
            iface = dbus.Interface(scripting, "org.kde.kwin.Scripting")
            try:
                iface.unloadScript(str(self._script_path))
            except Exception:
                pass
        except Exception:
            pass
        self._script_id = None


def detect_compositor() -> str:
    """Return a short identifier for the running compositor."""
    import os
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if "kde" in desktop:
        return "kwin"
    if "hyprland" in desktop:
        return "hyprland"
    if "sway" in desktop:
        return "sway"
    if "gnome" in desktop:
        return "gnome"
    return "unknown"


def find_kwin_script() -> Path | None:
    """Locate the bundled KWin JS script, regardless of install layout."""
    candidates = [
        # Repo layout (PYTHONPATH=src python -m ...).
        Path(__file__).resolve().parent.parent.parent / "data" / "kwin_active_window.js",
        # Installed under a typical share/ prefix.
        Path("/usr/share/g915x-rgb/kwin_active_window.js"),
        Path.home() / ".local" / "share" / "g915x-rgb" / "kwin_active_window.js",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None
