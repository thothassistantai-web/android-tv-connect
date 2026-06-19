# scrcpy screen mirroring

Android TV Connect can launch [scrcpy](https://github.com/Genymobile/scrcpy) to mirror the Android TV stick over the **same ADB session** used for remote control (wired USB or Wi‑Fi).

## Install scrcpy

```bash
sudo apt install scrcpy
```

Or set a custom executable path in **Settings → Screen mirror (scrcpy)**.

## Usage

1. Connect ADB (wired or wireless) using the existing **ADB** chip or Settings.
2. Click the **Mirror** chip in the header, or press **Shift+F8**.
3. Click **Mirror** again (or Shift+F8) to stop scrcpy.

The app picks the active transport automatically:

- Respects **Prefer wired ADB** when both USB and Wi‑Fi are available.
- Triggers the same `adb connect` flow for wireless if needed before launch.

scrcpy runs in a separate window; HDMI capture in the main app is unchanged.

## Settings

**Settings → Screen mirror (scrcpy)**

| Option | Default | Notes |
|--------|---------|--------|
| Auto-launch on ADB connect | off | Opens scrcpy when a device connects |
| scrcpy path | (PATH) | Override if scrcpy is not on PATH |
| Max size | 1920 | Longest edge in pixels; `0` = native |
| Video bit rate | 8M | Passed to `--video-bit-rate` |
| Window title | Android TV Connect | scrcpy window title |
| Start fullscreen | off | `--fullscreen` |
| No audio | on | Recommended for TV sticks |
| Stay awake | on | `--stay-awake` |
| Turn screen off | off | `--turn-screen-off` |

## Config file

```json
{
  "scrcpy": {
    "auto_launch_on_connect": false,
    "scrcpy_path": "",
    "max_size": 1920,
    "bit_rate": "8M",
    "fullscreen": false,
    "no_audio": true,
    "stay_awake": true,
    "turn_screen_off": false,
    "window_title": "Android TV Connect"
  }
}
```

Stored in `~/.config/android-tv-connect/config.json` alongside ADB and capture settings.

## Keyboard shortcut

Default: **Shift+F8** — toggle mirror. Customizable under **Settings → Keyboard Shortcuts**.

## Troubleshooting

**“scrcpy not found”**

Install the package or set **scrcpy path** to the full path of the binary.

**Mirror fails after wireless connect**

Confirm `adb devices -l` shows the TV at the configured IP:port. Use **Settings → ADB Connection → Refresh devices**.

**scrcpy exits immediately**

Check the app log (`journalctl` if launched via systemd watch) for `scrcpy:` lines. Common causes: unauthorized USB debugging, another scrcpy instance, or insufficient encoder support on the stick.

## Logs

scrcpy stdout/stderr is forwarded to the Python logger at INFO level with the `scrcpy:` prefix.
