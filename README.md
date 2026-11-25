# G915 X RGB Controller

A Linux application for controlling per-key RGB lighting on the **Logitech G915 X** wireless mechanical keyboard.

No existing Linux tool (OpenRGB, g810-led, libratbag) supports the G915 X. This project communicates directly with the keyboard via HID++ 4.2 protocol over hidraw, providing full per-key color control.

## Features

- Per-key and group colors, with blends across a selection, undo and redo
- 18 background effects, also usable once at login
- Optional typing effects: Glow, Ripple, Starburst and Heat
- Profiles that switch with the active application (KDE Plasma)
- Turns off when the screen locks; can dim when idle
- A Kirigami interface that follows your Plasma theme

## Installing (Arch Linux)

```bash
git clone https://github.com/joshman1019/g915x-rgb.git
cd g915x-rgb/packaging
makepkg -si
```

Reconnect the keyboard, then:

```bash
systemctl --user enable --now g915x-rgb.service
```

Open **G915 X Lighting** from the application menu, or apply a saved profile
with `g915x-rgb-apply "Profile name"`.

## Permissions

The package lets the logged-in user reach only the keyboard's lighting
interface, which does not carry key presses. No group membership is needed.

Typing effects do need key presses. They are off by default. To allow them,
install `python-evdev` and copy the optional rule, knowing that it lets every
program you run read this keyboard:

```bash
sudo cp /usr/share/doc/g915x-rgb/70-g915x-typing.rules /etc/udev/rules.d/
```

Then reconnect the keyboard.

## Logs

```bash
journalctl --user -u g915x-rgb -f
```

## How It Works

The G915 X communicates via Logitech's HID++ 4.2 protocol over a USB HID interface. This project:

1. Discovers the keyboard's hidraw device (interface 2, vendor-specific HID++)
2. Queries the **PerKeyBacklightV2** feature (HID++ feature `0x8081`)
3. Sets individual key colors using the feature's set/commit protocol
4. Handles the G915 X's quirks:
   - Standard keys use HID keycode - 3 addressing
   - Modifier keys, G-keys, media keys, and logo use Logitech-specific addresses
   - Each message waits for its reply, and a frame is committed once; unpaced writes stall the keyboard

### Key Address Map

| Key Group                      | Address Range     | Notes             |
| ------------------------------ | ----------------- | ----------------- |
| Standard keys (A-Z, 0-9, etc.) | `HID keycode - 3` | Standard offset   |
| Modifiers (Shift, Ctrl, Alt)   | `0x63-0x6F`       | Logitech-specific |
| G-keys (G1-G9)                 | `0xB4-0xBC`       | Logitech-specific |
| Media keys                     | `0x9B-0x9E`       | Logitech-specific |
| Logo                           | `0xD2`            | Logitech-specific |

## Contributing

Contributions welcome! Key areas for improvement:

- **Additional keyboard support** - the HID++ protocol is similar across Logitech keyboards. Adapting for G915, G815, or other models should be straightforward.
- **Better key layout** - pixel-perfect positioning to match the physical keyboard

## License

MIT License - see [LICENSE](LICENSE) for details.

## Acknowledgments

- Protocol research informed by [keyleds](https://github.com/keyleds/keyleds), [g810-led](https://github.com/MatMoul/g810-led), and the Logitech HID++ community
- Built with Kirigami and PySide6
