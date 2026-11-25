"""AnimationRunner: cancellation, playback, and looping behavior."""

from __future__ import annotations

import threading
import time

from g915x_rgb.animation import (
    ANIMATIONS,
    AnimationConfig,
    AnimationRunner,
    register,
)


def _record_runner():
    received: list = []
    lock = threading.Lock()

    def on_frame(frame):
        with lock:
            received.append(frame)

    return received, lock, AnimationRunner(on_frame=on_frame)


def test_runs_a_known_animation_to_completion():
    received, _, runner = _record_runner()
    runner.play("pulse", AnimationConfig(speed=20.0))  # speed it up so the test is quick
    # pulse generates ~62 frames; allow generous timeout.
    deadline = time.monotonic() + 4.0
    while runner.is_running() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not runner.is_running()
    assert len(received) > 10


def test_play_cancels_previous_animation():
    received, lock, runner = _record_runner()
    runner.play("aurora", AnimationConfig(speed=0.5))   # slow, ~7s
    time.sleep(0.05)
    assert runner.is_running()

    runner.play("pulse", AnimationConfig(speed=20.0))
    deadline = time.monotonic() + 4.0
    while runner.is_running() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not runner.is_running()


def test_unknown_animation_is_a_noop():
    received, _, runner = _record_runner()
    runner.play("does-not-exist", AnimationConfig())
    time.sleep(0.05)
    assert not runner.is_running()
    assert received == []


def test_loop_keeps_yielding_frames(monkeypatch):
    """Custom one-yield animation that keeps producing in loop mode."""
    yielded_count = [0]

    def tiny(_cfg):
        yielded_count[0] += 1
        yield ({0x40: (1, 2, 3)}, 0.001)

    ANIMATIONS["__test_tiny__"] = ("test", tiny)
    try:
        received, _, runner = _record_runner()
        runner.play("__test_tiny__", AnimationConfig(speed=1.0), loop=True)
        time.sleep(0.05)
        runner.stop()
        # In loop mode, the factory must have been re-entered multiple times.
        assert yielded_count[0] > 1
    finally:
        ANIMATIONS.pop("__test_tiny__", None)
