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
fi

if mountpoint -q /data; then
    for path in /data/images /data/images/.downloads /data/backups /data/recovered /data/catalog; do
        mkdir -p "$path"
        chown -R penlive:penlive "$path"
    done
fi
