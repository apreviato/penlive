#!/bin/sh
# Runtime partitions are not present while the squashfs is built, so their
# writable directories must be created and owned after fstab has mounted them.
set -eu

if mountpoint -q /boot; then
    for path in /boot/state /boot/extracted; do
        mkdir -p "$path"
        chown -R penlive:penlive "$path"
        chmod 0775 "$path"
    done
    mkdir -p /boot/grub

    # Images made by early PenLive builds did not always carry this file, and
    # an interrupted ext4 repair can leave it invalid. Native boot only needs a
    # standard 1 KiB GRUB environment block, so repair it during startup. Use
    # an atomic rename so firmware can never observe a half-created block.
    if ! grub-editenv /boot/grub/grubenv list >/dev/null 2>&1; then
        rm -f /boot/grub/.grubenv.tmp
        grub-editenv /boot/grub/.grubenv.tmp create
        grub-editenv /boot/grub/.grubenv.tmp set boot_attempts=0 next_entry=
        mv -f /boot/grub/.grubenv.tmp /boot/grub/grubenv
    fi
    # Only the privileged daemon and GRUB need to change this file. The API
    # can still inspect it through grub-editenv because it is world-readable.
    chown root:root /boot/grub/grubenv
    chmod 0644 /boot/grub/grubenv
fi

if mountpoint -q /data; then
    for path in /data/images /data/images/.downloads /data/backups /data/recovered /data/catalog /data/logs; do
        mkdir -p "$path"
        chown -R penlive:penlive "$path"
    done
fi
