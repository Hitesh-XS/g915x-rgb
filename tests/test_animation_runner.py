"""AnimationRunner: cancellation, playback, and looping behavior."""

from __future__ import annotations

import threading
import time

import pytest

from g915x_rgb.animation import (
    ANIMATIONS,
    AnimationConfig,
    AnimationRunner,
)
from g915x_rgb.keyboard_layout import ALL_ADDRESSES, KEY_BY_ADDRESS


def _record_runner():
    received: list = []
    lock = threading.Lock()

    def on_frame(frame):
        with lock:
            received.append(frame)

    return received, lock, AnimationRunner(on_frame=on_frame)


def _wait(condition, seconds=3.0):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    return condition()


def test_duration_ends_a_one_shot():
    received, _, runner = _record_runner()
    runner.play("pulse", AnimationConfig(), duration=0.3)
    assert _wait(lambda: not runner.is_running())
    assert received


def test_play_replaces_previous_animation():
    _, _, runner = _record_runner()
    runner.play("aurora", AnimationConfig())
    first = runner._thread
    runner.play("pulse", AnimationConfig(), duration=0.2)
    assert not first.is_alive()
    assert _wait(lambda: not runner.is_running())


@pytest.mark.parametrize("name", sorted(ANIMATIONS))
def test_every_animation_yields_complete_valid_frames(name):
    stream = ANIMATIONS[name][1](AnimationConfig(speed=3.0))
    frames = [next(stream)[0] for _ in range(150)]
    for frame in frames:
        assert set(frame) == set(ALL_ADDRESSES)
        assert all(0 <= v <= 255 and isinstance(v, int) for c in frame.values() for v in c)
    assert any(f != frames[0] for f in frames)


def test_wave_trail_follows_its_head():
    stream = ANIMATIONS["wave"][1](AnimationConfig(primary=(255, 0, 0), secondary=(255, 0, 0)))
    for _ in range(8):
        frame = next(stream)[0]
    lit = [KEY_BY_ADDRESS[a].x for a, c in frame.items() if c != (0, 0, 0)]
    head = [KEY_BY_ADDRESS[a].x for a, c in frame.items() if c == (255, 255, 255)]
    assert head and max(lit) <= max(head) and min(lit) < min(head)


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
