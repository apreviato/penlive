# Architecture

This document records **why** each decision was made. The code says what it
does; here is the reasoning that does not survive in comments.

## Boot chain

```
UEFI
 └─ EFI/BOOT/BOOTX64.EFI          (standalone GRUB, on the PENEFI partition)
     └─ configfile → PENSYS/boot/grub/grub.cfg
         ├─ menuentry "PenLive Manager"          (default)
         ├─ source boot/state/nextboot.cfg       (if present and attempts < 3)
         └─ source boot/grub/recovery.cfg        (always)
             └─ vmlinuz + initrd → live-boot
                 ├─ PENSYS/live/filesystem.squashfs   (read-only)
                 └─ LABEL=persistence partition       (OverlayFS, writable)
                     └─ systemd
                         ├─ NetworkManager
                         ├─ penlive-daemon   (root, Unix socket)
                         ├─ penlive-aria2    (penlive user)
                         ├─ penlive-api      (penlive user, :7777)
                         └─ penlive-kiosk    (X + fullscreen Chromium)
```

### Why the embedded GRUB is minimal

`grub-mkstandalone` produces a self-contained `BOOTX64.EFI`, but rewriting it is
awkward. So the embedded config does only two things: find the partition by its
`PENSYS` label and `configfile` into the real `grub.cfg`. All the menu logic
lives in an ordinary file on an ext4 partition — updating the menu is
overwriting a file, not rebuilding the EFI binary.

### The boot watchdog (what stops the stick bricking itself)

GRUB has no increment operator and no `rm`. That shaped two things:

1. The `boot_attempts` counter rises through an explicit `if/elif` chain in
   `grub.cfg`, not `+= 1`. On reaching 3, the pending boot is ignored and the
   manager starts normally. A bad `cmdline` from an adapter cannot trap the
   user in a boot loop.
2. "Cancel pending boot" from GRUB **does not delete** the file — GRUB cannot.
   It boots the manager with `penlive.clear_pending=1`, and the daemon deletes
   it once Linux is running.

`recovery.cfg` is sourced unconditionally **at the end** of `grub.cfg`, after
all the pending-boot logic, precisely so it stays reachable if anything before
it fails.

## Partitions

| # | Label | FS | Size | Contents |
|---|---|---|---|---|
| 1 | `PENEFI` | FAT32 | 512 M | `EFI/BOOT/BOOTX64.EFI` |
| 2 | `PENSYS` | ext4 | 4 G | `live/` + `boot/state/` + `boot/extracted/` |
| 3 | `persistence` | ext4 | 8 G | live-boot's OverlayFS |
| 4 | `PENDATA` | exFAT | rest | `images/`, `catalog/`, `logs/` |

Four decisions worth explaining:

**`boot/state` lives on PENSYS (ext4), not PENDATA (exFAT).** GRUB reads
`nextboot.cfg` straight off the raw partition, before Linux or OverlayFS
exist. GRUB's ext4 support is far better tested than its exfat support, and the
entire boot chain is not worth risking on that.

**PENDATA is exFAT anyway.** It is the partition a user sees when plugging the
stick into any Windows/macOS/Linux machine to copy an ISO across by hand.
Nothing GRUB must read at boot lives there — except in the `chainload` method,
which is exactly why that is the only one doing `insmod exfat`.

**ISOs ≠ persistence.** Downloads go to `PENDATA`, system state to
`persistence`. A factory reset (reformat `persistence`, recreate
`persistence.conf`) preserves every download. If ISOs lived in the overlay,
resetting the system would cost tens of GB of re-downloading.

**The `persistence` label is not ours to choose.** Debian live-boot scans for a
partition labelled exactly `persistence` containing `persistence.conf`. That
name is live-boot's contract; renaming it silently breaks the overlay, which is
why it kept its name when everything else was renamed to PenLive.

## Privilege separation

```
Chromium kiosk  (penlive user)
      │ HTTP localhost:7777
penlive-api     (penlive user)  ← parses untrusted input
      │ Unix socket, JSON per line
penlive-daemon  (root)          ← fixed command list
```

The API is what processes untrusted input: remote catalog JSON and the internal
contents of downloaded ISOs. That is why it does **not** run as root. Anything
needing privilege goes over a Unix socket to a daemon whose entire surface is
the list in `app/daemon/protocol.py`:

```
ping · mount_image · umount · write_nextboot · clear_nextboot
reboot · poweroff · kexec_boot · write_usb
job_start · job_status · job_list · job_cancel
run_operation · list_operations · set_keyboard
```

The daemon never receives a shell command; it receives a name from that list
and named arguments. There is no "execute this string" path. Validation happens
on both sides — the client refuses a command outside the list before even
opening the socket, and the server refuses again on receipt. A test guarantees
that `ALLOWED_COMMANDS` and the implemented handlers cannot drift apart.

`write_usb` reuses the guards in `builder/penlive/safety.py` rather than
reimplementing them: it refuses partition names and refuses the disk the
running system booted from.

The same principle covers the Tools: `job_start` receives the *name* of an
operation plus structured arguments, and the daemon builds the argv itself in
`daemon/operations.py`. Adding a plugin does not widen what the system can do.
Details and the threat model are in [PLUGINS.md](PLUGINS.md).

## Kiosk: why the lockdown has three layers

Locking the browser is not enough — a user leaves the application through paths
the browser never sees:

| Escape | Where it is closed |
|---|---|
| Ctrl+Alt+F2 (virtual terminal) | `getty@ttyN` masked + `NAutoVTs=0` in logind |
| Ctrl+Alt+Del | `ctrl-alt-del.target` masked |
| Ctrl+Alt+Backspace | `DontZap` in Xorg |
| Alt+F4, Alt+Tab, right-click menu | `openbox-rc.xml` with empty `<keyboard>` and `<mouse>` |
| Ctrl+R, F12, Ctrl+O, dropping a file | `src/kiosk.js` inside the app |
| Closing Chromium somehow | a `while true` loop in `xsession.sh` reopens it |

The browser layer is the last one, not the only one: Chromium never receives
Alt+F4, because the window manager consumes it first. And `kiosk.js` disables
itself on the dev server — locking out reload and devtools would make the UI
impossible to work on.

## First run

`setup.state()` decides between the wizard and the catalog:

```
never configured             → wizard (reason="first_run")
configured, but offline      → wizard (reason="offline")
configured and online        → straight to the catalog
```

Keyboard comes before network in the wizard on purpose: the next screen asks
for a Wi-Fi password, and typing one with the wrong layout is a confusing way
to fail. The test field exists because it is the only way for the user to
discover the problem beforehand.

"Skip for now" records `setup_completed` just the same: someone on an Ethernet
cable, or who only wants to boot an ISO already on the stick, must not be
trapped on a Wi-Fi screen every boot.

## Boot adapters

The mistake that breaks most "GRUB that boots any ISO" attempts is assuming a
universal method exists. It does not: every distribution puts its kernel and
initrd somewhere different and expects different parameters to find its own
root filesystem.

The abstraction here:

```python
detect(iso)  -> int   # confidence 0-100
prepare(iso, extract_dir, iso_rel_path) -> BootConfig
```

`detect()` returns **confidence, not a boolean**. An Ubuntu ISO has `/casper/`
and also `/EFI/BOOT/BOOTX64.EFI`, so the generic adapter would match too. With
scoring, the registry picks the best (Ubuntu 95 > generic 10) instead of the
first one to answer "maybe". A test covers exactly that conflict.

Two output methods:

- **`linux`** — extracts kernel/initrd from the ISO into
  `PENSYS/boot/extracted/<id>/` and builds a `menuentry` that hands the
  original ISO to the target OS as its root via `findiso=`,
  `iso-scan/filename=`, `inst.stage2=` and so on. Far more predictable than
  chainloading, because it does not depend on the ISO's own bootloader.
- **`chainload`** — for hybrid ISOs with no dedicated adapter: `loopback` plus
  `chainloader` into the ISO's own `BOOTX64.EFI`. Extracts nothing.

Inspection uses **pycdlib**, not a loop mount: reading ISO9660 in user space
needs no root. Root only appears much later, behind the "Mount" button.

> The Arch and Proxmox `cmdline` values change between releases. They are
> marked in the code as a starting point to verify against the specific ISO,
> not as a guarantee.

## Downloads

`aria2c` runs as **its own systemd service**, not as a child of the API. A 3 GB
download has to survive an API restart; at startup,
`downloader.resume_watchers()` reattaches to the GIDs still running. The
partial file and its `.aria2` control file live on `PENDATA`, which is
persisted — so resuming after powering the machine off genuinely works.

The ordering that makes "it exists in `images/`" mean "it is trustworthy":

```
aria2 → images/.downloads/x.iso.part → SHA-256 → atomic rename → images/x.iso
```

Verification failed → file deleted, status `corrupted`. The `rename()` only
happens after the hash matches, so nothing half-downloaded or tampered with
ever appears ready to boot.

## Catalog

`catalog.json` points straight at each distribution's official servers — the
project hosts no ISOs at all. Fallback order: remote → cache on `PENDATA` →
the copy bundled in the squashfs. Offline, the UI still shows the catalog.

Maintaining hashes by hand is how this kind of catalog rots: the distribution
ships a point release, the URL starts serving a different file, and every
download fails verification. `tools/update_catalog.py` fetches each vendor's
official checksum file and rewrites the entries. It understands the three
formats vendors actually use (`hash  file`, the same with a `*` binary marker,
and Fedora's BSD-style `SHA256 (file) = hash` inside a PGP-signed block).

Entries whose URL is a rolling alias — Arch's `latest/archlinux-x86_64.iso` —
also carry a `version_pattern`, because otherwise the hash gets corrected on
every run while the version shown in the UI silently rots.

## Development mode

`app/paths.py` decides at import time whether it is on the live system. Off it
(or with `PENLIVE_DEV=1`), everything points at `./devdata/` and privileged
operations return `503` instead of blowing up. That is what makes it possible
to develop the entire UI on Windows/macOS with no stick and no root.

In production `ensure_dirs()` deliberately does **not** create `/data` and
`/boot`: if the partition failed to mount, creating the directory would hide
the failure by writing into the overlay. Better to let it be missing and report
it.

`PENLIVE_OFFLINE=1` additionally skips the startup network probes (catalog
fetch, aria2 RPC). The test suite sets it so tests neither depend on internet
access nor pay a network round-trip per app instance.

## Building under WSL

WSL appends the entire Windows PATH after the Linux one, so inside Debian
`command -v npm` can resolve to `C:\Program Files\nodejs\npm`. A dependency
check that accepts it passes, Node is never installed in the distribution, and
the build later fails inside CMD.EXE complaining that UNC paths are
unsupported. `scripts/make-usb.sh` therefore strips `/mnt/` entries from PATH
under WSL and treats any command resolving under `/mnt/` as absent.

The build is also copied into the distribution's own filesystem first, never
built on `/mnt/c` or `/mnt/d`: debootstrap needs device nodes and real
ownership, which DrvFs cannot represent.

## What was not done, and why

In the order it makes sense to tackle:

1. **Windows / wimboot** — not "another adapter": a completely different boot
   method (WIM/WinPE, BCD, `boot.sdi`). It comes after the adapter abstraction
   has been validated on hardware with the Linux distributions.
2. **A/B updates of the manager** — `system-a.squashfs` / `system-b.squashfs`
   with GRUB rollback. The partition layout already reserves room for it.
3. **Secure Boot** — needs a Microsoft-signed shim and a signing chain in the
   builder; it multiplies build and update complexity.
4. **Legacy BIOS** — only if a real need appears. Supporting UEFI x86-64 alone
   eliminates an enormous number of special cases.
