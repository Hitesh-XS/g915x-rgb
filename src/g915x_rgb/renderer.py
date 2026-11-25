"""Layered render loop driving the keyboard at a fixed FPS.

Layers (bottom to top):

1. **base**       — the static profile colors set by ``set_base``.
2. **ambient**    — a playing :class:`~g915x_rgb.animation.AnimationSource`.
                    When present it shadows ``base``.
3. **transition** — interpolates between a snapshot of what was visible
                    when the transition began and the current target
                    layer; supersedes both ``base`` and ``ambient`` for
                    its duration.
4. **reactive**   — sparse overlay of typing effects rendered on top.

The renderer owns the only handle to the backend in the daemon process.
It runs in its own thread at ``fps`` Hz and only sends a HID++ batch
when the composed frame actually differs from what was last sent —
delta updates keep idle CPU near zero.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .animation import AnimationConfig, AnimationSource, Frame, RGB, _lerp
from .transitions import TRANSITIONS, TransitionConfig

logger = logging.getLogger("g915x.renderer")

# A reactive source returns ``{address: (color, alpha)}`` where ``alpha``
# is in [0, 1] and controls how strongly the overlay color blends over
# the underlying layer (base or ambient). Sparse — only the affected
# keys appear in the dict.
# Smallest channel change worth sending while something is moving.
MIN_CHANGE = 5

ReactiveSource = Callable[[float], dict[int, tuple[RGB, float]]]


@dataclass
class _Transition:
    name: str
    fn: Callable
    cfg: TransitionConfig
    old_frame: Frame
    started_at: float
    duration: float
    on_complete: Callable[[], None] | None = None


@dataclass
class _State:
    base: Frame = field(default_factory=dict)
    ambient: AnimationSource | None = None
    ambient_ends: float | None = None
    ambient_done: Callable[[], None] | None = None
    transition: _Transition | None = None
    reactive: ReactiveSource | None = None


class Renderer:
    """Composes layers and pushes deltas to the keyboard backend."""

    def __init__(self, kb, *, fps: float = 20.0) -> None:
        self._kb = kb
        self._period = 1.0 / fps
        self._state = _State()
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._filter: Callable[[Frame], Frame] | None = None
        self._last_sent: Frame = {}
        self._moving = False
        self._failing = False

    # ---------- lifecycle ----------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._cancel = threading.Event()
        # The keyboard may have lost its colors since the last frame.
        self.invalidate()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="renderer")
        self._thread.start()

    def stop(self, *, timeout: float = 2.0) -> None:
        self._cancel.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=timeout)
        self._thread = None

    def invalidate(self) -> None:
        """Forget what was sent, so the next frame is sent in full."""
        with self._lock:
            self._last_sent = {}

    def set_filter(self, fn: Callable[[Frame], Frame] | None) -> None:
        """Final pass over each frame: dimming, blanking, indicators."""
        with self._lock:
            self._filter = fn

    # ---------- layer setters ----------
    def set_base(self, frame: Frame) -> None:
        with self._lock:
            self._state.base = dict(frame)

    def start_ambient(self, name: str, cfg: AnimationConfig) -> None:
        source = AnimationSource(name, cfg)
        with self._lock:
            self._state.ambient = source
            self._state.ambient_ends = None

    def stop_ambient(self) -> None:
        with self._lock:
            self._state.ambient = None
            self._state.ambient_ends = None

    def update_ambient_config(self, **kwargs) -> None:
        """Change the running animation's colors or speed in place."""
        with self._lock:
            if self._state.ambient is not None:
                for k, v in kwargs.items():
                    setattr(self._state.ambient.cfg, k, v)

    def ambient_name(self) -> str:
        """Name of the looping animation, or "" (also during a one-shot)."""
        with self._lock:
            looping = self._state.ambient is not None and self._state.ambient_ends is None
            return self._state.ambient.name if looping else ""

    def play_one_shot(self, name: str, cfg: AnimationConfig,
                      *, duration: float = 3.0,
                      on_done: Callable[[], None] | None = None) -> None:
        """Play an animation for ``duration`` seconds, then call ``on_done``.

        Nothing is called if something else replaces it first.
        """
        source = AnimationSource(name, cfg)
        with self._lock:
            self._state.ambient = source
            self._state.ambient_ends = time.monotonic() + duration
            self._state.ambient_done = on_done

    def start_transition(
        self,
        name: str,
        target_frame: Frame,
        *,
        duration: float = 0.6,
        cfg: TransitionConfig | None = None,
        on_complete: Callable[[], None] | None = None,
    ) -> bool:
        """Begin a transition from the currently visible frame to ``target_frame``.

        Returns False if the transition name is unknown (and the target
        frame is applied immediately as if no transition was requested).
        """
        if name and name in TRANSITIONS:
            fn = TRANSITIONS[name][1]
        else:
            fn = None

        with self._lock:
            old = dict(self._last_sent) if self._last_sent else dict(self._state.base)
        if fn is None:
            self.set_base(target_frame)
            if on_complete is not None:
                on_complete()
            return False

        with self._lock:
            self._state.transition = _Transition(
                name=name,
                fn=fn,
                cfg=cfg or TransitionConfig(duration=duration),
                old_frame=old,
                started_at=time.monotonic(),
                duration=max(0.05, duration),
                on_complete=on_complete,
            )
        return True

    def set_reactive_source(self, source: ReactiveSource | None) -> None:
        with self._lock:
            self._state.reactive = source

    def visible_frame(self) -> Frame:
        """Snapshot of the most recent frame sent to the keyboard."""
        with self._lock:
            return dict(self._last_sent)

    # ---------- internals ----------
    def _compose(self, now: float) -> Frame:
        with self._lock:
            base = dict(self._state.base)
            done = None
            if self._state.ambient_ends is not None and now >= self._state.ambient_ends:
                done = self._state.ambient_done
                self._state.ambient = self._state.ambient_ends = None
                self._state.ambient_done = None
            source = self._state.ambient
            tx = self._state.transition
            reactive = self._state.reactive

        if done is not None:
            done()
        self._moving = source is not None or tx is not None
        target = source.frame(now) if source is not None else base

        if tx is not None:
            t = (now - tx.started_at) / tx.duration
            if t >= 1.0:
                done = tx
                with self._lock:
                    if self._state.transition is done:
                        self._state.transition = None
                if done.on_complete is not None:
                    try:
                        done.on_complete()
                    except Exception:
                        logger.exception("transition on_complete failed")
                content = target
            else:
                content = tx.fn(tx.old_frame, target, t, tx.cfg)
        else:
            content = target

        if reactive is not None:
            try:
                overlay = reactive(now)
            except Exception:
                logger.exception("reactive source raised")
                overlay = {}
            if overlay:
                self._moving = True
                merged = dict(content)
                for addr, (color, alpha) in overlay.items():
                    if alpha <= 0:
                        continue
                    base_c = merged.get(addr, (0, 0, 0))
                    if alpha >= 1.0:
                        merged[addr] = color
                    else:
                        merged[addr] = _lerp(base_c, color, alpha)
                content = merged
        with self._lock:
            post = self._filter
        return post(content) if post is not None else content

    def _diff_send(self, frame: Frame) -> None:
        with self._lock:
            last = self._last_sent
        if self._moving:
            # Invisible changes would cost frame rate. The comparison is with
            # what was sent, so small steps add up and are sent eventually.
            diff = {a: c for a, c in frame.items()
                    if a not in last or (c != last[a] and not any(c)) or max(abs(x - y) for x, y in zip(c, last[a])) >= MIN_CHANGE}
        else:
            diff = {a: c for a, c in frame.items() if last.get(a) != c}
        if not diff:
            self._failing = False
            return
        try:
            self._kb.apply_key_colors(diff)
        except OSError as e:
            # Some of the batch may have landed: resend everything next time.
            self.invalidate()
            if not self._failing:
                logger.warning("keyboard write failed, retrying: %s", e)
            self._failing = True
            self._cancel.wait(0.5)
            return
        self._failing = False
        with self._lock:
            if self._last_sent is last:
                self._last_sent = {**last, **diff}

    def _loop(self) -> None:
        cancel = self._cancel
        next_tick = time.monotonic()
        while not cancel.is_set():
            now = time.monotonic()
            try:
                frame = self._compose(now)
                if frame:
                    self._diff_send(frame)
            except Exception:
                logger.exception("renderer tick raised")

            next_tick += self._period
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0:
                if cancel.wait(sleep_for):
                    return
            else:
                # Fell behind — reset cadence rather than catching up in a tight loop.
                next_tick = time.monotonic()
