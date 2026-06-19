#!/usr/bin/env bash
# Install Android TV Connect (wrapper — see scripts/install-local.sh).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${ROOT}/scripts/install-local.sh" "$@"
