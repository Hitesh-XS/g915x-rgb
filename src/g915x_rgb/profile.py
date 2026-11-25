"""Profile model and persistence for G915 X RGB.

Profiles are stored as JSON in ``~/.config/g915x-rgb/profiles/``. The
schema is versioned; ``Profile.from_dict`` migrates v1 documents on read
so older profiles keep working after the schema bump.

A v2 profile carries:

* group/key colors (unchanged from v1)
* a startup-animation config that plays once when the profile is applied
* an optional ambient-animation config that loops continuously while the
  profile is active
* an optional reactive-overlay config (typing effects)
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .keyboard_layout import KEY_BY_NAME, G915X_KEYS

PROFILES_DIR = Path.home() / ".config" / "g915x-rgb" / "profiles"
RULES_PATH = Path.home() / ".config" / "g915x-rgb" / "auto_switch.json"

SCHEMA_VERSION = 2

RGB = tuple[int, int, int]
DEFAULT_PRIMARY: RGB = (0x00, 0x80, 0xFF)
DEFAULT_SECONDARY: RGB = (0xFF, 0x40, 0x80)


@dataclass
class AnimationProfile:
    """Per-profile knobs for an animation slot (startup or ambient)."""

    name: str = ""                          # registered animation name; "" = none
    primary: RGB = DEFAULT_PRIMARY
    secondary: RGB = DEFAULT_SECONDARY
    speed: float = 1.0
    intensity: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "primary": _rgb_to_hex(self.primary),
            "secondary": _rgb_to_hex(self.secondary),
            "speed": self.speed,
            "intensity": self.intensity,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnimationProfile":
        return cls(
            name=data.get("name", ""),
            primary=_parse_hex(data.get("primary", _rgb_to_hex(DEFAULT_PRIMARY))),
            secondary=_parse_hex(data.get("secondary", _rgb_to_hex(DEFAULT_SECONDARY))),
            speed=float(data.get("speed", 1.0)),
            intensity=float(data.get("intensity", 1.0)),
        )


@dataclass
class ReactiveProfile:
    """Reactive typing overlay configuration."""

    effect: str = ""                         # "" | "glow" | "ripple" | "starburst"
    color: RGB = (255, 255, 255)
    intensity: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect": self.effect,
            "color": _rgb_to_hex(self.color),
            "intensity": self.intensity,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReactiveProfile":
        return cls(
            effect=data.get("effect", ""),
            color=_parse_hex(data.get("color", "ffffff")),
            intensity=float(data.get("intensity", 1.0)),
        )


@dataclass
class Profile:
    name: str
    group_colors: dict[str, RGB] = field(default_factory=dict)
    key_colors: dict[int, RGB] = field(default_factory=dict)
    startup: AnimationProfile = field(default_factory=AnimationProfile)
    ambient: AnimationProfile = field(default_factory=AnimationProfile)
    reactive: ReactiveProfile = field(default_factory=ReactiveProfile)
    _saved_path: Path | None = field(default=None, repr=False)

    # ----- legacy-compat properties used by older callers -----
    @property
    def startup_animation(self) -> str:
        return self.startup.name

    @startup_animation.setter
    def startup_animation(self, name: str) -> None:
        self.startup = replace(self.startup, name=name)

    @property
    def animation_primary(self) -> RGB:
        return self.startup.primary

    @animation_primary.setter
    def animation_primary(self, color: RGB) -> None:
        self.startup = replace(self.startup, primary=color)

    @property
    def animation_secondary(self) -> RGB:
        return self.startup.secondary

    @animation_secondary.setter
    def animation_secondary(self, color: RGB) -> None:
        self.startup = replace(self.startup, secondary=color)

    # ----- color resolution -----
    def get_effective_color(self, address: int) -> RGB:
        if address in self.key_colors:
            return self.key_colors[address]
        for key in G915X_KEYS:
            if key.address == address and key.group in self.group_colors:
                return self.group_colors[key.group]
        return (0, 0, 0)

    def get_all_key_colors(self) -> dict[int, RGB]:
        colors: dict[int, RGB] = {}
        for key in G915X_KEYS:
            if key.group in self.group_colors:
                colors[key.address] = self.group_colors[key.group]
        colors.update(self.key_colors)
        return colors

    # ----- serialization -----
    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": SCHEMA_VERSION,
            "startup": self.startup.to_dict(),
            "ambient": self.ambient.to_dict(),
            "reactive": self.reactive.to_dict(),
            "groups": {
                g: _rgb_to_hex(c) for g, c in self.group_colors.items()
            },
            "individual_keys": {
                str(addr): _rgb_to_hex(c)
                for addr, c in self.key_colors.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Profile":
        version = int(data.get("version", 1))
        if version >= 2:
            return cls._from_v2(data)
        return cls._from_v1(data)

    @classmethod
    def _from_v2(cls, data: dict[str, Any]) -> "Profile":
        p = cls(name=data.get("name", "Unnamed"))
        if "startup" in data:
            p.startup = AnimationProfile.from_dict(data["startup"])
        if "ambient" in data:
            p.ambient = AnimationProfile.from_dict(data["ambient"])
        if "reactive" in data:
            p.reactive = ReactiveProfile.from_dict(data["reactive"])
        for group, color_hex in data.get("groups", {}).items():
            p.group_colors[group] = _parse_hex(color_hex)
        for addr_str, color_hex in data.get("individual_keys", {}).items():
            p.key_colors[int(addr_str)] = _parse_hex(color_hex)
        return p

    @classmethod
    def _from_v1(cls, data: dict[str, Any]) -> "Profile":
        p = cls(name=data.get("name", "Unnamed"))
        anim = data.get("startup_animation", "")
        if anim is True:
            anim = "arch"
        elif anim is False:
            anim = ""

        p.startup = AnimationProfile(
            name=anim,
            primary=_parse_hex(data["animation_primary"])
            if "animation_primary" in data else DEFAULT_PRIMARY,
            secondary=_parse_hex(data["animation_secondary"])
            if "animation_secondary" in data else DEFAULT_SECONDARY,
        )
        for group, color_hex in data.get("groups", {}).items():
            p.group_colors[group] = _parse_hex(color_hex)
        for addr_str, color_hex in data.get("individual_keys", {}).items():
            p.key_colors[int(addr_str)] = _parse_hex(color_hex)
        return p

    def save(self) -> Path:
        """Atomically write the profile to disk; remove the prior file if renamed."""
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        filename = _safe_filename(self.name) + ".json"
        path = PROFILES_DIR / filename

        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{filename}.", dir=str(PROFILES_DIR), suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self.to_dict(), f, indent=2)
            os.replace(tmp_name, path)
        except Exception:
            # Best-effort cleanup if anything goes wrong before replace.
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
            raise

        if self._saved_path and self._saved_path != path and self._saved_path.exists():
            self._saved_path.unlink()
        self._saved_path = path
        return path

    @classmethod
    def load(cls, path: Path) -> "Profile":
        with open(path) as f:
            p = cls.from_dict(json.load(f))
        p._saved_path = path
        return p


# =====================================================================
# Bulk operations
# =====================================================================

def list_profiles() -> list[Path]:
    if not PROFILES_DIR.exists():
        return []
    return sorted(PROFILES_DIR.glob("*.json"))


@dataclass
class LoadResult:
    profiles: list[Profile]
    errors: list[tuple[Path, str]]   # (path, error message)


def load_all_profiles_with_errors() -> LoadResult:
    """Load all saved profiles, returning successes and any per-file errors."""
    profiles: list[Profile] = []
    errors: list[tuple[Path, str]] = []
    for path in list_profiles():
        try:
            profiles.append(Profile.load(path))
        except (json.JSONDecodeError, KeyError, ValueError, OSError) as e:
            errors.append((path, f"{type(e).__name__}: {e}"))
    return LoadResult(profiles=profiles, errors=errors)


def load_all_profiles() -> list[Profile]:
    """Convenience wrapper for callers that don't care about errors."""
    return load_all_profiles_with_errors().profiles


def import_from_g810_script(script_path: str) -> Profile:
    p = Profile(name=Path(script_path).stem)
    with open(script_path) as f:
        for line in f:
            line = line.strip()
            m = re.match(r"g\d+x?-led\s+-g\s+(\w+)\s+([0-9a-fA-F]{6})", line)
            if m:
                p.group_colors[m.group(1)] = _parse_hex(m.group(2))
                continue
            m = re.match(r"g\d+x?-led\s+-k\s+(\w+)\s+([0-9a-fA-F]{6})", line)
            if m and m.group(1) in KEY_BY_NAME:
                p.key_colors[KEY_BY_NAME[m.group(1)].address] = _parse_hex(m.group(2))
    return p


def create_default_profile() -> Profile:
    p = Profile(name="Default")
    p.group_colors["keys"] = (255, 255, 255)
    p.group_colors["fkeys"] = (200, 200, 255)
    p.group_colors["modifiers"] = (180, 180, 180)
    p.group_colors["arrows"] = (200, 200, 255)
    p.group_colors["numeric"] = (200, 200, 200)
    p.group_colors["functions"] = (180, 180, 255)
    p.group_colors["gkeys"] = (100, 0, 200)
    p.group_colors["logo"] = (100, 0, 200)
    p.group_colors["indicators"] = (200, 200, 200)
    p.group_colors["multimedia"] = (150, 150, 200)
    return p


def duplicate_profile(p: Profile, new_name: str) -> Profile:
    """Return a deep-ish copy of ``p`` under a new name (unsaved)."""
    return Profile(
        name=new_name,
        group_colors=dict(p.group_colors),
        key_colors=dict(p.key_colors),
        startup=replace(p.startup),
        ambient=replace(p.ambient),
        reactive=replace(p.reactive),
    )


# =====================================================================
# Helpers
# =====================================================================

def _parse_hex(hex_str: str) -> RGB:
    hex_str = hex_str.lstrip("#")
    return (
        int(hex_str[0:2], 16),
        int(hex_str[2:4], 16),
        int(hex_str[4:6], 16),
    )


def _rgb_to_hex(c: RGB) -> str:
    r, g, b = c
    return f"{r:02x}{g:02x}{b:02x}"


def _safe_filename(name: str) -> str:
    return re.sub(r"[^\w\-]", "_", name).lower() or "profile"
