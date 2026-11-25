"""HID++ protocol layer regression tests.

Drives :class:`G915XBackend` against an in-memory fake hidraw fd so we
can assert on the exact bytes that would hit the kernel. Catches the
two things most likely to silently break: the standard/special key
split inside ``apply_key_colors`` and the message framing for the
batch / range / single-key functions.
"""

from __future__ import annotations

import errno

import pytest

from g915x_rgb.backend import (
    DEVICE_INDEX,
    FUNC_COMMIT,
    FUNC_SET_BATCH,
    FUNC_SET_KEY_COLOR,
    FUNC_SET_RANGE,
    G915XBackend,
)


_patches: list[pytest.MonkeyPatch] = []


@pytest.fixture(autouse=True)
def _undo_patches():
    yield
    while _patches:
        _patches.pop().undo()


class _FakeFd:
    """A drop-in for an os.write/os.read target: collects everything written."""

    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write_all(self, data: bytes) -> None:
        self.writes.append(bytes(data))


def _backend(fd_value: int = 999) -> tuple[G915XBackend, _FakeFd]:
    kb = G915XBackend()
    kb._fd = fd_value
    kb._pkb_index = 0x07
    fake = _FakeFd()

    def fake_write(_fd, data):
        fake.write_all(data)
        return len(data)

    def fake_select(*_a, **_kw):
        return ([], [], [])

    def fake_read(*_a, **_kw):
        raise BlockingIOError

    mp = pytest.MonkeyPatch()
    _patches.append(mp)
    mp.setattr("g915x_rgb.backend.os.write", fake_write)
    mp.setattr("g915x_rgb.backend.select.select", fake_select)
    mp.setattr("g915x_rgb.backend.os.read", fake_read)
    return kb, fake


def test_set_key_color_emits_long_message_with_function_byte():
    kb, fake = _backend()
    kb.set_key_color(0x40, 1, 2, 3)
    assert len(fake.writes) == 1
    msg = fake.writes[0]
    assert msg[0] == 0x11
    assert msg[1] == DEVICE_INDEX
    assert msg[2] == kb._pkb_index
    assert msg[3] == FUNC_SET_KEY_COLOR
    assert msg[4:8] == bytes([0x40, 1, 2, 3])
    assert len(msg) == 20


def test_set_keys_color_batches_four_per_message():
    kb, fake = _backend()
    keys = [(i, 10, 20, 30) for i in range(0x40, 0x4A)]   # 10 keys
    kb.set_keys_color(keys)
    assert len(fake.writes) == 3   # 4 + 4 + 2
    assert fake.writes[0][3] == FUNC_SET_KEY_COLOR
    # First batch encodes 4 keys after the header.
    assert fake.writes[0][4:8] == bytes([0x40, 10, 20, 30])
    assert fake.writes[0][8:12] == bytes([0x41, 10, 20, 30])


def test_set_range_color_packs_one_message():
    kb, fake = _backend()
    kb.set_range_color(0x10, 0x20, 1, 2, 3)
    assert len(fake.writes) == 1
    msg = fake.writes[0]
    assert msg[3] == FUNC_SET_RANGE
    assert msg[4:9] == bytes([0x10, 0x20, 1, 2, 3])


def test_set_batch_color_packs_thirteen_per_message():
    kb, fake = _backend()
    addrs = list(range(0x40, 0x55))   # 21 keys
    kb.set_batch_color(addrs, 5, 5, 5)
    # 13 + 8
    assert len(fake.writes) == 2
    msg = fake.writes[0]
    assert msg[3] == FUNC_SET_BATCH
    assert msg[4:7] == bytes([5, 5, 5])
    assert list(msg[7:20]) == list(range(0x40, 0x4D))


def test_apply_key_colors_splits_standard_and_special():
    kb, fake = _backend()
    # 4 standard keys + 2 special (one modifier, one G-key)
    colors = {
        0x10: (10, 20, 30),
        0x11: (10, 20, 30),
        0x12: (10, 20, 30),
        0x13: (10, 20, 30),
        0x69: (1, 1, 1),    # LShift
        0xB4: (2, 2, 2),    # G1
    }
    kb.apply_key_colors(colors)
    funcs = [m[3] for m in fake.writes]
    # Standard batch -> SET_KEY_COLOR + COMMIT, then per-special: SET_KEY_COLOR + COMMIT (×2)
    assert funcs[0] == FUNC_SET_KEY_COLOR
    assert funcs[1] == FUNC_COMMIT
    # Special keys each emit their own pair
    assert funcs.count(FUNC_COMMIT) == 3
    assert any(m[4] == 0x69 for m in fake.writes)
    assert any(m[4] == 0xB4 for m in fake.writes)


def test_write_errors_are_reported():
    kb, _ = _backend()

    def gone(*_a):
        raise OSError(errno.ENODEV, "gone")

    _patches[-1].setattr("g915x_rgb.backend.os.write", gone)
    with pytest.raises(OSError):
        kb.commit()


def test_full_queue_is_retried():
    kb, fake = _backend()
    tries = []

    def busy(_fd, data):
        tries.append(1)
        if len(tries) < 3:
            raise BlockingIOError
        fake.write_all(data)

    _patches[-1].setattr("g915x_rgb.backend.os.write", busy)
    kb.commit()
    assert len(fake.writes) == 1


def test_reply_matching_skips_unrelated_packets():
    kb, _ = _backend()
    request = [0x10, DEVICE_INDEX, 0x05, 0x1A, 0, 0, 0]
    packets = [bytes([0x11, DEVICE_INDEX, 0x07, 0x70] + [0] * 16),
               bytes([0x10, DEVICE_INDEX, 0x05, 0x1A, 77, 0, 0])]
    polls = []

    def select_(*_a):
        polls.append(1)
        return ([1], [], []) if len(polls) > 1 else ([], [], [])

    _patches[-1].setattr("g915x_rgb.backend.select.select", select_)
    _patches[-1].setattr("g915x_rgb.backend.os.read", lambda *_a: packets.pop(0))
    assert kb._send_recv(request)[4] == 77
