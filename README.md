# Android TV Connect

**Version 1.0.0** — Stream an Android TV stick through a MacroSilicon MS2109 HDMI capture dongle and control it with ADB from a GTK4 desktop app.

**Repository:** https://github.com/thothassistantai-web/android-tv-connect

Check the installed version:

```bash
android-tv-connect --version
```

## Requirements

Install system dependencies on Pop!_OS / Ubuntu:

```bash
sudo apt update
sudo apt install \
  python3 python3-gi python3-gi-cairo \
  gir1.2-gtk-4.0 gir1.2-adw-1 \
  gstreamer1.0-plugins-good gstreamer1.0-pipewire \
  adb v4l-utils
```

Hardware:

- MacroSilicon MS2109 USB capture dongle (`534d:2109`)
- Android TV device with USB debugging enabled (wired ADB preferred; wireless fallback supported)

## Install

From the project directory:

```bash
chmod +x install.sh
./install.sh
```

This installs to `~/.local`:

| Path | Purpose |
|------|---------|
| `~/.local/share/android-tv-connect/` | Application package |
| `~/.local/bin/android-tv-connect` | Launcher script |
| `~/.local/share/applications/android-tv-connect.desktop` | App menu entry |
| `~/.config/systemd/user/android-tv-connect-watch.service` | Auto-launch watcher |

The install script also attempts to install udev rules so the capture device is accessible without root. If sudo is unavailable, it prints manual commands.

### udev rules (manual)

```bash
sudo install -Dm644 packaging/udev/99-android-tv-capture.rules \
  /etc/udev/rules.d/99-android-tv-capture.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Unplug and replug the capture dongle after installing rules.

## Usage

### Manual launch

```bash
android-tv-connect
```

Equivalent:

```bash
PYTHONPATH=~/.local/share/android-tv-connect python3 -m android_tv_connect
```

### Watch mode (auto-launch)

The install script enables a systemd user service that polls for the capture dongle and an ADB device, then opens the main window when both are present.

```bash
systemctl --user status android-tv-connect-watch
systemctl --user restart android-tv-connect-watch
journalctl --user -u android-tv-connect-watch -f
```

Watch behavior:

1. Poll every 2 seconds for capture USB (`534d:2109`) and `/dev/video0`
2. Poll for an ADB device (wired serial preferred, wireless fallback)
3. Launch the main window when both are present and the app is not already running
4. Debounce disconnects for 3 seconds before closing

### App menu

After install, search for **Android TV Connect** in your desktop environment's application launcher.

## Development

Run from a source checkout without installing:

```bash
PYTHONPATH=. python3 -m android_tv_connect
```

Optional arguments (when implemented):

```bash
android-tv-connect --watch          # background device watcher
android-tv-connect --fullscreen     # start in fullscreen
```

## Configuration

Window geometry and mode are stored in:

```
~/.config/android-tv-connect/window.toml
```

## Uninstall

```bash
systemctl --user disable --now android-tv-connect-watch.service
rm -f ~/.config/systemd/user/android-tv-connect-watch.service
systemctl --user daemon-reload

rm -f ~/.local/bin/android-tv-connect
rm -f ~/.local/share/applications/android-tv-connect.desktop
rm -rf ~/.local/share/android-tv-connect

# Optional: remove udev rules
sudo rm -f /etc/udev/rules.d/99-android-tv-capture.rules
sudo udevadm control --reload-rules
```

## Troubleshooting

**No video device**

```bash
lsusb | grep -i 534d:2109
v4l2-ctl --list-devices
```

Confirm udev rules are installed and the dongle was replugged.

**ADB not detected**

```bash
adb devices -l
adb kill-server && adb start-server
```

Enable USB debugging on the Android TV stick and accept the authorization prompt.

**Watcher not starting at login**

```bash
systemctl --user enable android-tv-connect-watch.service
loginctl show-user "$USER" -p Linger
```

Enable lingering if you need the user service before the first graphical login:

```bash
sudo loginctl enable-linger "$USER"
```

## Support

[GitHub Issues](https://github.com/thothassistantai-web/android-tv-connect/issues) — no email support.
