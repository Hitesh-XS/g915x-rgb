"""User-idle notifications through Wayland's ext-idle-notify protocol.

KDE's D-Bus idle time call does not work on Wayland, and reading input
devices just to detect activity would be intrusive. This speaks the few
protocol messages needed to have the compositor report idleness instead.
"""

from __future__ import annotations

import logging
import os
import socket
import struct
from collections.abc import Callable

logger = logging.getLogger("g915x.idle")

_DISPLAY, _REGISTRY, _SYNC, _SEAT, _NOTIFIER, _NOTIFICATION = 1, 2, 3, 4, 5, 6


def _string(text: str) -> bytes:
    raw = text.encode() + b"\0"
    return struct.pack("=I", len(raw)) + raw + b"\0" * (-len(raw) % 4)


class IdleWatcher:
    """Calls ``on_change(True)`` when the user goes idle, ``False`` on return."""

    def __init__(self, seconds: int, on_change: Callable[[bool], None]) -> None:
        self._ms = seconds * 1000
        self._on_change = on_change
        self._buf = b""
        self._globals: dict[str, int] = {}
        display = os.environ.get("WAYLAND_DISPLAY", "wayland-0")
        path = os.path.join(os.environ.get("XDG_RUNTIME_DIR", ""), display)
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self._sock.connect(path)
            self._request(_DISPLAY, 1, struct.pack("=I", _REGISTRY))
            self._request(_DISPLAY, 0, struct.pack("=I", _SYNC))
        except OSError:
            self._sock.close()
            raise

    def fileno(self) -> int:
        return self._sock.fileno()

    def close(self) -> None:
        self._sock.close()

    def _request(self, obj: int, opcode: int, args: bytes) -> None:
        self._sock.sendall(struct.pack("=II", obj, (len(args) + 8) << 16 | opcode) + args)

    def _bind(self, interface: str, new_id: int) -> None:
        self._request(_REGISTRY, 0, struct.pack("=I", self._globals[interface])
                      + _string(interface) + struct.pack("=II", 1, new_id))

    def dispatch(self) -> bool:
        """Handle pending events. False when the connection is finished."""
        try:
            data = self._sock.recv(65536)
        except OSError:
            return False
        if not data:
            return False
        self._buf += data
        while len(self._buf) >= 8:
            obj, word = struct.unpack_from("=II", self._buf)
            size, opcode = word >> 16, word & 0xFFFF
            if size < 8:
                return False
            if len(self._buf) < size:
                break
            body, self._buf = self._buf[8:size], self._buf[size:]
            if not self._event(obj, opcode, body):
                return False
        return True

    def _event(self, obj: int, opcode: int, body: bytes) -> bool:
        if obj == _REGISTRY and opcode == 0:
            name, length = struct.unpack_from("=II", body)
            self._globals[body[8:8 + length - 1].decode()] = name
        elif obj == _SYNC:
            if not {"wl_seat", "ext_idle_notifier_v1"} <= self._globals.keys():
                logger.info("compositor has no idle notifications")
                return False
            self._bind("wl_seat", _SEAT)
            self._bind("ext_idle_notifier_v1", _NOTIFIER)
            self._request(_NOTIFIER, 1, struct.pack("=III", _NOTIFICATION, self._ms, _SEAT))
        elif obj == _NOTIFICATION:
            self._on_change(opcode == 0)
        elif obj == _DISPLAY and opcode == 0:
            logger.warning("idle watcher: protocol error")
            return False
        return True
