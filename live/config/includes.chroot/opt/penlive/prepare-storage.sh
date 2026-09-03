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

    # Images made by early PenLive builds did not always carry this file, and
    # an interrupted ext4 repair can leave it invalid. Native boot only needs a
    # standard 1 KiB GRUB environment block, so repair it during startup. Use
    # an atomic rename so firmware can never observe a half-created block.
    if ! grub-editenv /boot/state/bootenv list >/dev/null 2>&1; then
        rm -f /boot/state/.bootenv.tmp
        grub-editenv /boot/state/.bootenv.tmp create
        grub-editenv /boot/state/.bootenv.tmp set boot_attempts=0 next_entry=
        mv -f /boot/state/.bootenv.tmp /boot/state/bootenv
    fi
    chown penlive:penlive /boot/state/bootenv
    chmod 0664 /boot/state/bootenv
fi

if mountpoint -q /data; then
    for path in /data/images /data/images/.downloads /data/backups /data/recovered /data/catalog; do
        mkdir -p "$path"
        chown -R penlive:penlive "$path"
    done
fi
