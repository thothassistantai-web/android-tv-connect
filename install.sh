#!/usr/bin/env bash
# Install Android TV Connect to ~/.local (no root required except udev rules).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_ROOT="${HOME}/.local/share/android-tv-connect"
BIN_DIR="${HOME}/.local/bin"
APPS_DIR="${HOME}/.local/share/applications"
ICONS_DIR="${HOME}/.local/share/icons/hicolor/scalable/apps"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
UDEV_RULES_SRC="${SCRIPT_DIR}/packaging/udev/99-android-tv-capture.rules"
UDEV_RULES_DST="/etc/udev/rules.d/99-android-tv-capture.rules"

info() { printf '==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }

install_app_files() {
    info "Installing application to ${INSTALL_ROOT}"
    mkdir -p "${INSTALL_ROOT}"

    if [[ -d "${SCRIPT_DIR}/android_tv_connect" ]]; then
        rsync -a --delete \
            --exclude='.git' \
            --exclude='.cursor' \
            --exclude='packaging' \
            --exclude='__pycache__' \
            --exclude='*.pyc' \
            "${SCRIPT_DIR}/android_tv_connect" "${INSTALL_ROOT}/"
    else
        warn "android_tv_connect package not found yet; creating launcher only."
        mkdir -p "${INSTALL_ROOT}/android_tv_connect"
        touch "${INSTALL_ROOT}/android_tv_connect/__init__.py"
    fi

    cp -f "${SCRIPT_DIR}/requirements.txt" "${INSTALL_ROOT}/"
    if [[ -f "${SCRIPT_DIR}/VERSION" ]]; then
        cp -f "${SCRIPT_DIR}/VERSION" "${INSTALL_ROOT}/VERSION"
        cp -f "${SCRIPT_DIR}/VERSION" "${INSTALL_ROOT}/android_tv_connect/VERSION"
    fi
    if [[ -f "${SCRIPT_DIR}/README.md" ]]; then
        cp -f "${SCRIPT_DIR}/README.md" "${INSTALL_ROOT}/"
    fi
}

install_launcher() {
    info "Installing launcher to ${BIN_DIR}/android-tv-connect"
    mkdir -p "${BIN_DIR}"

    cat > "${BIN_DIR}/android-tv-connect" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
INSTALL_ROOT="${HOME}/.local/share/android-tv-connect"
export PYTHONPATH="${INSTALL_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
exec python3 -m android_tv_connect "$@"
EOF
    chmod +x "${BIN_DIR}/android-tv-connect"
}

install_icons() {
    local svg="${SCRIPT_DIR}/packaging/icons/android-tv-connect.svg"
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
    cp -f "${SCRIPT_DIR}/packaging/android-tv-connect.desktop" "${APPS_DIR}/android-tv-connect.desktop"
    install_icons
}

install_systemd_user_service() {
    info "Installing systemd user service"
    mkdir -p "${SYSTEMD_USER_DIR}"
    cp -f "${SCRIPT_DIR}/packaging/systemd/android-tv-connect-watch.service" \
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
    cat <<EOF

To grant access to the capture device without running as root, install the udev
rules manually:

  sudo install -Dm644 "${UDEV_RULES_SRC}" "${UDEV_RULES_DST}"
  sudo udevadm control --reload-rules
  sudo udevadm trigger

Then unplug and replug the MacroSilicon dongle (or reboot).

EOF
}

main() {
    install_app_files
    install_launcher
    install_desktop_entry
    install_systemd_user_service
    install_udev_rules

    cat <<EOF

Android TV Connect installed.

  Version:          $(cat "${SCRIPT_DIR}/VERSION" 2>/dev/null || echo unknown)
  Launch manually:  android-tv-connect
  Version flag:     android-tv-connect --version
  Watcher service:  systemctl --user status android-tv-connect-watch

EOF
}

main "$@"
