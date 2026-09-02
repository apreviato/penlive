#!/bin/bash
# Builds the Debian Live root filesystem for PenLive.
#
# Must run on a Debian/Ubuntu host as root, with live-build installed:
#     sudo apt install live-build
#     sudo ./live/build.sh
#
# Output (consumed by builder/penlive via --live-dir):
#     live/build/out/vmlinuz
#     live/build/out/initrd.img
#     live/build/out/filesystem.squashfs
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIVE_DIR="${REPO_ROOT}/live"
BUILD_DIR="${LIVE_DIR}/build"
OUT_DIR="${BUILD_DIR}/out"

if [[ $EUID -ne 0 ]]; then
    echo "error: live-build must run as root" >&2
    exit 1
fi
if ! command -v lb >/dev/null 2>&1; then
    echo "error: live-build is not installed (apt install live-build)" >&2
    exit 1
fi

echo "==> preparing build tree"
mkdir -p "${BUILD_DIR}"
cp -r "${LIVE_DIR}/auto" "${BUILD_DIR}/"
cp -r "${LIVE_DIR}/config" "${BUILD_DIR}/"

# Stage the manager into includes.chroot so the squashfs ships with the app.
# The frontend must be built first (npm run build) — the API serves its dist/
# as static files, so a missing dist means a live system with no UI.
STAGE="${BUILD_DIR}/config/includes.chroot/opt/penlive"
echo "==> staging manager into ${STAGE}"
mkdir -p "${STAGE}/backend" "${STAGE}/builder" "${STAGE}/docs"
cp -r "${REPO_ROOT}/manager/backend/app" "${STAGE}/backend/"
cp "${REPO_ROOT}/manager/backend/requirements.txt" "${STAGE}/backend/"
cp -r "${REPO_ROOT}/builder/penlive" "${STAGE}/builder/"
cp -r "${REPO_ROOT}/catalog" "${STAGE}/"
for doc in ARCHITECTURE PLUGINS BUILD ADAPTERS; do
    cp "${REPO_ROOT}/docs/${doc}.md" "${STAGE}/docs/" 2>/dev/null || true
done

if [[ -d "${REPO_ROOT}/manager/frontend/dist" ]]; then
    mkdir -p "${STAGE}/frontend"
    cp -r "${REPO_ROOT}/manager/frontend/dist" "${STAGE}/frontend/"
else
    echo "error: manager/frontend/dist not found — run 'npm ci && npm run build' in manager/frontend first" >&2
    exit 1
fi

echo "==> staging systemd units"
UNITS="${BUILD_DIR}/config/includes.chroot/etc/systemd/system"
mkdir -p "${UNITS}"
cp "${REPO_ROOT}"/systemd/*.service "${UNITS}/"

chmod +x "${BUILD_DIR}/config/includes.chroot/opt/penlive/"*.sh
chmod +x "${BUILD_DIR}/config/hooks/live/"*.hook.chroot
chmod +x "${BUILD_DIR}/auto/config"

echo "==> lb config"
cd "${BUILD_DIR}"
lb clean --purge || true
./auto/config

echo "==> lb build (this takes a while)"
lb build

echo "==> collecting artifacts"
mkdir -p "${OUT_DIR}"
cp binary/live/filesystem.squashfs "${OUT_DIR}/filesystem.squashfs"
cp binary/live/vmlinuz*           "${OUT_DIR}/vmlinuz"
cp binary/live/initrd.img*        "${OUT_DIR}/initrd.img"

echo
echo "done. artifacts in ${OUT_DIR}:"
ls -lh "${OUT_DIR}"
