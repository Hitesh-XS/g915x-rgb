"""GTK-side D-Bus client wrapping the daemon's controller interface.

If ``dbus-python`` isn't available the client degrades gracefully — calls
become no-ops and ``connected()`` returns ``False``. That lets the GUI
still launch on a system without the daemon (it just can't actually push
changes to the keyboard).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from .daemon import DBUS_BUS_NAME, DBUS_INTERFACE, DBUS_OBJECT_PATH
from .profile import Profile

logger = logging.getLogger("g915x.client")

try:
    import dbus
    import dbus.mainloop.glib
    HAVE_DBUS = True
except ImportError:
    dbus = None  # type: ignore[assignment]
    HAVE_DBUS = False


class DaemonClient:
    def __init__(self) -> None:
        self._iface = None
        self._proxy = None
        self._bus = None
        self._on_status: list[Callable[[dict], None]] = []
        self._on_profile_switched: list[Callable[[str, str], None]] = []

        if not HAVE_DBUS:
            logger.warning("dbus-python missing — daemon control disabled")
            return

        try:
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            self._bus = dbus.SessionBus()
            self._connect_proxy()
        except dbus.DBusException as e:
            logger.warning("could not initialize DBus client: %s", e)

    # ---------- connection state ----------
    def _connect_proxy(self) -> None:
        try:
            self._proxy = self._bus.get_object(DBUS_BUS_NAME, DBUS_OBJECT_PATH)
            self._iface = dbus.Interface(self._proxy, DBUS_INTERFACE)
            # Fire-and-forget probe so DBus auto-activates the service.
            self._iface.GetStatus()

            self._proxy.connect_to_signal(
                "StatusChanged", self._handle_status_changed,
                dbus_interface=DBUS_INTERFACE,
            )
            self._proxy.connect_to_signal(
                "ProfileSwitched", self._handle_profile_switched,
                dbus_interface=DBUS_INTERFACE,
            )
        except dbus.DBusException as e:
            logger.warning("daemon not reachable: %s", e)
            self._iface = None

    def connected(self) -> bool:
        return self._iface is not None

    def reconnect(self) -> bool:
        if not HAVE_DBUS or self._bus is None:
            return False
        self._connect_proxy()
        return self.connected()

    # ---------- listeners ----------
    def connect_status_changed(self, cb: Callable[[dict], None]) -> None:
        self._on_status.append(cb)

    def connect_profile_switched(self, cb: Callable[[str, str], None]) -> None:
        self._on_profile_switched.append(cb)

    def _handle_status_changed(self, status) -> None:
        snap = {str(k): _from_dbus(v) for k, v in dict(status).items()}
        for cb in self._on_status:
            try:
                cb(snap)
            except Exception:
                logger.exception("status listener raised")

    def _handle_profile_switched(self, name, reason) -> None:
        for cb in self._on_profile_switched:
            try:
                cb(str(name), str(reason))
            except Exception:
                logger.exception("profile-switched listener raised")

    # ---------- methods ----------
    def get_status(self) -> dict | None:
        if self._iface is None:
            return None
        try:
            status = self._iface.GetStatus()
        except Exception:
            logger.exception("GetStatus failed")
            return None
        return {str(k): _from_dbus(v) for k, v in dict(status).items()}

    def set_profile(self, name: str, transition: str = "") -> bool:
        if self._iface is None:
            return False
        try:
            return bool(self._iface.SetProfile(name, transition))
        except Exception:
            logger.exception("SetProfile failed")
            return False

    def preview_profile(self, profile: Profile) -> None:
        """Fire-and-forget preview: must not block the GUI mainloop.

        Slider drags fire this on every debounce tick; a synchronous
        DBus call would freeze the GUI for the duration of any in-flight
        renderer work on the daemon side.
        """
        if self._iface is None:
            return
        try:
            self._iface.PreviewProfile(
                json.dumps(profile.to_dict()),
                reply_handler=_silent_ok,
                error_handler=_silent_err,
            )
        except Exception:
            logger.exception("PreviewProfile failed")

    def stop_preview(self) -> None:
        if self._iface is None:
            return
        try:
            self._iface.StopPreview()
        except Exception:
            logger.exception("StopPreview failed")

    def reload_profiles(self) -> list[dict] | None:
        if self._iface is None:
            return None
        try:
            errors = self._iface.ReloadProfiles()
        except Exception:
            logger.exception("ReloadProfiles failed")
            return None
        return [{str(k): str(v) for k, v in dict(e).items()} for e in errors]

    def reload_rules(self) -> None:
        if self._iface is None:
            return
        try:
            self._iface.ReloadRules()
        except Exception:
            logger.exception("ReloadRules failed")


def _silent_ok(*_a, **_kw) -> None:
    pass


def _silent_err(err) -> None:
    logger.debug("async DBus call returned error: %s", err)


def _from_dbus(v):
    if isinstance(v, dict):
        return {str(k): _from_dbus(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_from_dbus(x) for x in v]
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    if hasattr(v, "real") and not isinstance(v, bool):
        return int(v) if isinstance(v, int) else float(v)
    if isinstance(v, bool):
        return bool(v)
    if isinstance(v, str):
        return str(v)
    return v
