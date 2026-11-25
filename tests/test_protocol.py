"""HID++ messages, driven against a fake keyboard."""

from __future__ import annotations

import errno
import random

import pytest

import g915x_rgb.backend as backend
from g915x_rgb.backend import (
    DEVICE_INDEX,
    FUNC_COMMIT,
    FUNC_SET_BATCH,
    FUNC_SET_CONSECUTIVE,
    FUNC_SET_KEY_COLOR,
    G915XBackend,
    plan_frame,
)
from g915x_rgb.keyboard_layout import ALL_ADDRESSES


class Wire:
    """Answers each write the way the keyboard does."""

    def __init__(self):
        self.writes, self.inbox, self.silent = [], [], 0

    def write(self, _fd, data):
        self.writes.append(bytes(data))
        if self.silent:
            self.silent -= 1
        else:
            self.inbox.append(bytes(data[:4]) + bytes(16))
        return len(data)

    def read(self, *_a):
        if not self.inbox:
            raise BlockingIOError
        return self.inbox.pop(0)

    def select(self, *_a):
        return ([1] if self.inbox else [], [], [])


@pytest.fixture
def kb(monkeypatch):
    wire = Wire()
    monkeypatch.setattr(backend.os, "write", wire.write)
    monkeypatch.setattr(backend.os, "read", wire.read)
    monkeypatch.setattr(backend.select, "select", wire.select)
    b = G915XBackend()
    b._fd, b._pkb_index = 999, 7
    return b, wire


def decode(messages):
    out = {}
    for fn, p in messages:
        if fn == FUNC_SET_KEY_COLOR:
            out.update({p[i]: tuple(p[i + 1:i + 4]) for i in range(0, 16, 4)})
        elif fn == FUNC_SET_CONSECUTIVE:
            out.update({p[0] + i: tuple(p[1 + 3 * i:4 + 3 * i]) for i in range(5)})
        elif fn == FUNC_SET_BATCH:
            out.update({a: tuple(p[:3]) for a in p[3:]})
    return out


@pytest.mark.parametrize("colors", [1, 2, 8, 40, 10**6])
def test_plan_sets_exactly_the_requested_keys(colors):
    rng = random.Random(colors)
    for _ in range(60):
        keys = rng.sample(ALL_ADDRESSES, rng.randint(1, len(ALL_ADDRESSES)))
        frame = {a: ((n := rng.randrange(colors)) % 256, n // 256 % 256, n // 65536) for a in keys}
        plan = plan_frame(frame)
        assert decode(plan) == frame
        assert all(len(p) == 16 for _, p in plan)


def test_plan_is_compact():
    assert len(plan_frame(dict.fromkeys(ALL_ADDRESSES, (1, 2, 3)))) <= 10
    assert len(plan_frame({a: (a, 0, 0) for a in ALL_ADDRESSES})) <= 28


def test_a_frame_is_committed_once_and_last(kb):
    b, wire = kb
    b.apply_key_colors({a: (a, 0, 0) for a in ALL_ADDRESSES})
    functions = [m[3] & 0xF0 for m in wire.writes]
    assert functions.count(FUNC_COMMIT) == 1 and functions[-1] == FUNC_COMMIT
    assert all(len(m) == 20 and m[:3] == bytes([0x11, DEVICE_INDEX, 7]) for m in wire.writes)


def test_every_write_waits_for_its_reply(kb):
    b, wire = kb
    original = wire.write

    def write(fd, data):
        assert not wire.inbox
        return original(fd, data)

    backend.os.write = write
    b.apply_key_colors({a: (a, 0, 0) for a in ALL_ADDRESSES})


def test_lost_reply_is_retried_then_reported(kb):
    b, wire = kb
    wire.silent = 1
    b.commit()
    assert len(wire.writes) == 2
    wire.silent = 2
    with pytest.raises(OSError):
        b.commit()


def test_write_errors_are_reported(kb, monkeypatch):
    def gone(*_a):
        raise OSError(errno.ENODEV, "gone")

    monkeypatch.setattr(backend.os, "write", gone)
    with pytest.raises(OSError):
        kb[0].commit()


def test_unrelated_packets_are_skipped(kb, monkeypatch):
    b, wire = kb

    def write(_fd, _data):
        wire.inbox += [bytes([0x11, DEVICE_INDEX, 7, 0x70] + [0] * 16),
                       bytes([0x10, DEVICE_INDEX, 5, 0x1A, 77, 0, 0])]

    monkeypatch.setattr(backend.os, "write", write)
    assert b._send_recv([0x10, DEVICE_INDEX, 5, 0x1A, 0, 0, 0])[4] == 77


def test_refusal_is_not_a_value(kb, monkeypatch):
    b, wire = kb
    monkeypatch.setattr(backend.os, "write", lambda *_a: wire.inbox.append(
        bytes([0x11, DEVICE_INDEX, 0xFF, 5, 0x1A, 2, 0])))
    assert b._send_recv([0x10, DEVICE_INDEX, 5, 0x1A, 0, 0, 0]) is None
