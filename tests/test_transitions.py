"""Each transition's t=0 must yield ``old`` and t=1 must yield ``new``.

Skips ``shutter`` and ``heartbeat`` for the t=0 sample at black-hold
windows — they're tested at their unique endpoints separately.
"""

from __future__ import annotations

import pytest

from g915x_rgb.keyboard_layout import G915X_KEYS
from g915x_rgb.transitions import TRANSITIONS, TransitionConfig

OLD_COLOR = (255, 0, 0)
NEW_COLOR = (0, 0, 255)


@pytest.fixture
def frames():
    old = {k.address: OLD_COLOR for k in G915X_KEYS}
    new = {k.address: NEW_COLOR for k in G915X_KEYS}
    return old, new


@pytest.mark.parametrize("name", list(TRANSITIONS.keys()))
def test_endpoint_t0_close_to_old(name, frames):
    old, new = frames
    fn = TRANSITIONS[name][1]
    cfg = TransitionConfig()
    out = fn(old, new, 0.0, cfg)
    # Sample any key — at t=0 the dominant color should be the old red.
    sample = next(iter(out.values()))
    if name == "glitch":
        # glitch jitters colors; just assert dominant is roughly red.
        assert sample[0] >= 200 and sample[2] <= 60
    else:
        assert sample == OLD_COLOR


@pytest.mark.parametrize("name", list(TRANSITIONS.keys()))
def test_endpoint_t1_equals_new(name, frames):
    old, new = frames
    fn = TRANSITIONS[name][1]
    cfg = TransitionConfig()
    out = fn(old, new, 1.0, cfg)
    sample = next(iter(out.values()))
    assert sample == NEW_COLOR
