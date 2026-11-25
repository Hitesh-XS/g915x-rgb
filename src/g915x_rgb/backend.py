"""Direct HID++ communication with the Logitech G915 X keyboard.

Protocol: HID++ 4.2 via /dev/hidraw, using PerKeyBacklightV2 (feature 0x8081).
The keyboard only supports 7-byte (report 0x10) and 20-byte (report 0x11) messages.
Key addresses follow HID Usage Table codes minus 3 offset.
"""

import errno
import glob
import os
import select
import threading
import time


VENDOR_ID = 0x046D
PRODUCT_ID = 0xC359
DEVICE_INDEX = 0x01  # Keyboard behind Lightspeed receiver
SOFTWARE_ID = 0x0A    # Tags our requests so replies can be matched
WRITE_TIMEOUT = 0.1
HID_KEYCODE_OFFSET = 3  # Logitech address = HID keycode - 3

# Feature IDs discovered via enumeration
FEATURE_PER_KEY_BACKLIGHT = 0x8081
FEATURE_RGB_EFFECTS = 0x8071
FEATURE_BRIGHTNESS = 0x8040
FEATURE_UNIFIED_BATTERY = 0x1004
FEATURE_BATTERY_LEGACY = 0x1000

# PerKeyBacklightV2 function IDs (shifted left 4 bits for byte 3)
FUNC_SET_KEY_COLOR = 0x10     # Function 1: set up to 4 individual keys
FUNC_SET_RANGE = 0x50         # Function 5: set key range to color
FUNC_SET_BATCH = 0x60         # Function 6: set up to 13 keys to same color
FUNC_COMMIT = 0x70            # Function 7: commit/apply changes


def device_present() -> bool:
    """Whether the keyboard is plugged in (reads sysfs, opens nothing)."""
    for path in glob.glob("/sys/class/hidraw/hidraw*/device/uevent"):
        try:
            with open(path) as f:
                if f"HID_ID=0003:{VENDOR_ID:08X}:{PRODUCT_ID:08X}" in f.read().upper():
                    return True
        except OSError:
            continue
    return False


class KeyboardNotFoundError(Exception):
    pass


class G915XBackend:
    """Low-level HID++ interface to the G915 X keyboard."""

    def __init__(self):
        self._fd = None
        self._lock = threading.RLock()
        self._hidraw_path = None
        self._pkb_index = None  # PerKeyBacklight feature index
        self._battery_index: int | None = None
        self._battery_feature: int | None = None  # which feature ID we resolved

    @property
    def is_connected(self) -> bool:
        return self._fd is not None

    def connect(self) -> None:
        """Find and open the correct hidraw device (interface 2, vendor HID++)."""
        if self._fd is not None:
            return

        path = self._find_hidraw_device()
        if path is None:
            raise KeyboardNotFoundError(
                "Logitech G915 X not found. Check USB connection and udev rules."
            )

        self._hidraw_path = path
        self._fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)

        # Discover the feature index for PerKeyBacklightV2
        try:
            self._pkb_index = self._get_feature_index(FEATURE_PER_KEY_BACKLIGHT)
        except OSError:
            self.disconnect()
            raise
        if self._pkb_index is None:
            self.disconnect()
            raise KeyboardNotFoundError(
                "PerKeyBacklightV2 feature not found on device."
            )

    def disconnect(self) -> None:
        with self._lock:
            if self._fd is None:
                return
            try:
                os.close(self._fd)
            finally:
                self._fd = None
                self._pkb_index = None
                self._battery_index = None
                self._battery_feature = None

    def _find_hidraw_device(self) -> str | None:
        """Find the hidraw device for interface 2 of the G915 X."""
        for hidraw in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
            name = hidraw.split("/")[-1]
            try:
                uevent_path = os.path.join(hidraw, "device", "uevent")
                with open(uevent_path) as f:
                    uevent = f.read()

                hid_id = ""
                for line in uevent.splitlines():
                    if line.startswith("HID_ID="):
                        hid_id = line.split("=", 1)[1]

                # Match vendor:product
                parts = hid_id.split(":")
                if len(parts) >= 3:
                    vid = int(parts[1], 16)
                    pid = int(parts[2], 16)
                    if vid != VENDOR_ID or pid != PRODUCT_ID:
                        continue

                # Check this is interface 2 (vendor HID++) via resolved sysfs path
                device_real = os.path.realpath(os.path.join(hidraw, "device"))
                usb_iface_uevent = os.path.join(device_real, "..", "uevent")
                usb_iface_uevent = os.path.normpath(usb_iface_uevent)
                with open(usb_iface_uevent) as f:
                    parent_uevent = f.read()

                if "INTERFACE=3/0/0" in parent_uevent:
                    return f"/dev/{name}"

            except (OSError, ValueError, IndexError):
                continue

        return None

    def _write(self, data: list[int]) -> None:
        """Write one report, waiting briefly if the kernel queue is full.

        Raises OSError when the write cannot be completed, so callers never
        record an update as sent when it was not.
        """
        buf = bytes(data)
        deadline = time.monotonic() + WRITE_TIMEOUT
        while True:
            if self._fd is None:
                raise OSError(errno.ENODEV, "keyboard is not connected")
            try:
                os.write(self._fd, buf)
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.002)

    def _drain(self) -> None:
        """Discard everything the keyboard has already sent."""
        while True:
            r, _, _ = select.select([self._fd], [], [], 0)
            if not r:
                return
            try:
                if not os.read(self._fd, 64):
                    return
            except OSError:
                return

    def _send(self, data: list[int]) -> None:
        """Send a message whose reply is not needed."""
        with self._lock:
            self._write(data)
            self._drain()

    def _send_recv(self, data: list[int], timeout: float = 0.5) -> bytes | None:
        """Send a request and return the reply that belongs to it.

        Replies are matched on device, feature index and function, so
        notifications and acknowledgements of color writes are skipped.
        """
        with self._lock:
            if self._fd is None:
                return None
            self._drain()
            self._write(data)
            deadline = time.monotonic() + timeout
            while (left := deadline - time.monotonic()) > 0:
                r, _, _ = select.select([self._fd], [], [], left)
                if not r:
                    return None
                try:
                    resp = os.read(self._fd, 64)
                except BlockingIOError:
                    continue
                if len(resp) < 7 or resp[1] != data[1]:
                    continue
                if resp[2] == data[2] and resp[3] == data[3]:
                    return resp
                if resp[2] in (0x8F, 0xFF) and resp[3] == data[2] and resp[4] == data[3]:
                    return None
        return None

    def _get_feature_index(self, feature_id: int) -> int | None:
        """Ask IRoot for a feature's index; None when it is unsupported."""
        resp = self._send_recv([0x10, DEVICE_INDEX, 0x00, 0x00 | SOFTWARE_ID,
                                feature_id >> 8, feature_id & 0xFF, 0x00])
        return (resp[4] or None) if resp else None

    def set_key_color(self, address: int, r: int, g: int, b: int) -> None:
        """Set a single key color (does not commit)."""
        self._ensure_connected()
        msg = [0x11, DEVICE_INDEX, self._pkb_index, FUNC_SET_KEY_COLOR,
               address, r, g, b] + [0] * 12
        self._send(msg)

    def set_keys_color(self, keys: list[tuple[int, int, int, int]]) -> None:
        """Set multiple key colors. Each entry is (address, r, g, b).
        Sends in batches of 4 per message (function 1 limit).
        Does not commit.
        """
        self._ensure_connected()
        for i in range(0, len(keys), 4):
            batch = keys[i:i + 4]
            msg = [0x11, DEVICE_INDEX, self._pkb_index, FUNC_SET_KEY_COLOR]
            for addr, r, g, b in batch:
                msg.extend([addr, r, g, b])
            msg.extend([0] * (20 - len(msg)))
            self._send(msg)

    def set_range_color(self, start: int, end: int, r: int, g: int, b: int) -> None:
        """Set a range of key addresses to the same color (does not commit)."""
        self._ensure_connected()
        msg = [0x11, DEVICE_INDEX, self._pkb_index, FUNC_SET_RANGE,
               start, end, r, g, b] + [0] * 11
        self._send(msg)

    def set_batch_color(self, addresses: list[int], r: int, g: int, b: int) -> None:
        """Set multiple keys to the same color. Up to 13 per message.
        Does not commit.
        """
        self._ensure_connected()
        for i in range(0, len(addresses), 13):
            batch = addresses[i:i + 13]
            msg = [0x11, DEVICE_INDEX, self._pkb_index, FUNC_SET_BATCH,
                   r, g, b] + batch
            msg.extend([0] * (20 - len(msg)))
            self._send(msg)

    def commit(self) -> None:
        """Commit pending color changes to the keyboard."""
        self._ensure_connected()
        msg = [0x11, DEVICE_INDEX, self._pkb_index, FUNC_COMMIT] + [0] * 16
        self._send(msg)

    # Addresses that don't respond to the range function and need
    # individual set commands. Discovered via protocol testing.
    SPECIAL_ADDRESSES = (
        # Modifier keys (0x63-0x6F range)
        list(range(0x63, 0x70))
        # G-keys (0xB4-0xBC)
        + list(range(0xB4, 0xBD))
        # Media keys (0x94-0x9F)
        + list(range(0x94, 0xA0))
        # Logo and other special LEDs (logo at 0xD2)
        + list(range(0xD0, 0xD8))
    )

    def set_all_keys(self, r: int, g: int, b: int) -> None:
        """Set all keys to a single color and commit."""
        self._ensure_connected()
        from .keyboard_layout import G915X_KEYS

        special = set(self.SPECIAL_ADDRESSES)

        # Standard keys via batch
        standard = [(k.address, r, g, b) for k in G915X_KEYS if k.address not in special]
        for i in range(0, len(standard), 16):
            batch = standard[i:i + 16]
            self.set_keys_color(batch)
            self.commit()

        # Special keys: individual commits
        for key in G915X_KEYS:
            if key.address in special:
                self.set_key_color(key.address, r, g, b)
                self.commit()

    def apply_key_colors(self, key_colors: dict[int, tuple[int, int, int]]) -> None:
        """Apply a dict of {address: (r, g, b)} efficiently.

        Standard keys are batched (4 per message, commit every 16).
        Special keys (modifiers, G-keys, media, logo) need individual
        commits to work reliably on the G915 X.
        """
        special = set(self.SPECIAL_ADDRESSES)

        standard_keys = [(a, *rgb) for a, rgb in key_colors.items() if a not in special]
        special_keys = [(a, *rgb) for a, rgb in key_colors.items() if a in special]

        # Standard keys: batch 4 per message, commit every 16
        for i in range(0, len(standard_keys), 16):
            batch = standard_keys[i:i + 16]
            self.set_keys_color(batch)
            self.commit()

        # Special keys: individual commits required
        for addr, r, g, b in special_keys:
            self.set_key_color(addr, r, g, b)
            self.commit()

    def _ensure_connected(self) -> None:
        if self._fd is None:
            raise OSError(errno.ENODEV, "keyboard is not connected")

    def get_battery(self) -> tuple[int, bool] | None:
        """Query battery status.

        Returns ``(percentage, charging)`` where ``percentage`` is 0–100
        and ``charging`` is True when the keyboard is charging or fully
        charged on cable. Returns ``None`` when neither battery feature is
        available or the device fails to respond.
        """
        if self._fd is None:
            return None

        if self._battery_index is None:
            for feature_id in (FEATURE_UNIFIED_BATTERY, FEATURE_BATTERY_LEGACY):
                idx = self._get_feature_index(feature_id)
                if idx is not None:
                    self._battery_index = idx
                    self._battery_feature = feature_id
                    break
        if self._battery_index is None:
            return None

        # Status is function 1 for 0x1004 and function 0 for 0x1000.
        resp = self._send_recv(
            [0x10, DEVICE_INDEX, self._battery_index,
             (0x10 if self._battery_feature == FEATURE_UNIFIED_BATTERY else 0x00) | SOFTWARE_ID, 0x00, 0x00, 0x00]
        )
        if resp is None or len(resp) < 7 or resp[2] == 0xFF:
            return None

        if self._battery_feature == FEATURE_UNIFIED_BATTERY:
            soc = resp[4]
            charging_status = resp[6] if len(resp) > 6 else 0
            # 1=charging, 2=charging slow, 3=charge complete (still on cable)
            charging = charging_status in (1, 2, 3)
        else:  # FEATURE_BATTERY_LEGACY
            soc = resp[4]
            status = resp[6] if len(resp) > 6 else 0
            charging = status in (1, 2, 3, 8, 9)
        return (max(0, min(100, soc)), charging)

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()


# Standard HID Usage Table to Logitech address mapping
def hid_to_logi(hid_keycode: int) -> int:
    """Convert HID Usage Table keycode to Logitech G915 X address."""
    return hid_keycode - HID_KEYCODE_OFFSET


def logi_to_hid(logi_addr: int) -> int:
    """Convert Logitech G915 X address to HID Usage Table keycode."""
    return logi_addr + HID_KEYCODE_OFFSET
