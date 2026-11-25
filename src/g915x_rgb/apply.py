"""CLI shim that asks the daemon to apply a profile.

Usage::

    g915x-rgb-apply [profile_name]

Kept for backwards compat with the old systemd unit / shell habits;
real work happens in the daemon. If the daemon isn't running yet, this
launches it via D-Bus activation (or, as a last resort, by running
``g915x-rgb-daemon`` itself in the foreground for one-shot use).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

from .config import get_last_profile
from .daemon import DBUS_BUS_NAME, DBUS_OBJECT_PATH, DBUS_INTERFACE


def _client():
    try:
        import dbus
    except ImportError:
        print("dbus-python is required (Arch: pacman -S python-dbus)",
              file=sys.stderr)
        sys.exit(2)
    bus = dbus.SessionBus()
    proxy = bus.get_object(DBUS_BUS_NAME, DBUS_OBJECT_PATH)
    return dbus.Interface(proxy, DBUS_INTERFACE), dbus


def _ensure_daemon_running(retries: int = 6, delay: float = 0.5) -> bool:
    """Try to connect; if the daemon isn't there, start it and wait briefly."""
    iface, dbus_mod = _client()
    for attempt in range(retries):
        try:
            iface.GetStatus()
            return True
        except dbus_mod.exceptions.DBusException:
            if attempt == 0:
                # Spawn the daemon in the background.
                try:
                    subprocess.Popen(
                        [sys.executable, "-m", "g915x_rgb.daemon"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                except OSError as e:
                    print(f"could not spawn daemon: {e}", file=sys.stderr)
                    return False
            time.sleep(delay)
            iface, _ = _client()
    return False


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else (get_last_profile() or "Default")
    if not _ensure_daemon_running():
        print("daemon did not start", file=sys.stderr)
        return 1
    iface, _ = _client()
    ok = bool(iface.SetProfile(target, ""))
    if ok:
        print(f"applied profile: {target}")
        return 0
    print(f"failed to apply profile: {target}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
