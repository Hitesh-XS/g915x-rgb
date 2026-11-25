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
REPLY_TIMEOUT = 0.25
HID_KEYCODE_OFFSET = 3  # Logitech address = HID keycode - 3

# Feature IDs discovered via enumeration
FEATURE_PER_KEY_BACKLIGHT = 0x8081
FEATURE_RGB_EFFECTS = 0x8071
FEATURE_BRIGHTNESS = 0x8040
FEATURE_UNIFIED_BATTERY = 0x1004
FEATURE_BATTERY_LEGACY = 0x1000

# PerKeyBacklightV2 function IDs (shifted left 4 bits for byte 3)
FUNC_SET_KEY_COLOR = 0x10     # Function 1: set up to 4 individual keys
FUNC_SET_CONSECUTIVE = 0x20   # Function 2: set 5 neighboring addresses
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


def plan_frame(colors: dict[int, tuple[int, int, int]]) -> list[tuple[int, list[int]]]:
    """Split a frame into as few color messages as possible.

    Every message takes about 3 ms, so their number sets the frame rate.
    """
    messages: list[tuple[int, list[int]]] = []
    rest = dict(colors)

    by_color: dict[tuple[int, int, int], list[int]] = {}
    for address in sorted(colors):
        by_color.setdefault(colors[address], []).append(address)
    for color, addresses in by_color.items():
        # Up to 13 keys of one color; worth it from 5 keys on.
        while len(addresses) >= 5:
            chunk, addresses = addresses[:13], addresses[13:]
            # Unused slots repeat a key: zero is itself an address.
            chunk_full = chunk + [chunk[-1]] * (13 - len(chunk))
            messages.append((FUNC_SET_BATCH, [*color, *chunk_full]))
            for a in chunk:
                del rest[a]

    for address in sorted(rest):
        run = range(address, address + 5)
        if all(a in rest for a in run):
            messages.append((FUNC_SET_CONSECUTIVE,
                             [address, *(v for a in run for v in rest[a])]))
            for a in run:
                del rest[a]

    left = list(rest.items())
    for i in range(0, len(left), 4):
        chunk4 = left[i:i + 4]
        chunk4 += [chunk4[-1]] * (4 - len(chunk4))
        messages.append((FUNC_SET_KEY_COLOR, [v for a, c in chunk4 for v in (a, *c)]))
    return messages


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
        self._battery_checked = False
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
                self._battery_checked = False
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
        """Send a color message and wait until the keyboard has handled it.

        Unpaced writes overrun the keyboard: it then stalls the USB transfer
        and lighting freezes. Waiting adds no time, since the reply arrives
        before the next write could start.
        """
        for _ in range(2):
            if self._exchange(data, REPLY_TIMEOUT) is not None:
                return
        raise OSError(errno.ETIMEDOUT, "the keyboard did not reply")

    def _send_recv(self, data: list[int], timeout: float = 0.5) -> bytes | None:
        """Send a request; its reply, or None on no reply or a refusal."""
        resp = self._exchange(data, timeout)
        return None if resp is None or resp[2] in (0x8F, 0xFF) else resp

    def _exchange(self, data: list[int], timeout: float) -> bytes | None:
        """Send a message and return its own reply, which may be an error.

        Replies are matched on device, feature index and function, so
        notifications and late replies are skipped.
        """
        with self._lock:
            if self._fd is None:
                raise OSError(errno.ENODEV, "keyboard is not connected")
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
                    return resp
        return None

    def _get_feature_index(self, feature_id: int) -> int | None:
        """Ask IRoot for a feature's index; None when it is unsupported."""
        resp = self._send_recv([0x10, DEVICE_INDEX, 0x00, 0x00 | SOFTWARE_ID,
                                feature_id >> 8, feature_id & 0xFF, 0x00])
        return (resp[4] or None) if resp else None

    def apply_key_colors(self, key_colors: dict[int, tuple[int, int, int]]) -> None:
        """Show ``{address: (r, g, b)}`` as one frame."""
        self._ensure_connected()
        if not key_colors:
            return
        for function, payload in plan_frame(key_colors):
            self._send_color(function, payload)
        self._send_color(FUNC_COMMIT, [])

    def _send_color(self, function: int, payload: list[int]) -> None:
        msg = [0x11, DEVICE_INDEX, self._pkb_index, function | SOFTWARE_ID, *payload]
        self._send(msg + [0] * (20 - len(msg)))

    def set_key_color(self, address: int, r: int, g: int, b: int) -> None:
        """Set one key (does not commit)."""
        self._ensure_connected()
        self._send_color(FUNC_SET_KEY_COLOR, [address, r, g, b] * 4)

    def commit(self) -> None:
        """Show everything set since the last commit."""
        self._ensure_connected()
        self._send_color(FUNC_COMMIT, [])

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

        if self._battery_index is None and not self._battery_checked:
            self._battery_checked = True
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
