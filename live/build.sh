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

# live-build emits both an unversioned name and a versioned hardlink, e.g.
# vmlinuz alongside vmlinuz-6.12.107+deb13-amd64. A `cp binary/live/vmlinuz* dest`
# therefore passes cp two sources and one non-directory target, and the whole
# 40-minute build fails at the very last step with "No such file or directory".
collect_artifact() {
    local base="$1" dest="$2"

    if [[ -f "binary/live/${base}" ]]; then
        cp "binary/live/${base}" "${dest}"
        return
    fi

    # Older/newer layouts may only ship the versioned name. Accept it, but only
    # when exactly one candidate exists - picking arbitrarily from several
    # kernel flavours would produce a stick that boots the wrong one.
    local matches=()
    shopt -s nullglob
    matches=(binary/live/"${base}"-*)
    shopt -u nullglob

    if [[ ${#matches[@]} -eq 1 ]]; then
        cp "${matches[0]}" "${dest}"
    elif [[ ${#matches[@]} -eq 0 ]]; then
        echo "error: live-build produced no ${base} in binary/live/" >&2
        exit 1
    else
        echo "error: several ${base} candidates in binary/live/: ${matches[*]}" >&2
        echo "       cannot choose between kernel flavours automatically" >&2
        exit 1
    fi
}

cp binary/live/filesystem.squashfs "${OUT_DIR}/filesystem.squashfs"
collect_artifact vmlinuz    "${OUT_DIR}/vmlinuz"
collect_artifact initrd.img "${OUT_DIR}/initrd.img"

echo
echo "done. artifacts in ${OUT_DIR}:"
ls -lh "${OUT_DIR}"
