#!/bin/bash
# Grow PENDATA to fill the stick, once, on the first boot after flashing.
#
# Writing a fixed-size image to a larger stick leaves the rest of the device
# unallocated: a 20 GiB image on a 57 GB stick wastes 37 GB that should be
# holding ISOs. Sizing the image to the device instead would mean writing tens
# of GB of zeroes over USB, so the growth happens here instead - the live
# system already has sgdisk and mkfs.exfat, and this is how appliance images
# normally handle it.
#
# The trigger is deliberately narrow: a GPT whose backup header is not at the
# end of the device. That is true exactly once, right after flashing an image
# smaller than the stick, and false forever after this script fixes it. It
# cannot fire on a stick that was partitioned directly (the builder already
# used the whole device), and it cannot fire twice.
#
#     --dry-run   print the plan and change nothing
set -euo pipefail

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

LOG_TAG="penlive-expand"
say() { printf '%s\n' "$*"; command -v logger >/dev/null 2>&1 && logger -t "${LOG_TAG}" "$*" || true; }
run() {
    if [[ ${DRY_RUN} -eq 1 ]]; then
        printf '  would run: %s\n' "$*"
    else
        "$@"
    fi
}

# --- locate our own device via the PENSYS label -----------------------------
# Deriving the disk from the partition we booted means we can never touch
# another drive, however many are plugged in.

pensys_part="$(blkid -L PENSYS 2>/dev/null || true)"
if [[ -z "${pensys_part}" ]]; then
    say "no PENSYS partition found; not a PenLive stick, nothing to do"
    exit 0
fi

parent="$(lsblk -no PKNAME "${pensys_part}" 2>/dev/null | head -1 | tr -d ' ')"
if [[ -z "${parent}" ]]; then
    say "could not determine the parent disk of ${pensys_part}"
    exit 0
fi
disk="/dev/${parent}"

data_part="$(blkid -L PENDATA 2>/dev/null || true)"
if [[ -z "${data_part}" ]]; then
    say "no PENDATA partition found on ${disk}"
    exit 0
fi

# PENDATA must be the last partition, or growing it would overwrite whatever
# follows.
last_num="$(sgdisk -p "${disk}" 2>/dev/null | awk '/^ *[0-9]+ /{n=$1} END{print n}')"
data_num="${data_part##*[a-z]}"
if [[ "${data_num}" != "${last_num}" ]]; then
    say "PENDATA is partition ${data_num} but ${last_num} is last; refusing to grow"
    exit 0
fi

# --- is there anything to reclaim? ------------------------------------------

disk_sectors="$(blockdev --getsz "${disk}")"
# sgdisk reports the last usable sector, which sits just below the backup GPT.
last_usable="$(sgdisk -p "${disk}" 2>/dev/null | awk '/last usable sector is/{print $NF}' | tr -d '.')"
if [[ -z "${last_usable}" ]]; then
    last_usable="$(sgdisk -i "${data_num}" "${disk}" 2>/dev/null | awk '/Last sector/{print $3}')"
fi

data_end="$(sgdisk -i "${data_num}" "${disk}" 2>/dev/null | awk '/Last sector/{print $3}')"
data_start="$(sgdisk -i "${data_num}" "${disk}" 2>/dev/null | awk '/First sector/{print $3}')"

if [[ -z "${data_end}" || -z "${data_start}" ]]; then
    say "could not read PENDATA geometry; leaving the disk alone"
    exit 0
fi

# 1 GiB of slack before bothering: the tail of a GPT disk always has a little
# unusable space, and reformatting to reclaim a few MB is not worth the risk.
slack_sectors=$(( disk_sectors - data_end ))
if (( slack_sectors < 2097152 )); then
    say "PENDATA already fills ${disk} (${slack_sectors} spare sectors); nothing to do"
    exit 0
fi

reclaim_gib=$(( slack_sectors / 2097152 ))
say "PENDATA ends at sector ${data_end} of ${disk_sectors} on ${disk}: about ${reclaim_gib} GiB unused"

# --- refuse if there is anything to lose ------------------------------------
# The partition is recreated and reformatted rather than resized, because
# exfatprogs has no resize tool. That is only acceptable while PENDATA is
# effectively empty, which is the case on the first boot after flashing.

mountpoint=/run/penlive-expand
mkdir -p "${mountpoint}"
occupied=0
if mount -t exfat -o ro "${data_part}" "${mountpoint}" 2>/dev/null; then
    # catalog/ and the empty download dir are seeded by the builder and are
    # regenerated, so they do not count as user data.
    occupied="$(find "${mountpoint}" -mindepth 1 -maxdepth 2 \
                     -not -path "${mountpoint}/catalog*" \
                     -not -path "${mountpoint}/logs*" \
                     -not -path "${mountpoint}/images" \
                     -not -path "${mountpoint}/images/.downloads" \
                     -not -path "${mountpoint}/System Volume Information*" \
                     2>/dev/null | head -1 | wc -l)"
    umount "${mountpoint}" 2>/dev/null || true
fi
rmdir "${mountpoint}" 2>/dev/null || true

if [[ "${occupied}" != "0" ]]; then
    say "PENDATA already holds files; refusing to reformat. Grow it manually if you want the space."
    exit 0
fi

# --- grow --------------------------------------------------------------------

say "growing PENDATA to fill ${disk}"

# The backup GPT still sits where the image ended; move it to the true end of
# the device first, or the kernel keeps reporting the old, smaller geometry.
run sgdisk -e "${disk}"

run sgdisk -d "${data_num}" "${disk}"
run sgdisk -n "${data_num}:${data_start}:0" -t "${data_num}:0700" -c "${data_num}:PENDATA" "${disk}"

run partprobe "${disk}"
run udevadm settle

run mkfs.exfat -n PENDATA "${data_part}"

# Recreate the directory skeleton the manager expects. catalog.json is not
# restored here: the API falls back to the copy bundled in the squashfs and
# refreshes from the network, so an empty catalog directory is fine.
if [[ ${DRY_RUN} -eq 0 ]]; then
    mkdir -p "${mountpoint}"
    if mount -t exfat "${data_part}" "${mountpoint}"; then
        mkdir -p "${mountpoint}/images/.downloads" "${mountpoint}/catalog" "${mountpoint}/logs"
        umount "${mountpoint}"
    fi
    rmdir "${mountpoint}" 2>/dev/null || true
fi

say "PENDATA now fills ${disk}"
