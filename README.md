# Android TV Connect

**Version 1.1.0** — Stream an Android TV stick through a MacroSilicon MS2109 HDMI capture dongle and control it with ADB from a GTK4 desktop app.

**Repository:** https://github.com/thothassistantai-web/android-tv-connect

## Quick start

```bash
chmod +x install.sh
./install.sh
atv-connect
```

The **launcher** (`android_tv_connect_launcher`) checks GitHub for updates, installs versioned app bundles, then starts the main app. If the GTK app breaks, `atv-connect` still runs to ship fixes. See [docs/UPDATES.md](docs/UPDATES.md).

Check versions:

```bash
atv-connect --version
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

```bash
./scripts/install-local.sh
```

Or `./install.sh` (same installer).

| Path | Purpose |
|------|---------|
| `~/.local/share/android-tv-connect/launcher/` | Stable updater (rarely changes) |
| `~/.local/share/android-tv-connect/versions/<ver>/` | Versioned app bundles |
| `~/.local/share/android-tv-connect/current` | Symlink to active version |
| `~/.local/bin/atv-connect` | User entry command |
| `~/.local/share/applications/android-tv-connect.desktop` | App menu entry |
| `~/.config/systemd/user/android-tv-connect-watch.service` | Auto-launch watcher |

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
atv-connect
```

Equivalent:

```bash
python3 -m android_tv_connect_launcher
```

### Watch mode (auto-launch)

```bash
systemctl --user status android-tv-connect-watch
systemctl --user restart android-tv-connect-watch
journalctl --user -u android-tv-connect-watch -f
```

The watcher runs `atv-connect --watch`, which checks for updates then starts the device poller.

### Updates

- **Automatic:** Settings → Updates → *Check on launch* (default on)
- **Manual:** Settings → *Check for updates now*
- **Recovery:** `atv-connect --apply-updates`

## Development

Run from a source checkout:

```bash
PYTHONPATH=. python3 -m android_tv_connect_launcher
```

Main app only (no update check):

```bash
PYTHONPATH=. python3 -m android_tv_connect
```

### Tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

### Release build

```bash
./scripts/build-release.sh
```

Upload `release/android-tv-connect-<version>.tar.gz` and `release/update-manifest.json` to a GitHub release.

## Configuration

Settings file:

```
~/.config/android-tv-connect/config.json
```

Window geometry:

```
~/.config/android-tv-connect/window.toml
```

## Uninstall

```bash
systemctl --user disable --now android-tv-connect-watch.service
rm -f ~/.config/systemd/user/android-tv-connect-watch.service
systemctl --user daemon-reload

rm -f ~/.local/bin/atv-connect
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

**ADB not detected**

```bash
adb devices -l
adb kill-server && adb start-server
```

**Updates not applying**

```bash
atv-connect --check-updates --json --force-check
atv-connect --apply-updates
```

See [docs/UPDATES.md](docs/UPDATES.md) for architecture and manifest format.

## Support

[GitHub Issues](https://github.com/thothassistantai-web/android-tv-connect/issues)
