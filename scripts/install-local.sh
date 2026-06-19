#!/usr/bin/env bash
# Install isolated launcher + versioned app bundle to ~/.local
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_ROOT="${HOME}/.local/share/android-tv-connect"
LAUNCHER_DIR="${DATA_ROOT}/launcher"
VERSIONS_DIR="${DATA_ROOT}/versions"
CURRENT_LINK="${DATA_ROOT}/current"
BIN_DIR="${HOME}/.local/bin"
APPS_DIR="${HOME}/.local/share/applications"
ICONS_DIR="${HOME}/.local/share/icons/hicolor/scalable/apps"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
UDEV_RULES_SRC="${ROOT}/packaging/udev/99-android-tv-capture.rules"
UDEV_RULES_DST="/etc/udev/rules.d/99-android-tv-capture.rules"

VERSION="$(tr -d '[:space:]' < "${ROOT}/VERSION")"
VERSION_DIR="${VERSIONS_DIR}/${VERSION}"

info() { printf '==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }

install_launcher_package() {
    info "Installing launcher to ${LAUNCHER_DIR}"
    mkdir -p "${LAUNCHER_DIR}"
    rsync -a --delete \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        "${ROOT}/android_tv_connect_launcher/" "${LAUNCHER_DIR}/android_tv_connect_launcher/"
}

install_app_bundle() {
    info "Installing app ${VERSION} to ${VERSION_DIR}"
    mkdir -p "${VERSION_DIR}"
    rsync -a --delete \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        "${ROOT}/android_tv_connect/" "${VERSION_DIR}/android_tv_connect/"
    cp -f "${ROOT}/requirements.txt" "${VERSION_DIR}/"
    cp -f "${ROOT}/VERSION" "${VERSION_DIR}/"
    cp -f "${ROOT}/VERSION_CODE" "${VERSION_DIR}/"
    if [[ -f "${ROOT}/README.md" ]]; then
        cp -f "${ROOT}/README.md" "${DATA_ROOT}/"
    fi

    ln -sfn "${VERSION_DIR}" "${CURRENT_LINK}"
    python3 - <<'PY'
import json
from pathlib import Path

root = Path.home() / ".local/share/android-tv-connect"
version = (root / "current" / "VERSION").read_text().strip()
code = int((root / "current" / "VERSION_CODE").read_text().strip())
(root / "installed.json").write_text(
    json.dumps({"version": version, "versionCode": code}, indent=2) + "\n"
)
PY
}

install_bin_script() {
    info "Installing ${BIN_DIR}/atv-connect"
    mkdir -p "${BIN_DIR}"
    cat > "${BIN_DIR}/atv-connect" <<EOF
#!/usr/bin/env bash
set -euo pipefail
LAUNCHER_ROOT="\${HOME}/.local/share/android-tv-connect/launcher"
export PYTHONPATH="\${LAUNCHER_ROOT}\${PYTHONPATH:+:\${PYTHONPATH}}"
exec python3 -m android_tv_connect_launcher "\$@"
EOF
    chmod +x "${BIN_DIR}/atv-connect"

    if [[ -x "${BIN_DIR}/android-tv-connect" ]]; then
        rm -f "${BIN_DIR}/android-tv-connect"
    fi
}

install_icons() {
    local svg="${ROOT}/packaging/icons/android-tv-connect.svg"
    if [[ ! -f "${svg}" ]]; then
        warn "Icon not found at packaging/icons/android-tv-connect.svg"
        return
    fi

    info "Installing application icon"
    mkdir -p "${ICONS_DIR}"
    cp -f "${svg}" "${ICONS_DIR}/android-tv-connect.svg"

    if command -v rsvg-convert >/dev/null 2>&1; then
        for size in 16 24 32 48 64 128 256; do
            local dir="${HOME}/.local/share/icons/hicolor/${size}x${size}/apps"
            mkdir -p "${dir}"
            rsvg-convert -w "${size}" -h "${size}" "${svg}" \
                -o "${dir}/android-tv-connect.png"
        done
        gtk-update-icon-cache "${HOME}/.local/share/icons/hicolor" 2>/dev/null || true
    fi
}

install_desktop_entry() {
    info "Installing desktop entry"
    mkdir -p "${APPS_DIR}"
    cp -f "${ROOT}/packaging/android-tv-connect.desktop" "${APPS_DIR}/android-tv-connect.desktop"
    install_icons
}

install_systemd_user_service() {
    info "Installing systemd user service"
    mkdir -p "${SYSTEMD_USER_DIR}"
    cp -f "${ROOT}/packaging/systemd/android-tv-connect-watch.service" \
        "${SYSTEMD_USER_DIR}/android-tv-connect-watch.service"

    systemctl --user daemon-reload
    systemctl --user enable android-tv-connect-watch.service
    systemctl --user restart android-tv-connect-watch.service || \
        systemctl --user start android-tv-connect-watch.service || true

    info "Watcher enabled. Check status with: systemctl --user status android-tv-connect-watch"
}

install_udev_rules() {
    info "Installing udev rules for MacroSilicon capture (534d:2109)"

    if [[ "${EUID}" -eq 0 ]]; then
        install -Dm644 "${UDEV_RULES_SRC}" "${UDEV_RULES_DST}"
        udevadm control --reload-rules
        udevadm trigger
        info "udev rules installed to ${UDEV_RULES_DST}"
        return
    fi

    if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
        sudo install -Dm644 "${UDEV_RULES_SRC}" "${UDEV_RULES_DST}"
        sudo udevadm control --reload-rules
        sudo udevadm trigger
        info "udev rules installed to ${UDEV_RULES_DST}"
        return
    fi

    warn "Could not install udev rules automatically (root required)."
}

main() {
    install_launcher_package
    install_app_bundle
    install_bin_script
    install_desktop_entry
    install_systemd_user_service
    install_udev_rules

    cat <<EOF

Android TV Connect installed.

  App version:      ${VERSION}
  Entry command:    atv-connect
  Launcher module:  python3 -m android_tv_connect_launcher
  Data directory:   ${DATA_ROOT}
  Watcher service:  systemctl --user status android-tv-connect-watch

EOF
}

main "$@"
