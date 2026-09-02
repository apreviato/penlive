#!/bin/bash
# Throwaway: runs only the guard block out of live/build.sh, in this environment.
set -uo pipefail
REPO_ROOT=/mnt/d/repos/penlive
LIVE_DIR="${REPO_ROOT}/live"
BUILD_DIR="${LIVE_DIR}/build"
sed -n '/^echo "==> checking line endings"$/,/^fi$/p' "${REPO_ROOT}/live/build.sh" > /tmp/guard_block.sh
bash /tmp/guard_block.sh
echo "guard exit: $?"
