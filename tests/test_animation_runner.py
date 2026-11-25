"""Animation frames and playback timing."""

from __future__ import annotations

import pytest

from g915x_rgb.animation import (
    ANIMATIONS,
    AnimationConfig,
    DT,
    AnimationSource,
)
from g915x_rgb.keyboard_layout import ALL_ADDRESSES, KEY_BY_ADDRESS


def _numbers(_cfg):
    n = 0
    while True:
        yield {1: (n, n, n)}, DT
        n += 30


@pytest.fixture
def numbers():
    ANIMATIONS["__numbers__"] = ("test", _numbers)
    yield "__numbers__"
    del ANIMATIONS["__numbers__"]


def test_playback_follows_time_not_call_rate(numbers):
    slow = AnimationSource(numbers, AnimationConfig())
    fast = AnimationSource(numbers, AnimationConfig())
    slow.frame(0.0)
    for i in range(200):
        last = fast.frame(i / 1000)
    # floating-point step boundaries may differ by a rounding step
    assert abs(slow.frame(0.199)[1][0] - last[1][0]) <= 1


def test_playback_blends_between_frames(numbers):
    source = AnimationSource(numbers, AnimationConfig())
    source.frame(0.0)
    assert 13 <= source.frame(DT / 2)[1][0] <= 16


def test_playback_does_not_race_after_a_pause(numbers):
    source = AnimationSource(numbers, AnimationConfig())
    source.frame(0.0)
    assert source.frame(60.0)[1][0] < 60


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
