"""Animations for the G915 X keyboard.

Every animation is a generator yielding ``(frame, dt)`` at a uniform
**15 FPS** with continuous phase math, so:

* ambient mode loops cleanly without hard resets,
* mid-flight cfg edits (color sliders, speed, intensity) take effect on
  the very next frame because the loop bodies re-read ``cfg`` per frame,
* the runner can play any animation as a one-shot by capping it on
  elapsed seconds.

The :class:`AnimationRunner` consumes a stream against an ``on_frame``
callback. ``play(..., loop=True)`` keeps it running forever (ambient);
``play(..., duration=N)`` stops automatically after N seconds (startup).
"""

from __future__ import annotations

import colorsys
import math
import random
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from .keyboard_layout import G915X_KEYS

# --- Types ---

RGB = tuple[int, int, int]
Frame = dict[int, RGB]
FrameStream = Iterator[tuple[Frame, float]]


@dataclass
class AnimationConfig:
    """Tunable parameters threaded through every animation.

    The dataclass is **mutable**: animations re-read these fields each
    frame, so updates from the GUI take effect without restarting the
    runner.
    """

    primary: RGB = (0x00, 0x80, 0xFF)
    secondary: RGB = (0xFF, 0x40, 0x80)
    speed: float = 1.0          # >1 = faster phase, <1 = slower
    intensity: float = 1.0      # output brightness multiplier (0..2)
    palette: list[RGB] = field(default_factory=list)


AnimationFactory = Callable[[AnimationConfig], FrameStream]


# --- Registry ---

ANIMATIONS: dict[str, tuple[str, AnimationFactory]] = {}


def register(name: str, description: str):
    def decorator(func: AnimationFactory) -> AnimationFactory:
        ANIMATIONS[name] = (description, func)
        return func
    return decorator


def get_animation_choices() -> list[tuple[str, str]]:
    return [(name, desc) for name, (desc, _) in ANIMATIONS.items()]


# --- Constants & helpers ---

ALL_KEYS: list[int] = [k.address for k in G915X_KEYS]
OFF: RGB = (0, 0, 0)
# 15 FPS: the device firmware can't keep up with all-keys-changed frames
# at 30 FPS — its HID write buffer saturates after a few seconds and
# everything stalls. 15 FPS is the upper bound the original code happened
# to land on for full-keyboard animations like aurora/helix/ripple.
DT = 1.0 / 15.0


def all_off_frame() -> Frame:
    return {a: OFF for a in ALL_KEYS}


def _lerp(c1: RGB, c2: RGB, t: float) -> RGB:
    t = max(0.0, min(1.0, t))
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def _dim(c: RGB, f: float) -> RGB:
    return (
        min(255, max(0, int(c[0] * f))),
        min(255, max(0, int(c[1] * f))),
        min(255, max(0, int(c[2] * f))),
    )


def _scale_frame(frame: Frame, intensity: float) -> Frame:
    if intensity == 1.0:
        return frame
    return {a: _dim(c, intensity) for a, c in frame.items()}


# Pre-computed spatial info — built once at import.
_KX: dict[int, float] = {k.address: k.x + k.w / 2 for k in G915X_KEYS}
_KY: dict[int, float] = {k.address: k.y + k.h / 2 for k in G915X_KEYS}
_X_MIN = min(_KX.values())
_X_MAX = max(_KX.values())
_Y_MIN = min(_KY.values())
_Y_MAX = max(_KY.values())
_CX = (_X_MIN + _X_MAX) / 2
_CY = (_Y_MIN + _Y_MAX) / 2
_MAX_RADIUS = math.hypot(_X_MAX - _CX, _Y_MAX - _CY)

_COLUMNS: dict[float, list[int]] = {}
for _k in G915X_KEYS:
    _COLUMNS.setdefault(round(_k.x * 2) / 2, []).append(_k.address)
_SORTED_COLS: list[float] = sorted(_COLUMNS)

_ROWS: dict[float, list[int]] = {}
for _k in G915X_KEYS:
    _ROWS.setdefault(round(_k.y * 2) / 2, []).append(_k.address)
_SORTED_ROWS: list[float] = sorted(_ROWS)


def _step(phase: float, speed: float, rate: float) -> float:
    """Advance ``phase`` by one tick at the given rate × speed."""
    return phase + DT * max(0.05, speed) * rate


# =====================================================================
# Animation implementations — all infinite-loop, all 15 FPS
# =====================================================================

@register("aurora", "Aurora borealis — flowing curtains of light")
def aurora(cfg: AnimationConfig) -> FrameStream:
    phase = 0.0
    while True:
        primary, secondary = cfg.primary, cfg.secondary
        f: Frame = {}
        for a, kx in _KX.items():
            ky = _KY[a]
            y_blend = max(0.0, min(1.0, (ky + 0.5) / 6.0))
            base = _lerp(primary, secondary, y_blend)
            wave1 = math.sin(kx * 0.4 + phase) * 0.5 + 0.5
            wave2 = math.sin(kx * 0.7 - phase * 1.3 + ky * 0.3) * 0.5 + 0.5
            shimmer = math.sin(kx * 1.5 + phase * 3.0) * 0.15 + 0.85
            brightness = (wave1 * 0.6 + wave2 * 0.4) * shimmer
            f[a] = _dim(base, brightness)
        yield f, DT
        phase = _step(phase, cfg.speed, 2.0)


@register("comet", "Twin comets — streaks of light crossing paths")
def comet(cfg: AnimationConfig) -> FrameStream:
    n = len(_SORTED_COLS)
    trail = 6
    cycle = n + trail + 4
    phase = 0.0
    while True:
        primary, secondary = cfg.primary, cfg.secondary
        head = phase % cycle
        f: Frame = {}
        for i, col_x in enumerate(_SORTED_COLS):
            dp = head - i
            ds = head - (n - 1 - i)
            color: RGB = OFF
            for d, col in [(dp, primary), (ds, secondary)]:
                if 0 <= d < trail:
                    bright = 1.0 - d / trail
                    c = (255, 255, 255) if d < 0.5 else _dim(col, bright)
                    color = c if color == OFF else _lerp(color, c, 0.5)
            for addr in _COLUMNS[col_x]:
                f[addr] = color
        yield f, DT
        phase = _step(phase, cfg.speed, 18.0)


@register("ripple", "Concentric ripples — waves from the center")
def ripple(cfg: AnimationConfig) -> FrameStream:
    ring_width = 2.0
    phase = 0.0
    while True:
        primary, secondary = cfg.primary, cfg.secondary
        f: Frame = {}
        for a, kx in _KX.items():
            ky = _KY[a]
            d = math.hypot(kx - _CX, ky - _CY)
            best_b = 0.0
            best_c = primary
            for w_i in range(3):
                front = phase - w_i * 3.0
                rd = abs(d - front)
                if rd < ring_width:
                    b = (1.0 - rd / ring_width) ** 2
                    if b > best_b:
                        best_b = b
                        best_c = primary if w_i % 2 == 0 else secondary
            f[a] = _dim(best_c, best_b) if best_b > 0.02 else OFF
        yield f, DT
        # Wrap so ambient never drifts to huge phase numbers.
        phase = _step(phase, cfg.speed, 4.0) % 60.0


@register("helix", "Double helix — intertwined sine waves")
def helix(cfg: AnimationConfig) -> FrameStream:
    cy = 3.0
    amp = 2.5
    phase = 0.0
    while True:
        primary, secondary = cfg.primary, cfg.secondary
        f: Frame = {}
        for a, kx in _KX.items():
            ky = _KY[a]
            y1 = cy + amp * math.sin(kx * 0.5 + phase)
            y2 = cy + amp * math.sin(kx * 0.5 + phase + math.pi)
            b1 = max(0.0, 1.0 - abs(ky - y1) / 1.2) ** 2
            b2 = max(0.0, 1.0 - abs(ky - y2) / 1.2) ** 2
            if b1 > 0.02 and b2 > 0.02:
                blend = min(b1, b2) / max(b1, b2)
                color = _lerp(_lerp(primary, secondary, b2 / (b1 + b2)),
                              (255, 255, 255), blend * 0.7)
                bright = max(b1, b2)
            elif b1 > 0.02:
                color, bright = primary, b1
            elif b2 > 0.02:
                color, bright = secondary, b2
            else:
                color, bright = OFF, 0.0
            f[a] = _dim(color, bright) if bright > 0.02 else OFF
        yield f, DT
        phase = (phase + DT * max(0.05, cfg.speed) * 2.5) % (math.pi * 4)


@register("prism", "Light prism — beam splitting endlessly")
def prism(cfg: AnimationConfig) -> FrameStream:
    """Continuous version of the original prism — a perpetual light
    beam striking the prism point and spraying spectral colors."""
    prism_x = 6.0
    max_x = _X_MAX
    beam_y = 3.0
    phase = 0.0
    while True:
        primary, secondary = cfg.primary, cfg.secondary
        # Beam sweep position oscillates along the prism point's row.
        beam_pulse = 0.5 + 0.5 * math.sin(phase)
        f: Frame = {}
        for a, kx in _KX.items():
            ky = _KY[a]
            if kx <= prism_x:
                # Incoming white beam: bright on beam_y row, fading vertically.
                vertical = max(0.0, 1.0 - abs(ky - beam_y) / 1.5)
                horiz = max(0.0, 1.0 - (prism_x - kx) / prism_x)
                f[a] = _dim((255, 255, 255), vertical * horiz * beam_pulse)
            else:
                # Spectral fan: y-blend × distance falloff × shimmer
                y_norm = (ky - _Y_MIN) / max(_Y_MAX - _Y_MIN, 1.0)
                color = _lerp(primary, secondary, y_norm)
                dist_t = (kx - prism_x) / max(max_x - prism_x, 1.0)
                shimmer = 0.7 + 0.3 * math.sin(phase * 2 + ky * 1.5)
                f[a] = _dim(color, max(0.25, dist_t) * shimmer)
        yield f, DT
        phase = _step(phase, cfg.speed, 1.6)


@register("pulse", "Breathing pulse — smooth color breathing")
def pulse(cfg: AnimationConfig) -> FrameStream:
    phase = 0.0
    while True:
        primary, secondary = cfg.primary, cfg.secondary
        # Slow alternation between primary and secondary on a sin curve.
        mix = 0.5 + 0.5 * math.cos(phase * 0.5)
        color = _lerp(primary, secondary, mix)
        # Brightness breath sits on a faster cycle.
        bright = 0.15 + 0.85 * (0.5 + 0.5 * math.sin(phase))
        c = _dim(color, bright)
        yield {a: c for a in ALL_KEYS}, DT
        phase = _step(phase, cfg.speed, 2.5)


@register("cascade", "Waterfall cascade — color falling continuously")
def cascade(cfg: AnimationConfig) -> FrameStream:
    """Loopable cascade: rows perpetually shift through a primary→secondary
    gradient that flows downward."""
    n_rows = len(_SORTED_ROWS)
    phase = 0.0
    while True:
        primary, secondary = cfg.primary, cfg.secondary
        f: Frame = {}
        for i, row_y in enumerate(_SORTED_ROWS):
            t = ((i / max(n_rows - 1, 1)) + phase) % 1.0
            color = _lerp(primary, secondary, t)
            # Subtle row-edge highlight to evoke water sheet
            highlight = 0.85 + 0.15 * math.sin(phase * 6 + i * 1.3)
            for addr in _ROWS[row_y]:
                f[addr] = _dim(color, highlight)
        yield f, DT
        phase = _step(phase, cfg.speed, 0.4)


@register("vortex", "Spiral vortex — keys swirling around the center")
def vortex(cfg: AnimationConfig) -> FrameStream:
    phase = 0.0
    angles: dict[int, float] = {
        a: math.atan2(_KY[a] - _CY, _KX[a] - _CX)
        for a in _KX
    }
    radii: dict[int, float] = {
        a: math.hypot(_KX[a] - _CX, _KY[a] - _CY)
        for a in _KX
    }
    while True:
        primary, secondary = cfg.primary, cfg.secondary
        f: Frame = {}
        for a in _KX:
            angle = angles[a] + phase
            dist_t = radii[a] / max(_MAX_RADIUS, 0.001)
            base = _lerp(primary, secondary, dist_t)
            arm = 0.5 + 0.5 * math.sin(angle * 3 + phase * 2)
            f[a] = _dim(base, 0.25 + 0.75 * arm)
        yield f, DT
        phase = _step(phase, cfg.speed, 1.8)


@register("lightning", "Electric storm — random bolts on an ambient glow")
def lightning(cfg: AnimationConfig) -> FrameStream:
    """Loopable lightning: a low ambient glow with stochastic bolts that
    stay bright for a few frames then fade."""
    rng = random.Random()
    bolt_keys: list[int] = []
    bolt_age = 999
    while True:
        primary, secondary = cfg.primary, cfg.secondary
        ambient = _dim(secondary, 0.12)
        f: Frame = {a: ambient for a in ALL_KEYS}

        # Maybe spawn a new bolt
        if bolt_age > 12 and rng.random() < 0.10:
            bolt_age = 0
            col_idx = rng.randint(2, len(_SORTED_COLS) - 3)
            bolt_keys = []
            for _ in _SORTED_ROWS:
                col_idx = max(0, min(len(_SORTED_COLS) - 1,
                                     col_idx + rng.randint(-2, 2)))
                bolt_keys.extend(_COLUMNS[_SORTED_COLS[col_idx]])
                if col_idx > 0:
                    bolt_keys.extend(_COLUMNS[_SORTED_COLS[col_idx - 1]])

        if bolt_age < 18:
            if bolt_age < 2:
                col = (255, 255, 255)
            elif bolt_age < 6:
                col = primary
            else:
                col = _dim(primary, max(0.0, 1.0 - (bolt_age - 6) / 12.0))
            for addr in bolt_keys:
                f[addr] = col

        yield f, DT
        bolt_age += max(0.1, cfg.speed)


@register("tide", "Ocean tide — sine-modulated waves washing across")
def tide(cfg: AnimationConfig) -> FrameStream:
    range_x = (_X_MAX - _X_MIN) + 6.0
    phase = 0.0
    while True:
        primary, secondary = cfg.primary, cfg.secondary
        wave_x = (phase % 1.0) * range_x + _X_MIN - 3.0
        wave_color = primary if int(phase) % 2 == 0 else secondary
        f: Frame = {}
        for a, kx in _KX.items():
            ky = _KY[a]
            front = wave_x + math.sin(ky * 0.8) * 1.5
            d = kx - front
            if -1.5 < d < 0:
                b = 1.0 - abs(d) / 1.5
                f[a] = _lerp(wave_color, (255, 255, 255), b * 0.7)
            elif -6 < d < -1.5:
                b = max(0.1, 1.0 - (abs(d) - 1.5) / 4.5)
                f[a] = _dim(wave_color, b)
            else:
                f[a] = OFF
        yield f, DT
        phase = _step(phase, cfg.speed, 0.35)


@register("wave", "Color wave — sweeping across continuously")
def wave(cfg: AnimationConfig) -> FrameStream:
    n = len(_SORTED_COLS)
    trail = 5
    cycle = n + trail
    phase = 0.0
    while True:
        primary, secondary = cfg.primary, cfg.secondary
        head = phase % cycle
        f: Frame = {a: OFF for a in ALL_KEYS}
        for i, col_x in enumerate(_SORTED_COLS):
            d = head - i
            if 0 <= d < trail:
                if d < 1:
                    color = (255, 255, 255)
                else:
                    blend = i / max(n - 1, 1)
                    base = _lerp(primary, secondary, blend)
                    fade = 1.0 - (d / trail) * 0.7
                    color = _dim(base, fade)
                for addr in _COLUMNS[col_x]:
                    f[addr] = color
        yield f, DT
        phase = _step(phase, cfg.speed, 14.0)


@register("sparkle", "Sparkling keys — twinkling lights")
def sparkle(cfg: AnimationConfig) -> FrameStream:
    rng = random.Random()
    state: Frame = {a: OFF for a in ALL_KEYS}
    active: dict[int, int] = {}
    while True:
        primary, secondary = cfg.primary, cfg.secondary
        palette = [
            primary, secondary,
            _lerp(primary, secondary, 0.5),
            _lerp(primary, (255, 255, 255), 0.5),
            _lerp(secondary, (255, 255, 255), 0.5),
            (255, 255, 255),
        ]

        # Spawn rate scales with speed.
        spawn = max(1, int(2 * max(0.5, cfg.speed)))
        for _ in range(spawn):
            if rng.random() < 0.7:
                addr = rng.choice(ALL_KEYS)
                if addr not in active:
                    state[addr] = rng.choice(palette)
                    active[addr] = max(1, int(rng.randint(4, 12) / max(0.2, cfg.speed)))

        to_remove = []
        for addr, ticks in list(active.items()):
            if ticks <= 1:
                state[addr] = OFF
                to_remove.append(addr)
            else:
                active[addr] = ticks - 1
        for addr in to_remove:
            del active[addr]

        yield dict(state), DT


@register("explosion", "Pulsating burst — rings expanding from center")
def explosion(cfg: AnimationConfig) -> FrameStream:
    """Loopable explosion: continuous rings perpetually emanating outward."""
    radii: dict[int, float] = {
        a: math.hypot(_KX[a] - _CX, _KY[a] - _CY)
        for a in _KX
    }
    phase = 0.0
    while True:
        primary, secondary = cfg.primary, cfg.secondary
        f: Frame = {}
        for a, r in radii.items():
            r_norm = r / max(_MAX_RADIUS, 0.001)
            # Multiple concentric rings traveling outward.
            best_b = 0.0
            for ring in range(3):
                front = (phase - ring * 0.33) % 1.0
                ring_dist = abs(r_norm - front)
                if ring_dist < 0.18:
                    b = (1.0 - ring_dist / 0.18) ** 2
                    if b > best_b:
                        best_b = b
            color = _lerp(primary, secondary, r_norm)
            if best_b > 0.5:
                color = _lerp(color, (255, 255, 255), 0.5)
            f[a] = _dim(color, max(0.15, best_b))
        yield f, DT
        phase = _step(phase, cfg.speed, 0.8)


@register("matrix", "Digital rain — falling code drops")
def matrix(cfg: AnimationConfig) -> FrameStream:
    rng = random.Random()
    cols_by_x: dict[float, list] = {}
    for k in G915X_KEYS:
        col = round(k.x * 2) / 2
        cols_by_x.setdefault(col, []).append(k)
    for col in cols_by_x.values():
        col.sort(key=lambda k: k.y)
    col_xs = sorted(cols_by_x)
    drops: dict[float, float] = {}    # col_x -> head_row (float)
    state: Frame = {a: OFF for a in ALL_KEYS}

    while True:
        primary, secondary = cfg.primary, cfg.secondary
        head_color = _lerp(primary, (255, 255, 255), 0.5)
        body_color = primary
        dim_body = _dim(primary, 0.5)

        # Spawn new drops occasionally.
        if rng.random() < 0.12:
            col_x = rng.choice(col_xs)
            if col_x not in drops:
                drops[col_x] = -1.0

        # Decay current keys to make trails (cheap fade).
        for a in list(state):
            r, g, b = state[a]
            state[a] = (max(0, r - 12), max(0, g - 12), max(0, b - 12))

        finished = []
        for col_x, head in drops.items():
            col = cols_by_x[col_x]
            ihead = int(head)
            use_secondary = int(col_x * 2) % 5 == 0
            hc = _lerp(secondary, (255, 255, 255), 0.5) if use_secondary else head_color
            bc = secondary if use_secondary else body_color
            dc = _dim(secondary, 0.5) if use_secondary else dim_body
            if ihead < len(col):
                if ihead >= 0:
                    state[col[ihead].address] = hc
                if ihead - 1 >= 0:
                    state[col[ihead - 1].address] = bc
                if ihead - 2 >= 0:
                    state[col[ihead - 2].address] = dc
                drops[col_x] = head + 0.6 * max(0.3, cfg.speed)
            else:
                finished.append(col_x)
        for col_x in finished:
            del drops[col_x]

        yield dict(state), DT


@register("fire", "Fire — flames rising from the bottom row")
def fire(cfg: AnimationConfig) -> FrameStream:
    cols = sorted({round(x) for x in _KX.values()})
    heat = {c: 0.6 for c in cols}
    rng = random.Random()
    phase = 0.0
    while True:
        for c in cols:
            heat[c] += (0.25 + rng.random() * 0.95 - heat[c]) * min(1.0, 0.35 * cfg.speed)
        f: Frame = {}
        for a, x in _KX.items():
            c = round(x)
            near = (heat[c] * 2 + heat.get(c - 1, heat[c]) + heat.get(c + 1, heat[c])) / 4
            up = (_Y_MAX - _KY[a]) / (_Y_MAX - _Y_MIN)
            h = near * 1.35 - up + 0.12 * math.sin(phase + x * 1.7 + up * 6)
            h = max(0.0, min(1.0, h))
            f[a] = _dim(_lerp(cfg.primary, cfg.secondary, h * 1.4 - 0.4), min(1.0, h * 1.5))
        yield f, DT
        phase = _step(phase, cfg.speed, 5.0)


@register("plasma", "Plasma — slow drifting blends")
def plasma(cfg: AnimationConfig) -> FrameStream:
    phase = 0.0
    while True:
        f: Frame = {}
        for a, x in _KX.items():
            y = _KY[a]
            v = (math.sin(x * 0.5 + phase) + math.sin(y + phase * 1.4 + x * 0.2)
                 + math.sin(math.hypot(x - _CX, y - _CY) * 0.6 - phase)) / 3
            f[a] = _lerp(cfg.primary, cfg.secondary, 0.5 + 0.5 * v)
        yield f, DT
        phase = _step(phase, cfg.speed, 1.2)


@register("starfield", "Starfield — stars streaming from the center")
def starfield(cfg: AnimationConfig) -> FrameStream:
    rng = random.Random()
    stars: list[list[float]] = []
    while True:
        if len(stars) < 28:
            stars.append([rng.uniform(0, math.tau), 0.3])
        glow = dict.fromkeys(_KX, 0.0)
        for star in stars:
            star[1] += DT * cfg.speed * (1.0 + star[1])
            sx = _CX + math.cos(star[0]) * star[1]
            sy = _CY + math.sin(star[0]) * star[1] * 0.45
            for a, x in _KX.items():
                d = math.hypot(x - sx, _KY[a] - sy)
                if d < 0.9:
                    glow[a] = max(glow[a], 1 - d / 0.9)
        stars = [s for s in stars if s[1] < _MAX_RADIUS + 1]
        yield {a: _dim(_lerp(cfg.primary, cfg.secondary,
                             math.hypot(_KX[a] - _CX, _KY[a] - _CY) / _MAX_RADIUS), g)
               for a, g in glow.items()}, DT


@register("rainbow", "Rainbow — the full spectrum sweeping across")
def rainbow(cfg: AnimationConfig) -> FrameStream:
    phase = 0.0
    while True:
        f: Frame = {}
        for a, x in _KX.items():
            r, g, b = colorsys.hsv_to_rgb(((x + _KY[a]) / 24 - phase) % 1.0, 1.0, 1.0)
            f[a] = (int(r * 255), int(g * 255), int(b * 255))
        yield f, DT
        phase = _step(phase, cfg.speed, 0.15) % 1.0


# =====================================================================
# Runner — drives a stream against a callback, with cancellation
# =====================================================================

class AnimationRunner:
    """Plays an animation in a worker thread.

    * ``play(name, cfg, loop=True)`` — run forever (ambient).
    * ``play(name, cfg, duration=N)`` — stop after N seconds (startup).
    * Edits to ``cfg`` are picked up live by animations on their next frame.
    * A new ``play`` cancels any in-flight animation cleanly.
    """

    # Short stop timeout: the longest dt is ~33 ms, so the worker reaches
    # its cancel.wait poll quickly. Keeping this small avoids the GUI
    # having to wait noticeably even on the rare cases we still restart.
    STOP_TIMEOUT = 0.5

    def __init__(self, on_frame: Callable[[Frame], None]):
        self._on_frame = on_frame
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._cfg: AnimationConfig | None = None

    @property
    def cfg(self) -> AnimationConfig | None:
        return self._cfg

    def play(self, name: str, cfg: AnimationConfig,
             *, loop: bool = False, duration: float | None = None) -> None:
        if name not in ANIMATIONS:
            return
        with self._lock:
            self._stop_locked()
            self._cancel = threading.Event()
            cancel = self._cancel
            self._cfg = cfg
            self._thread = threading.Thread(
                target=self._run,
                args=(name, cfg, loop, duration, cancel),
                daemon=True,
                name=f"animation-{name}",
            )
            self._thread.start()

    def stop(self, *, timeout: float | None = None) -> None:
        with self._lock:
            self._stop_locked(timeout=timeout)

    def is_running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    def update_config(self, **kwargs) -> None:
        """Mutate the running cfg in place (live edit). No-op when idle."""
        if self._cfg is None:
            return
        for k, v in kwargs.items():
            if hasattr(self._cfg, k):
                setattr(self._cfg, k, v)

    def _stop_locked(self, *, timeout: float | None = None) -> None:
        self._cancel.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=timeout if timeout is not None else self.STOP_TIMEOUT)
        self._thread = None

    def _run(self, name: str, cfg: AnimationConfig, loop: bool,
             duration: float | None, cancel: threading.Event) -> None:
        factory = ANIMATIONS[name][1]
        started = time.monotonic()
        while not cancel.is_set():
            for frame, dt in factory(cfg):
                if cancel.is_set():
                    return
                if duration is not None and (time.monotonic() - started) >= duration:
                    return
                self._on_frame(_scale_frame(frame, cfg.intensity))
                if dt > 0 and cancel.wait(dt):
                    return
            if not loop:
                return
