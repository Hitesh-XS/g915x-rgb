"""Apply a saved profile from the command line: ``g915x-rgb-apply [name]``."""

from __future__ import annotations

import sys

import dbus

from .config import get_last_profile

BUS_NAME = "org.g915xrgb.Controller"


def main() -> int:
    name = " ".join(sys.argv[1:]) or get_last_profile() or "Default"
    try:
        # D-Bus starts the service if it is not running.
        proxy = dbus.SessionBus().get_object(BUS_NAME, "/org/g915xrgb/Controller")
        daemon = dbus.Interface(proxy, BUS_NAME)
        daemon.ReloadProfiles()
        ok = daemon.SetProfile(name, "")
    except dbus.DBusException as e:
        print(f"Could not reach the lighting service: {e.get_dbus_message()}", file=sys.stderr)
        return 1
    if not ok:
        print(f"No profile named {name!r}", file=sys.stderr)
        return 1
    print(f"Applied {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
