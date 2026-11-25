"""Light unidentified addresses one at a time to see which key responds.

Run from the repository: PYTHONPATH=src python tools/probe_lights.py
Only addresses the keyboard reports, and the app does not use, are lit.
"""

from g915x_rgb.backend import DEVICE_INDEX, SOFTWARE_ID, G915XBackend
from g915x_rgb.keyboard_layout import KEY_BY_ADDRESS


def reported(kb: G915XBackend) -> set[int]:
    found = set()
    for group in range(3):
        reply = kb._send_recv([0x10, DEVICE_INDEX, kb._pkb_index, SOFTWARE_ID, 0, group, 0])
        for i, byte in enumerate(reply[6:20] if reply else b""):
            found.update(group * 112 + i * 8 + bit for bit in range(8) if byte >> bit & 1)
    return found


def main() -> None:
    with G915XBackend() as kb:
        hits = {}
        for address in sorted(reported(kb) - set(KEY_BY_ADDRESS)):
            kb.set_key_color(address, 0, 255, 0)
            kb.commit()
            note = input(f"0x{address:02X} is now green. Which key changed? (Enter for none) ")
            if note.strip():
                hits[address] = note.strip()
            kb.set_key_color(address, 0, 0, 0)
            kb.commit()
    for address, note in hits.items():
        print(f"0x{address:02X}: {note}")
    if not hits:
        print("No unidentified address lights a key.")


if __name__ == "__main__":
    try:
        main()
    except (EOFError, KeyboardInterrupt):
        print()
