"""Profile-to-profile transition animations.

A transition is a function ``(old_frame, new_frame, t) -> Frame`` where
``t`` is a normalized progress value in ``[0, 1]``. The renderer plays
the transition over a configured duration whenever a SetProfile call
includes a transition name.

All transitions share a small helper vocabulary (``_lerp``, ``_dim``,
plus a few spatial primitives). Each implementation is intentionally
short — the visual interest comes from the choice of mapping, not from
heavy code.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass

from .animation import Frame, RGB, _dim, _lerp
from .keyboard_layout import G915X_KEYS, KEY_BY_ADDRESS, LAYOUT_HEIGHT, LAYOUT_WIDTH


@dataclass
class TransitionConfig:
    """Knobs that any transition can read."""

    duration: float = 0.6           # seconds
    intensity: float = 1.0          # 0..2, scales output brightness
    accent: RGB = (255, 255, 255)   # used by wipe edges, sparkle flash, glitch noise


TransitionFn = Callable[[Frame, Frame, float, TransitionConfig], Frame]


TRANSITIONS: dict[str, tuple[str, TransitionFn]] = {}


def register(name: str, description: str):
    def decorator(func: TransitionFn) -> TransitionFn:
        TRANSITIONS[name] = (description, func)
        return func
    return decorator


def get_transition_choices() -> list[tuple[str, str]]:
    return [(name, desc) for name, (desc, _) in TRANSITIONS.items()]


# =====================================================================
# Spatial helpers
# =====================================================================

_KEY_X: dict[int, float] = {k.address: k.x + k.w / 2 for k in G915X_KEYS}
_KEY_Y: dict[int, float] = {k.address: k.y + k.h / 2 for k in G915X_KEYS}

_X_MIN = min(_KEY_X.values())
_X_MAX = max(_KEY_X.values())
_Y_MIN = min(_KEY_Y.values())
_Y_MAX = max(_KEY_Y.values())
_CX = (_X_MIN + _X_MAX) / 2
_CY = (_Y_MIN + _Y_MAX) / 2
_MAX_RADIUS = math.sqrt(((_X_MAX - _X_MIN) / 2) ** 2 + ((_Y_MAX - _Y_MIN) / 2) ** 2)

# A stable but unsorted key order so sparkle / dissolve produce visually
# even-looking results without re-shuffling each call.
_RNG_KEYS = sorted(_KEY_X)
_DISSOLVE_ORDER: list[int] = list(_RNG_KEYS)
random.Random(0xC0FFEE).shuffle(_DISSOLVE_ORDER)
_DISSOLVE_T: dict[int, float] = {
    addr: i / max(len(_DISSOLVE_ORDER) - 1, 1)
    for i, addr in enumerate(_DISSOLVE_ORDER)
}

_SPARKLE_ORDER: list[int] = list(_RNG_KEYS)
random.Random(0xBADC0DE).shuffle(_SPARKLE_ORDER)
_SPARKLE_T: dict[int, float] = {
    addr: i / max(len(_SPARKLE_ORDER) - 1, 1)
    for i, addr in enumerate(_SPARKLE_ORDER)
}


def _scale(frame: Frame, intensity: float) -> Frame:
    if intensity == 1.0:
        return frame
    return {a: _dim(c, intensity) for a, c in frame.items()}


def _saturation(c: RGB) -> float:
    mx = max(c)
    mn = min(c)
    if mx == 0:
        return 0.0
    return (mx - mn) / mx


def _to_grayscale(c: RGB) -> RGB:
    luma = int(0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2])
    return (luma, luma, luma)


# =====================================================================
# Transitions
# =====================================================================

@register("fade", "Smooth cross-fade between profiles")
def fade(old: Frame, new: Frame, t: float, cfg: TransitionConfig) -> Frame:
    out: Frame = {}
    addrs = set(old) | set(new)
    for a in addrs:
        out[a] = _lerp(old.get(a, (0, 0, 0)), new.get(a, (0, 0, 0)), t)
    return _scale(out, cfg.intensity)


@register("horizontal_wipe", "Bright edge sweeps left to right, revealing the new profile")
def horizontal_wipe(old: Frame, new: Frame, t: float, cfg: TransitionConfig) -> Frame:
    edge_x = _X_MIN + (_X_MAX - _X_MIN + 1.0) * t
    edge_width = 0.6
    out: Frame = {}
    addrs = set(old) | set(new)
    for a in addrs:
        x = _KEY_X.get(a, _X_MIN)
        old_c = old.get(a, (0, 0, 0))
        new_c = new.get(a, (0, 0, 0))
        if x < edge_x - edge_width:
            out[a] = new_c
        elif x > edge_x + edge_width:
            out[a] = old_c
        else:
            edge_t = 1.0 - abs(x - edge_x) / edge_width
            base = _lerp(old_c, new_c, 0.5)
            out[a] = _lerp(base, cfg.accent, edge_t)
    return _scale(out, cfg.intensity)


@register("vertical_wipe", "Bright edge sweeps top to bottom")
def vertical_wipe(old: Frame, new: Frame, t: float, cfg: TransitionConfig) -> Frame:
    edge_y = _Y_MIN + (_Y_MAX - _Y_MIN + 1.0) * t
    edge_width = 0.6
    out: Frame = {}
    addrs = set(old) | set(new)
    for a in addrs:
        y = _KEY_Y.get(a, _Y_MIN)
        old_c = old.get(a, (0, 0, 0))
        new_c = new.get(a, (0, 0, 0))
        if y < edge_y - edge_width:
            out[a] = new_c
        elif y > edge_y + edge_width:
            out[a] = old_c
        else:
            edge_t = 1.0 - abs(y - edge_y) / edge_width
            base = _lerp(old_c, new_c, 0.5)
            out[a] = _lerp(base, cfg.accent, edge_t)
    return _scale(out, cfg.intensity)


@register("sparkle_away", "Old keys flash white and pop into new colors one by one")
def sparkle_away(old: Frame, new: Frame, t: float, cfg: TransitionConfig) -> Frame:
    flash_window = 0.08
    out: Frame = {}
    addrs = set(old) | set(new)
    for a in addrs:
        key_t = _SPARKLE_T.get(a, 0.5)
        d = t - key_t
        old_c = old.get(a, (0, 0, 0))
        new_c = new.get(a, (0, 0, 0))
        if d < 0:
            out[a] = old_c
        elif d < flash_window:
            flash_t = d / flash_window
            out[a] = _lerp(old_c, cfg.accent, 1.0 - flash_t)
        elif d < flash_window * 2:
            settle_t = (d - flash_window) / flash_window
            out[a] = _lerp(cfg.accent, new_c, settle_t)
        else:
            out[a] = new_c
    return _scale(out, cfg.intensity)


@register("dissolve", "Random per-key cuts from old to new")
def dissolve(old: Frame, new: Frame, t: float, cfg: TransitionConfig) -> Frame:
    out: Frame = {}
    addrs = set(old) | set(new)
    for a in addrs:
        threshold = _DISSOLVE_T.get(a, 0.5)
        out[a] = new.get(a, (0, 0, 0)) if t >= threshold else old.get(a, (0, 0, 0))
    return _scale(out, cfg.intensity)


@register("iris", "Old recedes from edges to center; new expands from center outward")
def iris(old: Frame, new: Frame, t: float, cfg: TransitionConfig) -> Frame:
    radius_visible = _MAX_RADIUS * t
    band = 0.6
    out: Frame = {}
    addrs = set(old) | set(new)
    for a in addrs:
        x, y = _KEY_X.get(a, _CX), _KEY_Y.get(a, _CY)
        d = math.sqrt((x - _CX) ** 2 + (y - _CY) ** 2)
        old_c = old.get(a, (0, 0, 0))
        new_c = new.get(a, (0, 0, 0))
        if d < radius_visible - band:
            out[a] = new_c
        elif d > radius_visible + band:
            out[a] = old_c
        else:
            edge_t = 1.0 - abs(d - radius_visible) / band
            base = _lerp(old_c, new_c, 1.0 if d < radius_visible else 0.0)
            out[a] = _lerp(base, cfg.accent, edge_t * 0.6)
    return _scale(out, cfg.intensity)


@register("shutter", "Three vertical bars close, pause, then reopen on the new profile")
def shutter(old: Frame, new: Frame, t: float, cfg: TransitionConfig) -> Frame:
    # Close: 0..0.45, Pause black: 0.45..0.55, Open: 0.55..1.0
    close_end = 0.45
    open_start = 0.55
    bars = 3
    bar_width = (_X_MAX - _X_MIN) / bars
    out: Frame = {}
    addrs = set(old) | set(new)

    if t < close_end:
        progress = t / close_end       # 0..1
        # Each bar grows from its center outward to bar_width
    elif t < open_start:
        progress = 1.0
    else:
        progress = (1.0 - t) / (1.0 - open_start)  # 1..0
        progress = max(0.0, progress)

    for a in addrs:
        x = _KEY_X.get(a, _X_MIN)
        bar_idx = min(bars - 1, max(0, int((x - _X_MIN) / bar_width)))
        bar_center = _X_MIN + (bar_idx + 0.5) * bar_width
        rel = abs(x - bar_center) / (bar_width / 2)   # 0 at center, 1 at edge
        covered = rel <= progress
        old_c = old.get(a, (0, 0, 0))
        new_c = new.get(a, (0, 0, 0))
        if t < open_start:
            # Closing or paused: covered keys go to black, exposing new behind
            out[a] = (0, 0, 0) if covered else old_c
        else:
            # Opening: covered keys still black, others reveal new
            out[a] = (0, 0, 0) if covered else new_c
    return _scale(out, cfg.intensity)


@register("color_drain", "Old colors desaturate row by row, then new saturates in")
def color_drain(old: Frame, new: Frame, t: float, cfg: TransitionConfig) -> Frame:
    drain_end = 0.5
    out: Frame = {}
    addrs = set(old) | set(new)

    for a in addrs:
        y = _KEY_Y.get(a, _Y_MIN)
        row_t = (y - _Y_MIN) / max(_Y_MAX - _Y_MIN, 1.0)
        old_c = old.get(a, (0, 0, 0))
        new_c = new.get(a, (0, 0, 0))

        if t < drain_end:
            phase = t / drain_end                 # 0..1 as drain proceeds
            local = max(0.0, min(1.0, (phase - row_t) * 2.0 + 0.5))
            gray = _to_grayscale(old_c)
            black = (0, 0, 0)
            mid = _lerp(old_c, gray, min(1.0, local * 2))
            out[a] = _lerp(mid, black, max(0.0, local * 2 - 1.0))
        else:
            phase = (t - drain_end) / (1.0 - drain_end)
            inv_row = 1.0 - row_t
            local = max(0.0, min(1.0, (phase - inv_row) * 2.0 + 0.5))
            gray = _to_grayscale(new_c)
            black = (0, 0, 0)
            half = _lerp(black, gray, min(1.0, local * 2))
            out[a] = _lerp(half, new_c, max(0.0, local * 2 - 1.0))
    return _scale(out, cfg.intensity)


@register("glitch", "RGB-noise glitches before settling on the new profile")
def glitch(old: Frame, new: Frame, t: float, cfg: TransitionConfig) -> Frame:
    # Discrete phases: 4 noise pulses interspersed with brief flashes of old/new
    phases = [
        ("noise", 0.0, 0.18),
        ("old", 0.18, 0.22),
        ("noise", 0.22, 0.40),
        ("new", 0.40, 0.44),
        ("noise", 0.44, 0.62),
        ("old", 0.62, 0.66),
        ("noise", 0.66, 0.85),
        ("new", 0.85, 1.0),
    ]
    phase = "new"
    for kind, lo, hi in phases:
        if lo <= t < hi:
            phase = kind
            break

    if phase == "old":
        return _scale(dict(old), cfg.intensity)
    if phase == "new":
        return _scale(dict(new), cfg.intensity)

    # Noise: deterministic-but-chaotic per (addr, phase_bucket)
    bucket = int(t * 64)
    rng = random.Random(bucket * 7919)
    out: Frame = {}
    addrs = set(old) | set(new)
    for a in addrs:
        if rng.random() < 0.35:
            out[a] = (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
        else:
            base = new.get(a, (0, 0, 0)) if rng.random() < 0.6 else old.get(a, (0, 0, 0))
            jitter = rng.randint(-30, 30)
            out[a] = (
                max(0, min(255, base[0] + jitter)),
                max(0, min(255, base[1] + jitter)),
                max(0, min(255, base[2] + jitter)),
            )
    return _scale(out, cfg.intensity)


@register("burn", "Hot flash, embers cool through orange, new profile emerges")
def burn(old: Frame, new: Frame, t: float, cfg: TransitionConfig) -> Frame:
    HOT = (255, 90, 0)
    DIM = (60, 8, 0)
    out: Frame = {}
    addrs = set(old) | set(new)

    if t < 0.15:
        # Flash up: old -> hot
        u = t / 0.15
        for a in addrs:
            out[a] = _lerp(old.get(a, (0, 0, 0)), HOT, u)
    elif t < 0.5:
        # Cool: hot -> dim ember
        u = (t - 0.15) / 0.35
        for a in addrs:
            base = _lerp(HOT, DIM, u)
            jitter = 1.0 - 0.15 * math.sin((t + _KEY_X.get(a, 0)) * 9)
            out[a] = _dim(base, jitter)
    elif t < 0.7:
        # Embers fade to black
        u = (t - 0.5) / 0.2
        for a in addrs:
            out[a] = _lerp(DIM, (0, 0, 0), u)
    else:
        # New profile rises in
        u = (t - 0.7) / 0.3
        for a in addrs:
            out[a] = _lerp((0, 0, 0), new.get(a, (0, 0, 0)), u)
    return _scale(out, cfg.intensity)


@register("heartbeat", "Two pulses on the old colors, a beat, two on the new")
def heartbeat(old: Frame, new: Frame, t: float, cfg: TransitionConfig) -> Frame:
    # 0.0–0.2: pulse 1 of old      (peak → dip → peak)
    # 0.2–0.4: pulse 2 of old
    # 0.4–0.6: black hold
    # 0.6–0.8: pulse 1 of new
    # 0.8–1.0: pulse 2 of new      (ends on peak)
    out: Frame = {}
    addrs = set(old) | set(new)

    def pulse_strength(local_t: float) -> float:
        # peak at 0, dip at 0.5, peak at 1
        return 0.5 + 0.5 * math.cos(local_t * 2 * math.pi)

    if t < 0.2:
        s = pulse_strength(t / 0.2)
        src = old
    elif t < 0.4:
        s = pulse_strength((t - 0.2) / 0.2)
        src = old
    elif t < 0.6:
        return _scale({a: (0, 0, 0) for a in addrs}, cfg.intensity)
    elif t < 0.8:
        s = pulse_strength((t - 0.6) / 0.2)
        src = new
    else:
        s = pulse_strength((t - 0.8) / 0.2)
        src = new

    for a in addrs:
        out[a] = _dim(src.get(a, (0, 0, 0)), s)
    return _scale(out, cfg.intensity)
