"""Long-running keyboard daemon.

The daemon is the sole owner of the hidraw fd. The GUI talks to it over
the session bus; the watcher behavior (apply on connect / handle
disconnect) is folded in. It also hosts:

* the layered :class:`Renderer` that drives the keyboard at 30 FPS,
* an :class:`AnimationRunner` for the looping ambient layer,
* a periodic battery poller,
* an evdev :class:`KeyEventReader` feeding the reactive overlay,
* an active-window source feeding auto-switch rule matching.

D-Bus interface
---------------
Service:    ``org.g915xrgb.Controller``
Path:       ``/org/g915xrgb/Controller``

Methods:

* ``SetProfile(name, transition)``
* ``PreviewProfile(json)``        — apply an unsaved profile from JSON
* ``PreviewAnimation(name, primary_hex, secondary_hex)``
* ``StopPreview()``
* ``ReloadProfiles()``
* ``ReloadRules()``
* ``GetStatus()``                  — dict with connected/battery/current_profile/...
* ``OnActiveWindowChanged(class, title)``  — called by the KWin script
* ``Quit()``

Signals:

* ``StatusChanged(dict)``
* ``ProfileSwitched(name, reason)``
"""

from __future__ import annotations

import glob
import json
import logging
import os
import sys
import threading
import time
from dataclasses import asdict, dataclass

try:
    import dbus
    import dbus.mainloop.glib
    import dbus.service
    HAVE_DBUS = True
except ImportError:
    dbus = None  # type: ignore[assignment]
    HAVE_DBUS = False

try:
    from gi.repository import GLib
    HAVE_GLIB = True
except ImportError:
    GLib = None  # type: ignore[assignment]
    HAVE_GLIB = False

from .animation import ANIMATIONS, AnimationConfig
from .auto_switch import (
    ACTIVE_WINDOW_HANDLER,
    AutoSwitchRules,
    KWinSource,
    detect_compositor,
    find_kwin_script,
)
from .backend import (
    G915XBackend,
    KeyboardNotFoundError,
    PRODUCT_ID,
    VENDOR_ID,
)
from .config import (
    get_last_profile,
    set_last_profile,
)
from .logging_setup import configure as configure_logging
from .profile import (
    AnimationProfile,
    Profile,
    create_default_profile,
    load_all_profiles_with_errors,
)
from .reactive import HAVE_EVDEV, KeyEventReader, ReactiveOverlay, find_keyboard_event_devices
from .renderer import Renderer
from .transitions import TRANSITIONS, TransitionConfig

DBUS_BUS_NAME = "org.g915xrgb.Controller"
DBUS_OBJECT_PATH = "/org/g915xrgb/Controller"
DBUS_INTERFACE = "org.g915xrgb.Controller"

POLL_INTERVAL = 2.0           # seconds between presence checks
SETTLE_DELAY = 2.0            # seconds after detecting device before connecting
BATTERY_POLL_SECONDS = 60
LOW_BATTERY_THRESHOLD = 15

logger = logging.getLogger("g915x.daemon")


# =====================================================================
# Helpers
# =====================================================================

def _device_present() -> bool:
    """Sysfs-only check for the G915 X (no fd open)."""
    for hidraw in glob.glob("/sys/class/hidraw/hidraw*"):
        try:
            with open(os.path.join(hidraw, "device", "uevent")) as f:
                uevent = f.read()
            for line in uevent.splitlines():
                if line.startswith("HID_ID="):
                    parts = line.split("=", 1)[1].split(":")
                    if len(parts) >= 3:
                        if int(parts[1], 16) == VENDOR_ID and int(parts[2], 16) == PRODUCT_ID:
                            return True
        except (FileNotFoundError, ValueError, IndexError):
            continue
    return False


def _profile_to_animation_cfg(p: AnimationProfile) -> AnimationConfig:
    return AnimationConfig(
        primary=p.primary,
        secondary=p.secondary,
        speed=p.speed,
        intensity=p.intensity,
    )


# =====================================================================
# Core controller (decoupled from DBus for testability)
# =====================================================================

@dataclass
class Status:
    connected: bool = False
    current_profile: str = ""
    battery_percent: int = -1
    battery_charging: bool = False
    ambient_running: bool = False
    reactive_active: bool = False
    auto_switch_enabled: bool = False
    auto_switch_compositor: str = ""

    def to_dbus(self) -> dict:
        return {k: v for k, v in asdict(self).items()}


class Controller:
    """The non-DBus core of the daemon. The DBus service is a thin facade."""

    def __init__(self) -> None:
        self._kb = G915XBackend()
        self._renderer = Renderer(self._kb, fps=15.0)
        self._lock = threading.Lock()

        self._profiles: list[Profile] = []
        self._current: Profile | None = None
        self._status = Status()

        self._reactive: ReactiveOverlay | None = None
        self._key_reader: KeyEventReader | None = None

        self._rules = AutoSwitchRules.load()
        self._win_source: KWinSource | None = None
        self._win_source_started = False
        self._last_window: tuple[str, str] = ("", "")
        self._auto_switched_to: str | None = None

        self._on_status_changed: list = []
        self._on_profile_switched: list = []

    # ---------- listeners (DBus layer plugs in here) ----------
    def on_status_changed(self, cb) -> None:
        self._on_status_changed.append(cb)

    def on_profile_switched(self, cb) -> None:
        self._on_profile_switched.append(cb)

    def _emit_status(self) -> None:
        self._status.auto_switch_enabled = self._rules.enabled
        self._status.auto_switch_compositor = self._win_source.name if self._win_source_started else ""
        self._status.ambient_running = (
            self._current is not None
            and bool(self._current.ambient.name)
        )
        self._status.reactive_active = self._reactive is not None and bool(self._reactive.effect)
        snap = self._status.to_dbus()
        for cb in self._on_status_changed:
            try:
                cb(snap)
            except Exception:
                logger.exception("status listener raised")

    def _emit_profile_switched(self, name: str, reason: str) -> None:
        for cb in self._on_profile_switched:
            try:
                cb(name, reason)
            except Exception:
                logger.exception("profile-switched listener raised")

    # ---------- profile management ----------
    def reload_profiles(self) -> list[tuple[str, str]]:
        """Re-read profiles from disk. Returns load errors as (path, msg)."""
        result = load_all_profiles_with_errors()
        with self._lock:
            self._profiles = result.profiles
        for path, msg in result.errors:
            logger.warning("could not load profile %s: %s", path, msg)
        return [(str(p), m) for p, m in result.errors]

    def reload_rules(self) -> None:
        self._rules = AutoSwitchRules.load()
        self._maybe_start_window_source()
        self._emit_status()

    def _find_profile(self, name: str) -> Profile | None:
        for p in self._profiles:
            if p.name == name:
                return p
        for p in self._profiles:
            if p.name.lower() == name.lower():
                return p
        return None

    # ---------- device lifecycle ----------
    def _try_connect(self) -> bool:
        if self._kb.is_connected:
            self._status.connected = True
            return True
        try:
            self._kb.connect()
            self._status.connected = True
            self._renderer.start()
            self._poll_battery_now()
            logger.info("keyboard connected via %s", self._kb._hidraw_path)
            return True
        except KeyboardNotFoundError:
            self._status.connected = False
            return False
        except (PermissionError, OSError) as e:
            logger.warning("connect failed (will retry): %s", e)
            self._status.connected = False
            return False

    def _disconnect(self) -> None:
        self._renderer.stop_ambient()
        self._renderer.stop()
        try:
            self._kb.disconnect()
        finally:
            self._status.connected = False
            self._status.battery_percent = -1
            self._status.battery_charging = False
            self._emit_status()

    # ---------- public actions ----------
    def set_profile(self, name: str, transition: str = "",
                    *, reason: str = "manual") -> bool:
        with self._lock:
            profile = self._find_profile(name)
        if profile is None:
            logger.warning("set_profile: profile %r not found", name)
            return False
        return self._apply_profile(profile, transition, reason=reason)

    def preview_profile_json(self, payload: str) -> bool:
        """Apply an unsaved profile sent as JSON (used for live edit).

        Hot path: must NOT block, restart animations, or play
        transitions. We snap the base layer immediately and live-update
        the running ambient runner's cfg in place. Only when the
        ambient *animation name* itself changed do we tear down + start
        a new runner.
        """
        try:
            data = json.loads(payload)
            profile = Profile.from_dict(data)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning("preview_profile_json: bad payload: %s", e)
            return False
        return self._apply_preview(profile)

    def _apply_preview(self, profile: Profile) -> bool:
        if not self._try_connect():
            return False

        old = self._current

        # 1. Base layer: cheap dict swap.
        self._renderer.set_base(profile.get_all_key_colors())

        # 2. Ambient: only restart when the animation name changed.
        old_amb_name = old.ambient.name if old is not None else ""
        new_amb_name = profile.ambient.name
        if new_amb_name != old_amb_name:
            self._renderer.stop_ambient()
            if new_amb_name:
                self._renderer.start_ambient(
                    new_amb_name,
                    _profile_to_animation_cfg(profile.ambient),
                )
        elif new_amb_name:
            # Same animation; live-update colors / speed / intensity.
            self._renderer.update_ambient_config(
                primary=profile.ambient.primary,
                secondary=profile.ambient.secondary,
                speed=profile.ambient.speed,
                intensity=profile.ambient.intensity,
            )

        # 3. Reactive: same diff strategy.
        self._configure_reactive(profile)

        # 4. Update bookkeeping but DO NOT play a transition.
        self._current = profile
        self._emit_status()
        return True

    def preview_animation(self, name: str, primary: tuple[int, int, int],
                          secondary: tuple[int, int, int]) -> None:
        if not self._kb.is_connected:
            return
        cfg = AnimationConfig(primary=primary, secondary=secondary)
        self._renderer.start_ambient(name, cfg)

    def stop_preview(self) -> None:
        # Resumes whatever the active profile dictates.
        if self._current:
            self._apply_profile(self._current, "", reason="stop_preview")

    # ---------- battery ----------
    def _poll_battery_now(self) -> None:
        if not self._kb.is_connected:
            return
        try:
            result = self._kb.get_battery()
        except Exception:
            logger.exception("battery query failed")
            return
        if result is None:
            return
        pct, charging = result
        was_pct = self._status.battery_percent
        self._status.battery_percent = pct
        self._status.battery_charging = charging
        self._emit_status()
        if was_pct >= LOW_BATTERY_THRESHOLD > pct and not charging:
            logger.warning("keyboard battery low: %d%%", pct)

    # ---------- auto-switch ----------
    def _maybe_start_window_source(self) -> None:
        if not self._rules.enabled:
            self._stop_window_source()
            return
        if self._win_source_started:
            return
        if detect_compositor() != "kwin":
            logger.info("auto-switch: KWin not detected (compositor=%s); skipping",
                        detect_compositor())
            return
        script = find_kwin_script()
        if script is None:
            logger.info("auto-switch: KWin script asset not found; skipping")
            return
        self._win_source = KWinSource(script)
        ok = self._win_source.start(self._handle_window_change)
        self._win_source_started = ok
        if ok:
            logger.info("auto-switch: KWin source started")

    def _stop_window_source(self) -> None:
        if self._win_source is not None:
            self._win_source.stop()
        self._win_source = None
        self._win_source_started = False

    def ingest_active_window(self, win_class: str, title: str) -> None:
        """DBus entry point — KWin script calls us here."""
        if self._win_source is not None:
            self._win_source.ingest(win_class, title)
        else:
            self._handle_window_change(win_class, title)

    def _handle_window_change(self, win_class: str, title: str) -> None:
        if (win_class, title) == self._last_window:
            return
        self._last_window = (win_class, title)
        if not self._rules.enabled or not self._rules.rules:
            return
        rule = self._rules.match(win_class, title)
        if rule is None:
            return
        if self._current and self._current.name == rule.profile:
            return
        target = self._find_profile(rule.profile)
        if target is None:
            logger.debug("auto-switch: rule pointed at missing profile %r", rule.profile)
            return
        transition = rule.transition or self._rules.transition
        self._auto_switched_to = rule.profile
        self._apply_profile(target, transition,
                            reason=f"window:{win_class}")

    # ---------- core apply path ----------
    def _apply_profile(self, profile: Profile, transition: str,
                       *, reason: str, play_startup: bool = True) -> bool:
        if not self._try_connect():
            return False

        target_frame = profile.get_all_key_colors()
        old = self._current

        self._current = profile
        set_last_profile(profile.name)

        # Reactive layer for new profile
        self._configure_reactive(profile)

        new_amb = profile.ambient if profile.ambient.name else None

        def start_ambient_if_any() -> None:
            if new_amb is not None and new_amb.name:
                self._renderer.start_ambient(
                    new_amb.name, _profile_to_animation_cfg(new_amb)
                )

        # Smart ambient diff: same name => live-update; different name => restart.
        old_amb_name = old.ambient.name if old is not None else ""
        if new_amb is None:
            if old_amb_name:
                self._renderer.stop_ambient()
        elif new_amb.name != old_amb_name:
            self._renderer.stop_ambient()
            # We start the new ambient AFTER any startup-animation /
            # transition completes, via the callback below.
        else:
            # Same animation: just update cfg in place; no restart needed.
            self._renderer.update_ambient_config(
                primary=new_amb.primary,
                secondary=new_amb.secondary,
                speed=new_amb.speed,
                intensity=new_amb.intensity,
            )

        # Push the static base — what shows when no ambient is active.
        self._renderer.set_base(target_frame)

        # Decide what plays during the apply: startup animation, transition,
        # or nothing.
        startup_name = profile.startup.name if play_startup else ""
        if startup_name and startup_name in ANIMATIONS:
            self._renderer.play_one_shot(
                startup_name,
                _profile_to_animation_cfg(profile.startup),
                duration=3.0,
                on_done=start_ambient_if_any,
            )
        elif transition and transition in TRANSITIONS:
            self._renderer.start_transition(
                transition, target_frame,
                duration=0.6,
                cfg=TransitionConfig(duration=0.6),
                on_complete=start_ambient_if_any,
            )
        else:
            # Same-name ambient case: nothing to start; cfg already updated.
            if new_amb is not None and new_amb.name != old_amb_name:
                start_ambient_if_any()

        self._emit_profile_switched(profile.name, reason)
        self._emit_status()
        return True

    # ---------- reactive ----------
    def _configure_reactive(self, profile: Profile) -> None:
        # Stop existing if no longer wanted or settings differ.
        if not profile.reactive.effect:
            self._teardown_reactive()
            return
        if not HAVE_EVDEV:
            if self._reactive is None:
                logger.info("reactive: python-evdev unavailable; effect skipped")
            return

        if self._reactive is None:
            self._reactive = ReactiveOverlay(
                effect=profile.reactive.effect,
                color=profile.reactive.color,
                intensity=profile.reactive.intensity,
            )
            self._renderer.set_reactive_source(self._reactive)
        else:
            self._reactive.effect = profile.reactive.effect
            self._reactive.color = profile.reactive.color
            self._reactive.intensity = profile.reactive.intensity

        if self._key_reader is None:
            paths = find_keyboard_event_devices()
            if not paths:
                logger.warning("reactive: no Logitech evdev keyboard found")
                return
            try:
                reader = KeyEventReader(self._on_key_press)
                reader.start(paths)
                self._key_reader = reader
                logger.info("reactive: reading from %s", paths)
            except Exception:
                logger.exception("reactive: could not start evdev reader")

    def _teardown_reactive(self) -> None:
        if self._reactive is not None:
            self._renderer.set_reactive_source(None)
            self._reactive = None
        if self._key_reader is not None:
            self._key_reader.stop()
            self._key_reader = None

    def _on_key_press(self, address: int) -> None:
        if self._reactive is not None:
            self._reactive.trigger(address)

    # ---------- main loop integration ----------
    def status_dict(self) -> dict:
        return self._status.to_dbus()

    def boot(self) -> None:
        self.reload_profiles()
        self._maybe_start_window_source()
        # Apply last-used profile if we already see the device.
        if _device_present():
            time.sleep(SETTLE_DELAY if not self._kb.is_connected else 0)
            last = get_last_profile()
            target = self._find_profile(last) if last else None
            if target is None:
                if not self._profiles:
                    self._profiles = [create_default_profile()]
                    self._profiles[0].save()
                target = self._profiles[0]
            # ``transition`` and the startup animation name are different
            # things; play_startup=True will run target.startup.name as a
            # one-shot before ambient takes over.
            self._apply_profile(target, "", reason="boot", play_startup=True)

    def shutdown(self) -> None:
        self._teardown_reactive()
        self._stop_window_source()
        self._disconnect()


# =====================================================================
# DBus glue
# =====================================================================

if HAVE_DBUS:
    _DBusServiceObject = dbus.service.Object
    _dbus_method = dbus.service.method
    _dbus_signal = dbus.service.signal
else:
    class _DBusServiceObject:  # type: ignore[no-redef]
        def __init__(self, *a, **kw): ...

    def _dbus_method(*_args, **_kwargs):  # type: ignore[no-redef]
        def deco(fn): return fn
        return deco

    def _dbus_signal(*_args, **_kwargs):  # type: ignore[no-redef]
        def deco(fn): return fn
        return deco


class ControllerService(_DBusServiceObject):
    """Thin DBus facade over :class:`Controller`."""

    def __init__(self, bus_name, controller: Controller):
        super().__init__(bus_name, DBUS_OBJECT_PATH)
        self._c = controller
        controller.on_status_changed(self._emit_status_changed)
        controller.on_profile_switched(self._emit_profile_switched)

    @_dbus_method(DBUS_INTERFACE, in_signature="ss", out_signature="b")
    def SetProfile(self, name: str, transition: str) -> bool:
        return self._c.set_profile(str(name), str(transition))

    @_dbus_method(DBUS_INTERFACE, in_signature="s", out_signature="b")
    def PreviewProfile(self, payload: str) -> bool:
        return self._c.preview_profile_json(str(payload))

    @_dbus_method(DBUS_INTERFACE, in_signature="sss", out_signature="")
    def PreviewAnimation(self, name: str, primary_hex: str, secondary_hex: str) -> None:
        from .profile import _parse_hex
        try:
            p = _parse_hex(str(primary_hex))
            s = _parse_hex(str(secondary_hex))
        except Exception:
            return
        self._c.preview_animation(str(name), p, s)

    @_dbus_method(DBUS_INTERFACE, in_signature="", out_signature="")
    def StopPreview(self) -> None:
        self._c.stop_preview()

    @_dbus_method(DBUS_INTERFACE, in_signature="", out_signature="aa{ss}")
    def ReloadProfiles(self):
        errors = self._c.reload_profiles()
        return [{"path": p, "error": m} for p, m in errors]

    @_dbus_method(DBUS_INTERFACE, in_signature="", out_signature="")
    def ReloadRules(self) -> None:
        self._c.reload_rules()

    @_dbus_method(DBUS_INTERFACE, in_signature="", out_signature="a{sv}")
    def GetStatus(self):
        return _to_dbus_dict(self._c.status_dict())

    @_dbus_method(DBUS_INTERFACE, in_signature="ss", out_signature="")
    def OnActiveWindowChanged(self, win_class: str, title: str) -> None:
        self._c.ingest_active_window(str(win_class), str(title))

    @_dbus_method(DBUS_INTERFACE, in_signature="", out_signature="")
    def Quit(self) -> None:
        GLib.idle_add(self._quit_main_loop)

    def _quit_main_loop(self) -> bool:
        loop = getattr(self, "_main_loop", None)
        if loop is not None:
            loop.quit()
        return False

    @_dbus_signal(DBUS_INTERFACE, signature="a{sv}")
    def StatusChanged(self, status):
        pass

    @_dbus_signal(DBUS_INTERFACE, signature="ss")
    def ProfileSwitched(self, name, reason):
        pass

    def _emit_status_changed(self, status: dict) -> None:
        try:
            self.StatusChanged(_to_dbus_dict(status))
        except Exception:
            logger.exception("StatusChanged emit failed")

    def _emit_profile_switched(self, name: str, reason: str) -> None:
        try:
            self.ProfileSwitched(name, reason)
        except Exception:
            logger.exception("ProfileSwitched emit failed")


def _to_dbus_dict(d: dict) -> dict:
    """Coerce native types into ``a{sv}``-compatible dbus types."""
    out: dict = {}
    for k, v in d.items():
        if isinstance(v, bool):
            out[k] = dbus.Boolean(v)
        elif isinstance(v, int):
            out[k] = dbus.Int32(v)
        elif isinstance(v, float):
            out[k] = dbus.Double(v)
        else:
            out[k] = dbus.String(str(v))
    return out


# =====================================================================
# Top-level
# =====================================================================

def main() -> int:
    configure_logging("daemon")
    logger.info("g915x-rgb daemon starting (pid=%d)", os.getpid())

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    try:
        bus_name = dbus.service.BusName(DBUS_BUS_NAME, bus, do_not_queue=True)
    except dbus.exceptions.NameExistsException:
        logger.error("another g915x-rgb daemon already owns %s; exiting", DBUS_BUS_NAME)
        return 1

    controller = Controller()
    service = ControllerService(bus_name, controller)
    main_loop = GLib.MainLoop()
    service._main_loop = main_loop

    # Boot synchronously: load profiles, attempt connect, apply last profile.
    controller.boot()

    # Periodic device-presence + battery checks.
    def _tick_presence() -> bool:
        present = _device_present()
        if present and not controller._kb.is_connected:
            controller._try_connect()
            last = get_last_profile()
            target = controller._find_profile(last) if last else None
            if target is None and controller._profiles:
                target = controller._profiles[0]
            if target is not None:
                controller._apply_profile(target, "", reason="reconnect",
                                          play_startup=True)
        elif not present and controller._kb.is_connected:
            logger.info("keyboard disconnected")
            controller._disconnect()
        return True  # keep ticking

    def _tick_battery() -> bool:
        controller._poll_battery_now()
        return True

    GLib.timeout_add_seconds(int(POLL_INTERVAL), _tick_presence)
    GLib.timeout_add_seconds(BATTERY_POLL_SECONDS, _tick_battery)

    try:
        main_loop.run()
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("shutting down")
        controller.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
