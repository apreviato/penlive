#!/bin/sh
# Runtime partitions are not present while the squashfs is built, so their
# writable directories must be created and owned after fstab has mounted them.
#
# PENSYS is mounted on /boot, and everything GRUB reads lives at the root of
# that partition: /boot/grub, /boot/state, /boot/extracted, /boot/live. GRUB
# sees the identical paths as ($root)/grub, ($root)/state and so on, which is
# what makes "the manager wrote nextboot.cfg" and "GRUB found nextboot.cfg"
# the same sentence.
set -eu

GRUB_SRC=/opt/penlive/grub

# PENDATA first, deliberately. A PENSYS that has gone read-only after an
# unclean unplug ends this script under set -e, and downloads have nothing to
# do with that partition - preparing them first keeps one failure from becoming
# two. The manager reports an unwritable /boot with something the user can act
# on; it cannot report a /data it was never allowed to create.
if mountpoint -q /data; then
    for path in /data/images /data/images/.downloads /data/backups /data/recovered \
                /data/catalog /data/logs /data/vm-sessions; do
        mkdir -p "$path"
        chown -R penlive:penlive "$path"
    done
fi

if mountpoint -q /boot; then
    # Sticks built before the layout was flattened keep grub/state/extracted a
    # level down, where the running system writes to a different set of
    # directories than GRUB reads. Lift them, and leave the old grub.cfg path
    # as a one-line redirect so an ESP stub from that build still finds the
    # menu.
    if [ -d /boot/boot/grub ] && [ ! -e /boot/grub/grub.cfg ]; then
        mkdir -p /boot/grub
        for legacy in /boot/boot/grub/*; do
            # The glob stays literal when the directory is empty, and an
            # unguarded test would end the script under set -e.
            if [ -e "$legacy" ]; then
                mv -f "$legacy" /boot/grub/
            fi
        done
        for legacy in state extracted wimboot; do
            if [ -e "/boot/boot/$legacy" ] && [ ! -e "/boot/$legacy" ]; then
                mv -f "/boot/boot/$legacy" "/boot/$legacy"
            fi
        done
        mkdir -p /boot/boot/grub
        printf 'configfile ($root)/grub/grub.cfg\n' > /boot/boot/grub/grub.cfg
    fi

    for path in /boot/state /boot/extracted; do
        mkdir -p "$path"
        chown -R penlive:penlive "$path"
        chmod 0775 "$path"
    done
    mkdir -p /boot/grub

    # Ship the menu with the squashfs rather than only with the flashing tool:
    # a boot-logic fix then reaches an existing stick on its next start instead
    # of requiring the whole device to be rewritten.
    for cfg in grub.cfg recovery.cfg; do
        if [ -f "${GRUB_SRC}/${cfg}" ]; then
            install -m 0644 "${GRUB_SRC}/${cfg}" "/boot/grub/${cfg}"
        fi
    done

    # Images made by early PenLive builds did not always carry this file, and
    # an interrupted ext4 repair can leave it invalid. Native boot only needs a
    # standard 1 KiB GRUB environment block, so repair it during startup. Use
    # an atomic rename so firmware can never observe a half-created block.
    if ! grub-editenv /boot/grub/grubenv list >/dev/null 2>&1; then
        rm -f /boot/grub/.grubenv.tmp
        grub-editenv /boot/grub/.grubenv.tmp create
        grub-editenv /boot/grub/.grubenv.tmp set next_entry=
        mv -f /boot/grub/.grubenv.tmp /boot/grub/grubenv
    fi
    # Only the privileged daemon and GRUB need to change this file. The API
    # can still inspect it through grub-editenv because it is world-readable.
    chown root:root /boot/grub/grubenv
    chmod 0644 /boot/grub/grubenv
fi
