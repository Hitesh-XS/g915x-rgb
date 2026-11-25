"""Layered render loop driving the keyboard at a fixed FPS.

Layers (bottom to top):

1. **base**       — the static profile colors set by ``set_base``.
2. **ambient**    — the latest frame yielded by a looping
                    :class:`~g915x_rgb.animation.AnimationRunner`. When
                    present it shadows ``base``.
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

# Note: ``threading`` import above is intentionally also used by
# play_one_shot's watcher thread.

from .animation import AnimationConfig, AnimationRunner, Frame, RGB, _lerp
from .transitions import TRANSITIONS, TransitionConfig

logger = logging.getLogger("g915x.renderer")

# A reactive source returns ``{address: (color, alpha)}`` where ``alpha``
# is in [0, 1] and controls how strongly the overlay color blends over
# the underlying layer (base or ambient). Sparse — only the affected
# keys appear in the dict.
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
    ambient_frame: Frame | None = None
    ambient_runner: AnimationRunner | None = None
    transition: _Transition | None = None
    reactive: ReactiveSource | None = None


class Renderer:
    """Composes layers and pushes deltas to the keyboard backend."""

    def __init__(self, kb, *, fps: float = 15.0) -> None:
        self._kb = kb
        self._period = 1.0 / fps
        self._state = _State()
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._filter: Callable[[Frame], Frame] | None = None
        self._last_sent: Frame = {}
        self._ambient_name = ""
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
        # Detach the ambient runner under the lock, then stop it WITHOUT
        # the lock held — the runner's worker thread takes our lock to
        # deliver frames, so joining it under the lock deadlocks.
        with self._lock:
            runner = self._state.ambient_runner
            self._state.ambient_runner = None
        if runner is not None:
            runner.stop()
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
        # Detach the previous runner first so we can stop it WITHOUT
        # holding self._lock; its worker thread takes self._lock from
        # _on_frame, and joining-under-lock would deadlock.
        with self._lock:
            old = self._state.ambient_runner
            self._state.ambient_runner = None
            self._state.ambient_frame = None
        if old is not None:
            old.stop()

        # Tag the new runner so late frames from a not-quite-stopped
        # predecessor can't overwrite ambient_frame after we've moved on.
        runner_box: list = []

        def _on_frame(f: Frame) -> None:
            with self._lock:
                if runner_box and self._state.ambient_runner is runner_box[0]:
                    self._state.ambient_frame = dict(f)

        runner = AnimationRunner(on_frame=_on_frame)
        runner_box.append(runner)
        with self._lock:
            self._state.ambient_runner = runner
            self._ambient_name = name
        runner.play(name, cfg, loop=True)

    def stop_ambient(self) -> None:
        with self._lock:
            runner = self._state.ambient_runner
            self._state.ambient_runner = None
            self._state.ambient_frame = None
        if runner is not None:
            runner.stop()

    def update_ambient_config(self, **kwargs) -> AnimationConfig | None:
        """Live-update the running ambient cfg without restarting.

        Returns the runner's cfg (so the caller can compare-and-decide
        elsewhere). Returns None if no ambient is running.
        """
        with self._lock:
            runner = self._state.ambient_runner
        if runner is None:
            return None
        runner.update_config(**kwargs)
        return runner.cfg

    def ambient_name(self) -> str:
        """Name of the looping animation, or "" (also during a one-shot)."""
        with self._lock:
            return self._ambient_name if self._state.ambient_runner is not None else ""

    def has_ambient(self) -> bool:
        with self._lock:
            return self._state.ambient_runner is not None

    def play_one_shot(self, name: str, cfg: AnimationConfig,
                      *, duration: float = 3.0,
                      on_done: Callable[[], None] | None = None) -> None:
        """Run a finite animation in the ambient slot, fire ``on_done`` after.

        Used for startup animations: takes over the ambient layer,
        plays for ``duration`` seconds, then yields back to whoever set
        the next ambient/base.
        """
        # Reuse the ambient slot but with duration-capped playback.
        with self._lock:
            old = self._state.ambient_runner
            self._state.ambient_runner = None
            self._state.ambient_frame = None
        if old is not None:
            old.stop()

        runner_box: list = []

        def _on_frame(f: Frame) -> None:
            with self._lock:
                if runner_box and self._state.ambient_runner is runner_box[0]:
                    self._state.ambient_frame = dict(f)

        runner = AnimationRunner(on_frame=_on_frame)
        runner_box.append(runner)
        with self._lock:
            self._state.ambient_runner = runner
            self._ambient_name = ""

        # Wrap on_done so the ambient slot self-clears when the one-shot ends.
        def _done_thread():
            t = runner._thread
            if t is not None:
                t.join()
            with self._lock:
                if self._state.ambient_runner is runner:
                    self._state.ambient_runner = None
                    self._state.ambient_frame = None
            if on_done is not None:
                try:
                    on_done()
                except Exception:
                    logger.exception("play_one_shot on_done failed")

        runner.play(name, cfg, duration=duration)
        threading.Thread(target=_done_thread, daemon=True,
                         name="one-shot-watcher").start()

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
            amb = dict(self._state.ambient_frame) if self._state.ambient_frame else None
            tx = self._state.transition
            reactive = self._state.reactive

        target = amb if amb is not None else base

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
