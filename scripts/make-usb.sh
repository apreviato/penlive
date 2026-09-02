#!/bin/bash
# Guided end-to-end build of a PenLive USB stick.
#
#     sudo ./scripts/make-usb.sh
#
# Walks through dependencies, the frontend build, the Debian Live rootfs, target
# selection and the write itself, asking before anything irreversible. Every
# step is skippable when its output already exists, because the live-build stage
# takes 20-40 minutes and there is no reason to repeat it to reflash a stick.
#
# Partitioning is delegated to `penlive` (builder/) rather than reimplemented
# here, so the device guards live in exactly one place.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIVE_OUT="${REPO_ROOT}/live/build/out"
FRONTEND_DIST="${REPO_ROOT}/manager/frontend/dist"
LOG_FILE="${REPO_ROOT}/make-usb.log"

# Defaults mirror builder/penlive/disk.py; overridden for small sticks below.
EFI_MIB=512
SYSTEM_MIB=4096
PERSIST_MIB=8192
DATA_FS=exfat

ASSUME_YES=0
SKIP_BUILD=0
BUILD_ONLY=0
ASSUME_CONFIRMED=0
IMAGE_OUT=""
# Must exceed EFI+SYSTEM+PERSIST (12800) plus the 4096 MiB minimum data
# partition. 16 GiB looks like a natural default but leaves only 3584 MiB and
# is rejected; 20 GiB still fits a 32 GB stick.
IMAGE_SIZE_MIB=20480
TARGET_DEVICE=""

STEP=0
TOTAL_STEPS=6

# ---------------------------------------------------------------- output ----

if [[ -t 1 ]] && [[ -z "${NO_COLOR:-}" ]]; then
    BOLD=$'\e[1m'; DIM=$'\e[2m'; RED=$'\e[31m'; GREEN=$'\e[32m'
    YELLOW=$'\e[33m'; BLUE=$'\e[34m'; RESET=$'\e[0m'
else
    BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; BLUE=""; RESET=""
fi

log()   { printf '%s\n' "$*" | tee -a "${LOG_FILE}" >&2; }
info()  { log "${BLUE}==>${RESET} $*"; }
ok()    { log "${GREEN}  ok${RESET} $*"; }
warn()  { log "${YELLOW}  !${RESET}  $*"; }
err()   { log "${RED}error:${RESET} $*"; }
note()  { log "${DIM}     $*${RESET}"; }

step() {
    STEP=$((STEP + 1))
    log ""
    log "${BOLD}[${STEP}/${TOTAL_STEPS}] $*${RESET}"
    log "${DIM}$(printf '%.0s─' {1..66})${RESET}"
}

die() { err "$*"; log ""; log "Full log: ${LOG_FILE}"; exit 1; }

on_error() {
    local line=$1
    err "failed at line ${line}"
    log "Full log: ${LOG_FILE}"
}
trap 'on_error $LINENO' ERR
trap 'log ""; warn "interrupted"; exit 130' INT

ask() {
    # ask "question" [default_yes]  -> 0 for yes, 1 for no
    local prompt="$1" default="${2:-y}" reply
    if [[ ${ASSUME_YES} -eq 1 ]]; then return 0; fi
    if [[ ! -t 0 ]]; then
        die "need an interactive terminal for '${prompt}' (or pass --yes)"
    fi
    local hint="[Y/n]"
    [[ "${default}" == "n" ]] && hint="[y/N]"
    read -r -p "     ${prompt} ${hint} " reply </dev/tty
    reply="${reply:-${default}}"
    [[ "${reply,,}" == "y" || "${reply,,}" == "yes" ]]
}

# ------------------------------------------------------------------ args ----

usage() {
    cat <<EOF
Guided build of a PenLive USB stick.

    sudo ./scripts/make-usb.sh [options]

Options:
  --device /dev/sdX   Skip the interactive picker and use this device
  --skip-build        Reuse the existing live system, never rebuild it
  --build-only        Build everything and write an .img, touching no device
  --image-out PATH    Where --build-only writes its image
  --image-size-mib N  Size of that image (default ${IMAGE_SIZE_MIB})
  --data-fs FS        exfat (default, readable on Windows) or ext4
  --persist-mib N     Persistence partition size (default ${PERSIST_MIB})
  --system-mib N      System partition size (default ${SYSTEM_MIB})
  --yes               Answer yes to every prompt EXCEPT the final erase
                      confirmation, which always requires typing the device path
  --assume-confirmed  Skip the typed erase confirmation. Only for wrappers that
                      have ALREADY obtained explicit confirmation from the user
                      (scripts/make-usb.ps1 does). Never use it interactively.
  -h, --help          Show this help

Examples:
  sudo ./scripts/make-usb.sh
  sudo ./scripts/make-usb.sh --device /dev/sdb --skip-build
  sudo ./scripts/make-usb.sh --build-only --image-out dist/penlive.img
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --device)           TARGET_DEVICE="$2"; shift 2 ;;
        --skip-build)       SKIP_BUILD=1; shift ;;
        --build-only)       BUILD_ONLY=1; shift ;;
        --image-out)        IMAGE_OUT="$2"; shift 2 ;;
        --image-size-mib)   IMAGE_SIZE_MIB="$2"; shift 2 ;;
        --data-fs)          DATA_FS="$2"; shift 2 ;;
        --persist-mib)      PERSIST_MIB="$2"; shift 2 ;;
        --system-mib)       SYSTEM_MIB="$2"; shift 2 ;;
        --yes)              ASSUME_YES=1; shift ;;
        --assume-confirmed) ASSUME_CONFIRMED=1; shift ;;
        -h|--help)          usage; exit 0 ;;
        *)                  usage >&2; die "unknown option: $1" ;;
    esac
done

if [[ ${BUILD_ONLY} -eq 1 ]]; then
    TOTAL_STEPS=4
    [[ -n "${IMAGE_OUT}" ]] || IMAGE_OUT="${REPO_ROOT}/dist/penlive-amd64.img"
fi

# -------------------------------------------------------------- preflight ----

: > "${LOG_FILE}"

log ""
log "${BOLD}PenLive USB builder${RESET}"
log "${DIM}repository: ${REPO_ROOT}${RESET}"

[[ "$(uname -s)" == "Linux" ]] || die "this script must run on Linux (live-build and the partitioning tools are Linux-only)"

if [[ ${EUID} -ne 0 ]]; then
    die "must run as root: sudo ./scripts/make-usb.sh"
fi

# WSL appends the entire Windows PATH after the Linux one, so `command -v npm`
# happily resolves to C:\Program Files\nodejs\npm. The dependency check then
# passes, Node is never installed inside Debian, and the build fails much later
# inside CMD.EXE complaining that UNC paths are unsupported. Dropping the
# Windows entries removes that whole class of confusion.
if grep -qi microsoft /proc/version 2>/dev/null; then
    _linux_path="$(printf '%s' "${PATH}" | tr ':' '\n' | grep -v '^/mnt/' | paste -sd: -)"
    if [[ -n "${_linux_path}" ]]; then
        export PATH="${_linux_path}"
        log "${DIM}     running under WSL: Windows PATH entries ignored${RESET}"
    fi
fi

# npm as root litters the repo with root-owned node_modules the user then can't
# clean up, so drop back to the invoking account for that one step.
RUN_AS_USER=()
if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    RUN_AS_USER=(sudo -u "${SUDO_USER}")
fi

# ------------------------------------------------------ 1. dependencies ----

step "Checking dependencies"

declare -A PACKAGE_FOR=(
    [lb]=live-build
    [sgdisk]=gdisk
    [mkfs.vfat]=dosfstools
    [mkfs.exfat]=exfatprogs
    [mkfs.ext4]=e2fsprogs
    [grub-mkstandalone]=grub-efi-amd64-bin
    [grub-editenv]=grub-common
    [wipefs]=util-linux
    [partprobe]=parted
    [python3]=python3
    [rsync]=rsync
    # node as well as npm: npm alone can resolve to a Windows shim under WSL
    # while the actual node binary is absent, which fails only at build time.
    [node]=nodejs
    [npm]=npm
    [zstd]=zstd
)

# A command that resolves under /mnt/ is a Windows executable reached through
# WSL interop, not something we can build with. Treat it as absent.
have_linux_cmd() {
    local resolved
    resolved="$(command -v "$1" 2>/dev/null)" || return 1
    [[ "${resolved}" != /mnt/* ]]
}

missing_pkgs=()
for cmd in "${!PACKAGE_FOR[@]}"; do
    if ! have_linux_cmd "${cmd}"; then
        missing_pkgs+=("${PACKAGE_FOR[${cmd}]}")
        warn "missing: ${cmd} (package: ${PACKAGE_FOR[${cmd}]})"
    fi
done

if [[ ${#missing_pkgs[@]} -gt 0 ]]; then
    mapfile -t missing_pkgs < <(printf '%s\n' "${missing_pkgs[@]}" | sort -u)
    if ! command -v apt-get >/dev/null 2>&1; then
        die "missing tools and no apt-get available. Install manually: ${missing_pkgs[*]}"
    fi
    log ""
    note "these will be installed with apt:"
    note "  ${missing_pkgs[*]}"
    if ask "Install them now?"; then
        apt-get update >>"${LOG_FILE}" 2>&1 || die "apt-get update failed - see ${LOG_FILE}"
        apt-get install -y "${missing_pkgs[@]}" >>"${LOG_FILE}" 2>&1 \
            || die "apt-get install failed - see ${LOG_FILE}"
        ok "installed"
    else
        die "cannot continue without: ${missing_pkgs[*]}"
    fi
else
    ok "all required tools present"
fi

# live-build needs room for a full Debian chroot plus the squashfs.
avail_mib=$(df -Pm "${REPO_ROOT}" | awk 'NR==2 {print $4}')
if [[ ${SKIP_BUILD} -eq 0 && ${avail_mib} -lt 15000 ]]; then
    warn "only $((avail_mib / 1024)) GiB free at ${REPO_ROOT}; the live build needs roughly 15 GiB"
    ask "Continue anyway?" n || die "not enough free space"
else
    ok "$((avail_mib / 1024)) GiB free for the build"
fi

# -------------------------------------------------------- 2. frontend ----

step "Building the web interface"

# Vite 8 requires Node ^20.19 || >=22.12. Debian trixie ships 20.19.x, which
# just qualifies; an older base would otherwise fail deep inside the bundler
# with an error that says nothing about Node.
node_version="$(node --version 2>/dev/null | sed 's/^v//')"
if [[ -n "${node_version}" ]]; then
    node_major="${node_version%%.*}"
    node_rest="${node_version#*.}"
    node_minor="${node_rest%%.*}"
    if (( node_major < 20 )) || { (( node_major == 20 )) && (( node_minor < 19 )); }; then
        die "Node ${node_version} is too old to build the interface (need 20.19+ or 22.12+).
     Install a newer Node, e.g. from https://deb.nodesource.com, then re-run."
    fi
    ok "node ${node_version}"
fi

if [[ -d "${FRONTEND_DIST}" ]] && [[ -n "$(ls -A "${FRONTEND_DIST}" 2>/dev/null)" ]]; then
    ok "already built at manager/frontend/dist"
    if ask "Rebuild it?" n; then
        rm -rf "${FRONTEND_DIST}"
    fi
fi

if [[ ! -d "${FRONTEND_DIST}" ]] || [[ -z "$(ls -A "${FRONTEND_DIST}" 2>/dev/null)" ]]; then
    if [[ ${#RUN_AS_USER[@]} -gt 0 ]]; then
        info "running npm ci && npm run build as ${SUDO_USER}"
    else
        info "running npm ci && npm run build"
    fi
    (
        cd "${REPO_ROOT}/manager/frontend"
        # ${arr[@]+"${arr[@]}"} keeps an empty array from tripping `set -u`.
        ${RUN_AS_USER[@]+"${RUN_AS_USER[@]}"} npm ci
        ${RUN_AS_USER[@]+"${RUN_AS_USER[@]}"} npm run build
    ) >>"${LOG_FILE}" 2>&1 || die "frontend build failed - see ${LOG_FILE}"
    ok "built"
fi

# The API serves dist/ as static files; without it the live system boots to a
# blank browser, which is a confusing way to discover a missing build step.
[[ -f "${FRONTEND_DIST}/index.html" ]] || die "frontend build produced no index.html"

# ------------------------------------------------------ 3. live system ----

step "Building the Debian Live system"

live_ready=1
for f in vmlinuz initrd.img filesystem.squashfs; do
    [[ -f "${LIVE_OUT}/${f}" ]] || live_ready=0
done

if [[ ${live_ready} -eq 1 ]]; then
    ok "already built at live/build/out"
    note "$(du -sh "${LIVE_OUT}" | cut -f1) total"
    if [[ ${SKIP_BUILD} -eq 0 ]] && ask "Rebuild it? (20-40 minutes)" n; then
        live_ready=0
    fi
elif [[ ${SKIP_BUILD} -eq 1 ]]; then
    die "--skip-build was given but there is no live system at ${LIVE_OUT}"
fi

# live-build runs for 20-40 minutes. Streaming its raw output would bury the
# script's own messages, but staying silent is worse: squashfs compression in
# particular emits nothing for several minutes, which is indistinguishable from
# a hang. So the full output goes to the log and a summary is shown here.
#
# `read -t` is what makes the quiet stretches survivable: the heartbeat fires on
# the timeout, so elapsed time keeps advancing even when the build says nothing.
live_build_progress() {
    local start=${SECONDS}
    local stage="starting" shown_stage="" activity="preparing"
    local fetched=0 last_tick=0 line rc now

    _elapsed() { printf '%02d:%02d' $(( (SECONDS - start) / 60 )) $(( (SECONDS - start) % 60 )); }
    _emit() { log "     $(_elapsed)  ${stage}$(printf '%*s' $(( 10 - ${#stage} )) '')  ${activity}"; }

    while true; do
        if IFS= read -r -t 5 line; then
            printf '%s\n' "${line}" >> "${LOG_FILE}"

            case "${line}" in
                *"] lb "*)
                    # live-build announces each phase as "[timestamp] lb <stage>"
                    stage="${line##*] lb }"
                    stage="${stage%% *}"
                    stage="${stage%%_*}"
                    # Reset to a per-stage default, or the line carries the
                    # previous phase's activity and reads as nonsense - e.g.
                    # "binary / configuring packages".
                    case "${stage}" in
                        bootstrap) activity="creating the base system" ;;
                        chroot)    activity="installing packages" ;;
                        installer) activity="preparing the installer" ;;
                        binary)    activity="building kernel, initrd and squashfs" ;;
                        source)    activity="collecting sources" ;;
                        *)         activity="working" ;;
                    esac
                    ;;
                "I: Retrieving"*|"I: Validating"*) activity="fetching base system" ;;
                "I: Extracting"*)                  activity="extracting base system" ;;
                "I: Configuring"*|"Setting up "*)  activity="configuring packages" ;;
                "Unpacking "*)                     activity="unpacking packages" ;;
                Get:*)
                    fetched=$(( fetched + 1 ))
                    activity="${fetched} packages downloaded"
                    ;;
                *mksquashfs*|*"Compressing"*|*".squashfs"*) activity="compressing filesystem (quiet for a while)" ;;
                *"Begin unmounting"*)              activity="finishing up" ;;
            esac
        else
            rc=$?
            # read exits >128 on timeout and 1 at end of input; only the latter
            # means the build is over.
            (( rc > 128 )) || break
        fi

        now=${SECONDS}
        if [[ "${stage}" != "${shown_stage}" ]]; then
            shown_stage="${stage}"
            last_tick=${now}
            _emit
        elif (( now - last_tick >= 15 )); then
            last_tick=${now}
            _emit
        fi
    done

    activity="done"
    _emit
}

if [[ ${live_ready} -eq 0 ]]; then
    warn "this downloads a full Debian system and takes 20-40 minutes"
    ask "Start the build?" || die "aborted"
    info "running live/build.sh - full output in ${LOG_FILE}"
    note "progress below updates every 15s; long gaps during compression are normal"
    log ""

    set -o pipefail
    "${REPO_ROOT}/live/build.sh" 2>&1 | live_build_progress
    live_rc=${PIPESTATUS[0]}
    set +o pipefail

    log ""
    (( live_rc == 0 )) || die "live build failed (exit ${live_rc}) - see ${LOG_FILE}"
    ok "built in $(du -sh "${LIVE_OUT}" 2>/dev/null | cut -f1) of artifacts"
fi

for f in vmlinuz initrd.img filesystem.squashfs; do
    [[ -f "${LIVE_OUT}/${f}" ]] || die "live build did not produce ${f}"
done

# ------------------------------------------------- 4. build-only: image ----

if [[ ${BUILD_ONLY} -eq 1 ]]; then
    step "Building a flashable image"

    mkdir -p "$(dirname "${IMAGE_OUT}")"
    info "writing ${IMAGE_OUT} (${IMAGE_SIZE_MIB} MiB)"

    PYTHONPATH="${REPO_ROOT}/builder" python3 -m penlive.cli image "${IMAGE_OUT}" \
        --size-mib "${IMAGE_SIZE_MIB}" \
        --live-dir "${LIVE_OUT}" \
        --grub-cfg "${REPO_ROOT}/grub/grub.cfg" \
        --recovery-cfg "${REPO_ROOT}/grub/recovery.cfg" \
        --catalog "${REPO_ROOT}/catalog/catalog.json" \
        --efi-mib "${EFI_MIB}" --system-mib "${SYSTEM_MIB}" \
        --persist-mib "${PERSIST_MIB}" --data-fs "${DATA_FS}" \
        --log "${LOG_FILE}" \
        2>&1 | tee -a "${LOG_FILE}" >&2 \
        || die "image build failed - see ${LOG_FILE}"

    ok "image ready at ${IMAGE_OUT}"
    log ""
    log "${GREEN}${BOLD}Done.${RESET} Flash it with:"
    log "    sudo ./scripts/make-usb.sh --device /dev/sdX --skip-build"
    log "  or on Windows:"
    log "    .\\scripts\\make-usb.ps1 -Image <path to the image>"
    log ""
    exit 0
fi

# ---------------------------------------------------- 4. target device ----

step "Choosing the target USB stick"

root_disk="$(findmnt / -no SOURCE 2>/dev/null | sed -E 's#p?[0-9]+$##')"

# Parsed as JSON rather than columns: drive models routinely contain spaces
# ("SanDisk Extreme Pro"), which silently corrupts positional field splitting
# and could end up showing the wrong device for a number the user then picks.
list_candidates() {
    lsblk -db -J -o PATH,SIZE,MODEL,TRAN,RM,TYPE | python3 -c '
import json, sys
for d in json.load(sys.stdin)["blockdevices"]:
    if d.get("type") != "disk":
        continue
    print("\t".join([
        d["path"],
        str(d.get("size") or 0),
        (d.get("tran") or "-"),
        "1" if d.get("rm") else "0",
        (d.get("model") or "").strip(),
    ]))
'
}

if [[ -z "${TARGET_DEVICE}" ]]; then
    mapfile -t rows < <(list_candidates)
    [[ ${#rows[@]} -gt 0 ]] || die "no block devices found"

    log ""
    printf '     %-4s %-14s %10s  %-6s %-4s %s\n' "#" "DEVICE" "SIZE" "BUS" "REM" "MODEL" \
        | tee -a "${LOG_FILE}" >&2
    idx=0
    declare -a paths=()
    for row in "${rows[@]}"; do
        IFS=$'\t' read -r path size tran rm_flag model <<<"${row}"
        idx=$((idx + 1))
        paths+=("${path}")

        marker=""
        [[ "${path}" == "${root_disk}" ]] && marker="${RED}<- SYSTEM DISK${RESET}"
        [[ "${rm_flag}" == "0" && -z "${marker}" ]] && marker="${YELLOW}<- not removable${RESET}"

        printf '     %-4s %-14s %9.1fG  %-6s %-4s %s %s\n' \
            "${idx})" "${path}" "$(awk "BEGIN{printf \"%.1f\", ${size}/1073741824}")" \
            "${tran}" "$([[ ${rm_flag} == 1 ]] && echo yes || echo no)" \
            "${model}" "${marker}" | tee -a "${LOG_FILE}" >&2
    done
    log ""

    if [[ ${ASSUME_YES} -eq 1 ]]; then
        die "--yes requires --device, since the target cannot be guessed safely"
    fi
    [[ -t 0 ]] || die "need an interactive terminal to choose a device (or pass --device)"

    read -r -p "     Which device? [1-${idx}] " choice </dev/tty
    [[ "${choice}" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= idx )) \
        || die "invalid selection: ${choice}"
    TARGET_DEVICE="${paths[$((choice - 1))]}"
fi

[[ -b "${TARGET_DEVICE}" ]] || die "${TARGET_DEVICE} is not a block device"

if [[ "${TARGET_DEVICE}" == "${root_disk}" ]]; then
    die "${TARGET_DEVICE} is the disk this system is running from. Refusing."
fi

device_bytes=$(blockdev --getsize64 "${TARGET_DEVICE}")
device_mib=$((device_bytes / 1048576))

# --------------------------------------------------------- 5. layout ----

step "Planning the partition layout"

fixed_mib=$((EFI_MIB + SYSTEM_MIB + PERSIST_MIB))
MIN_DATA_MIB=4096
needed_mib=$((fixed_mib + MIN_DATA_MIB))

if [[ ${device_mib} -lt ${needed_mib} ]]; then
    warn "${TARGET_DEVICE} holds $((device_mib / 1024)) GiB, but the default layout needs $((needed_mib / 1024)) GiB"
    # Shrink persistence first: it is the partition whose size matters least on
    # a small stick, and downloaded ISOs live on DATA, not here.
    shrunk_persist=$((device_mib - EFI_MIB - SYSTEM_MIB - MIN_DATA_MIB))
    if [[ ${shrunk_persist} -ge 1024 ]]; then
        note "persistence could be reduced from ${PERSIST_MIB} MiB to ${shrunk_persist} MiB"
        if ask "Use the smaller persistence partition?"; then
            PERSIST_MIB=${shrunk_persist}
            fixed_mib=$((EFI_MIB + SYSTEM_MIB + PERSIST_MIB))
        else
            die "device too small for the requested layout"
        fi
    else
        die "${TARGET_DEVICE} is too small for PenLive (need at least $((needed_mib / 1024)) GiB, 32 GB or larger recommended)"
    fi
fi

data_mib=$((device_mib - fixed_mib))

log ""
note "$(printf '%-14s %-8s %10s  %s' 'PARTITION' 'FORMAT' 'SIZE' 'CONTENTS')"
note "$(printf '%-14s %-8s %9sM  %s' 'PENEFI'     'fat32'      "${EFI_MIB}"     'GRUB bootloader')"
note "$(printf '%-14s %-8s %9sM  %s' 'PENSYS'     'ext4'       "${SYSTEM_MIB}"  'kernel, squashfs, boot state')"
note "$(printf '%-14s %-8s %9sM  %s' 'persistence' 'ext4'       "${PERSIST_MIB}" 'settings, Wi-Fi, keyboard')"
note "$(printf '%-14s %-8s %9sM  %s' 'PENDATA'    "${DATA_FS}" "${data_mib}"    'downloaded ISOs, backups')"
log ""

# ---------------------------------------------------------- 6. write ----

step "Writing to ${TARGET_DEVICE}"

log ""
log "     ${BOLD}${RED}Everything on ${TARGET_DEVICE} will be erased.${RESET}"
log ""
lsblk -o PATH,SIZE,FSTYPE,LABEL,MOUNTPOINT "${TARGET_DEVICE}" | sed 's/^/     /' | tee -a "${LOG_FILE}" >&2
log ""

for mp in $(lsblk -nro MOUNTPOINT "${TARGET_DEVICE}" | grep -v '^$' || true); do
    warn "currently mounted at ${mp}"
    if ask "Unmount it?"; then
        umount "${mp}" || die "could not unmount ${mp}"
        ok "unmounted ${mp}"
    else
        die "cannot write to a mounted device"
    fi
done

if ask "Show the exact command plan first (dry run)?" n; then
    log ""
    PYTHONPATH="${REPO_ROOT}/builder" python3 -m penlive.cli install "${TARGET_DEVICE}" \
        --dry-run --yes \
        --live-dir "${LIVE_OUT}" \
        --grub-cfg "${REPO_ROOT}/grub/grub.cfg" \
        --recovery-cfg "${REPO_ROOT}/grub/recovery.cfg" \
        --catalog "${REPO_ROOT}/catalog/catalog.json" \
        --efi-mib "${EFI_MIB}" --system-mib "${SYSTEM_MIB}" \
        --persist-mib "${PERSIST_MIB}" --data-fs "${DATA_FS}" \
        2>&1 | sed 's/^/     /' | tee -a "${LOG_FILE}" >&2
    log ""
fi

# Always required, even under --yes: this is the irreversible step. The one
# exception is a wrapper that already took an explicit confirmation from the
# user for this same device (scripts/make-usb.ps1), where asking twice for the
# same disk under two different names is confusing rather than safer.
if [[ ${ASSUME_CONFIRMED} -eq 1 ]]; then
    warn "erase already confirmed by the calling wrapper"
else
    [[ -t 0 ]] || die "need an interactive terminal for the erase confirmation"
    log "     Type the device path to confirm erasing it."
    read -r -p "     ${TARGET_DEVICE} > " typed </dev/tty
    [[ "${typed}" == "${TARGET_DEVICE}" ]] || die "confirmation did not match; nothing was written"
fi

log ""
info "writing - do not remove the stick"

PYTHONPATH="${REPO_ROOT}/builder" python3 -m penlive.cli install "${TARGET_DEVICE}" \
    --yes \
    --live-dir "${LIVE_OUT}" \
    --grub-cfg "${REPO_ROOT}/grub/grub.cfg" \
    --recovery-cfg "${REPO_ROOT}/grub/recovery.cfg" \
    --catalog "${REPO_ROOT}/catalog/catalog.json" \
    --efi-mib "${EFI_MIB}" --system-mib "${SYSTEM_MIB}" \
    --persist-mib "${PERSIST_MIB}" --data-fs "${DATA_FS}" \
    --log "${LOG_FILE}" \
    2>&1 | tee -a "${LOG_FILE}" >&2 \
    || die "install failed - see ${LOG_FILE}"

sync
ok "written"

info "verifying"
if PYTHONPATH="${REPO_ROOT}/builder" python3 -m penlive.cli validate "${TARGET_DEVICE}" \
        --data-fs "${DATA_FS}" >>"${LOG_FILE}" 2>&1; then
    ok "layout verified"
else
    die "verification failed - see ${LOG_FILE}"
fi

# ---------------------------------------------------------------- done ----

log ""
log "${GREEN}${BOLD}PenLive is ready on ${TARGET_DEVICE}${RESET}"
log ""
log "  To boot it:"
log "    1. Leave the stick plugged in and restart the machine"
log "    2. Open the firmware boot menu (usually F12, F10, Esc or Del)"
log "    3. Choose the USB device"
log ""
log "  ${YELLOW}Secure Boot must be disabled${RESET} - it is not supported yet."
log "  First boot asks for keyboard layout and Wi-Fi, then shows the catalog."
log ""
log "  ${DIM}Log: ${LOG_FILE}${RESET}"
log ""
