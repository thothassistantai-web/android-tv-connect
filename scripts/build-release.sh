#!/usr/bin/env bash
# Build application bundle tarball and update-manifest.json for GitHub Releases.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VERSION="$(tr -d '[:space:]' < "${ROOT}/VERSION")"
VERSION_CODE="$(tr -d '[:space:]' < "${ROOT}/VERSION_CODE")"
RELEASE_DIR="${ROOT}/release"
STAGING="${RELEASE_DIR}/staging-${VERSION}"
BUNDLE_NAME="android-tv-connect-${VERSION}.tar.gz"
BUNDLE_PATH="${RELEASE_DIR}/${BUNDLE_NAME}"
MANIFEST_PATH="${RELEASE_DIR}/update-manifest.json"
REPO="thothassistantai-web/android-tv-connect"

echo "==> Building Android TV Connect ${VERSION} (versionCode ${VERSION_CODE})"
rm -rf "${STAGING}"
mkdir -p "${STAGING}" "${RELEASE_DIR}"

rsync -a \
  --exclude='.git' \
  --exclude='.cursor' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='release' \
  --exclude='dist' \
  --exclude='build' \
  --exclude='.pytest_cache' \
  "${ROOT}/android_tv_connect" \
  "${ROOT}/requirements.txt" \
  "${ROOT}/VERSION" \
  "${ROOT}/VERSION_CODE" \
  "${STAGING}/"

tar -czf "${BUNDLE_PATH}" -C "${STAGING}" .
SHA256="$(sha256sum "${BUNDLE_PATH}" | awk '{print $1}')"

DOWNLOAD_URL="https://github.com/${REPO}/releases/download/v${VERSION}/${BUNDLE_NAME}"
NOTES_FILE="${ROOT}/release/RELEASE_NOTES-${VERSION}.txt"
RELEASE_NOTES="See GitHub release notes for v${VERSION}."
if [[ -f "${NOTES_FILE}" ]]; then
  RELEASE_NOTES="$(cat "${NOTES_FILE}")"
fi

cat > "${MANIFEST_PATH}" <<EOF
{
  "version": "${VERSION}",
  "versionCode": ${VERSION_CODE},
  "bundleUrl": "${DOWNLOAD_URL}",
  "sha256": "${SHA256}",
  "mandatory": false,
  "releaseNotes": $(python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' <<<"${RELEASE_NOTES}")
}
EOF

rm -rf "${STAGING}"

echo "==> Bundle: ${BUNDLE_PATH}"
echo "==> SHA256: ${SHA256}"
echo "==> Manifest: ${MANIFEST_PATH}"
echo ""
echo "Upload to GitHub release v${VERSION}:"
echo "  ${BUNDLE_NAME}"
echo "  update-manifest.json"
