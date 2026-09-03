# Architecture

This document records **why** each decision was made. The code says what it
does; here is the reasoning that does not survive in comments.

## Boot chain

```
UEFI
 └─ EFI/BOOT/BOOTX64.EFI          (shim, Microsoft-signed, on PENEFI)
     └─ EFI/BOOT/grubx64.efi      (GRUB, Debian-signed, verified by shim)
         └─ EFI/debian/grub.cfg   (stub: find PENSYS, hand off)
             └─ configfile → PENSYS/grub/grub.cfg
         ├─ menuentry "PenLive Manager"          (default)
         ├─ source state/nextboot.cfg            (if a system is scheduled)
         └─ source grub/recovery.cfg             (always)
             └─ vmlinuz + initrd → live-boot
                 ├─ PENSYS/live/filesystem.squashfs   (read-only)
                 └─ LABEL=persistence partition       (OverlayFS, writable)
                     └─ systemd
                         ├─ NetworkManager
                         ├─ penlive-storage  (root, one-shot permissions)
                         ├─ penlive-daemon   (root, Unix socket)
                         ├─ penlive-aria2    (penlive user)
                         ├─ penlive-api      (penlive user, :7777)
                         └─ penlive-kiosk    (X + fullscreen Chromium)
```

### Why the config on the ESP is minimal

Whichever chain is used, the config on the ESP does only two things: find the
partition by its `PENSYS` label and `configfile` into the real `grub.cfg`. All
the menu logic lives in an ordinary file on an ext4 partition — updating the
menu is overwriting a file, not rebuilding or resigning an EFI binary.

### One layout, two readers

PENSYS is mounted on `/boot`, so the partition root **is** `/boot` to the
running system: `grub/`, `state/`, `extracted/`, `live/` and `wimboot` sit
directly there and nowhere else. What GRUB reads as `($root)/state/nextboot.cfg`
is what the manager writes as `/boot/state/nextboot.cfg`.

This is worth stating because getting it wrong is silent. An extra `boot/`
level on the partition means the manager writes `PENSYS/state/nextboot.cfg`
while GRUB tests `PENSYS/boot/state/nextboot.cfg`, and the `if [ -f ... ]`
guard simply does not fire: the scheduled system never appears in the menu, no
error is produced anywhere, and the same split silently disables `grubenv` and
`wimboot` too. `builder/tests/test_kiosk_boot.py` asserts that neither config
contains `($root)/boot/`.

### Restart answers before it reboots

`handle_reboot` arms the boot entry, flushes PENSYS — small, fast, and the one
thing the restart depends on — and then answers. The rest, flushing PENDATA and
releasing PenLive's own mounts, happens in a background task before
`systemctl reboot`.

Doing that work inside the request was a real bug with a confusing shape.
Syncing exFAT on a USB stick with a download's worth of dirty pages routinely
takes longer than the UI's 20-second request timeout, so the Restarting screen
gave up, reported a timeout, and dropped the user back into the app — which
then rebooted underneath them a minute later. It looked intermittent because it
depended entirely on how much was dirty. The reply is the promise that the
machine is going down; only the part that can still be *reported* as a failure
belongs in front of it.

The flush stays before `systemctl` rather than being left to shutdown, because
that is what keeps the blank screen at the end short. After 90 seconds the
Restarting screen says so and offers a way back, so a genuine hang is not
indistinguishable from a slow stick.

### Scheduled boot: once, and only next time

Pressing Boot must mean "this system starts on the next restart", not "this
system starts from now on". Two independent mechanisms make that true, because
each covers a case the other cannot:

1. **GRUB consumes the selection.** `grub.cfg` reads `next_entry` out of
   `grubenv`, and the boot that takes the pending entry clears and saves it
   first. This is the case where PenLive never runs again in between — the user
   boots into Ubuntu, works, and restarts from inside Ubuntu. Nothing of ours
   is running to clean up, so GRUB has to do it itself.
2. **The manager deletes it on startup.** PenLive being up means the scheduled
   boot either already happened or was passed over, so `nextboot.cfg` is
   retired in the API lifespan (`services/bootmanager.clear_on_startup`). This
   covers a `save_env` that failed on unusual firmware, and it is why the
   selection can never accumulate across sessions. The file must predate the
   running kernel to count as spent — `penlive-api` restarts itself on failure,
   and wiping a boot the user scheduled a minute ago would be its own bug.

There is deliberately no retry counter. A one-shot cannot loop, so counting
attempts only added a way for the entry to disappear from the menu with an
explanation the user never saw. `set fallback=boot_manager` handles the
remaining case — an entry GRUB cannot load — by starting PenLive instead of
dropping to a GRUB prompt.

`recovery.cfg` is sourced unconditionally **at the end** of `grub.cfg`, after
all the pending-boot logic, precisely so it stays reachable if anything before
it fails.

### GRUB's modules live on PENSYS

`$prefix` is pinned to `($root)/grub` at the top of `grub.cfg`, before the
first `insmod`, and the builder copies `/usr/lib/grub/x86_64-efi/*.mod` there
alongside the config.

This is not housekeeping. Debian's signed `grubx64.efi` carries a fixed
built-in module set and loads anything else from `$prefix/x86_64-efi`, and
**`exfat` is not in that set**. Its prefix starts as `/EFI/debian` on the ESP,
where PenLive installs only the three-line stub, so `insmod exfat` looked in a
directory that does not exist. What the user saw was three unrelated-looking
messages in a row: `file exfat.mod not found`, then `no such device: PENDATA`
(GRUB cannot identify an exFAT partition by label without the driver), then
`no server is specified` from a GRUB that had fallen back to reading the path
as a network address. None of the three mention the actual problem.

Secure Boot still refuses to load unsigned modules, so this fixes the
`chainload` method for machines with Secure Boot **off**. With it on, an image
that can only be chainloaded genuinely cannot start from an exFAT partition,
and the pending-boot banner says exactly that rather than letting the user find
out from GRUB.

### The menu ships in the squashfs too

`prepare-storage.sh` reinstalls `grub.cfg` and `recovery.cfg` from
`/opt/penlive/grub/` on every boot. Boot-logic fixes therefore reach an
already-flashed stick with a squashfs update, instead of requiring the whole
device to be rewritten, and a stick built with the older nested layout is
lifted to the current one on its next start.

## Partitions

| # | Label | FS | Size | Contents |
|---|---|---|---|---|
| 1 | `PENEFI` | FAT32 | 512 M | `EFI/BOOT/BOOTX64.EFI` |
| 2 | `PENSYS` | ext4 | 4 G | `live/` + `grub/` + `state/` + `extracted/` + `wimboot` |
| 3 | `persistence` | ext4 | 8 G | live-boot's OverlayFS |
| 4 | `PENDATA` | exFAT | rest | `images/`, `windows/`, `catalog/`, `logs/`, `vm-sessions/` |

Four decisions worth explaining:

**`state/` lives on PENSYS (ext4), not PENDATA (exFAT).** GRUB reads
`nextboot.cfg` straight off the raw partition, before Linux or OverlayFS
exist. GRUB's ext4 support is far better tested than its exfat support, and the
entire boot chain is not worth risking on that.

**PENDATA is exFAT anyway.** It is the partition a user sees when plugging the
stick into any Windows/macOS/Linux machine to copy an ISO across by hand.
Nothing GRUB must read at boot lives there — except in the `chainload` method,
which is exactly why that is the only one doing `insmod exfat`. exFAT earns its
keep a second time for Windows: WinPE can read it, and an `install.wim` larger
than 4 GiB fits, which is why `windows/` holds the unpacked Setup media.

**ISOs ≠ persistence.** Downloads go to `PENDATA`, system state to
`persistence`. A factory reset (reformat `persistence`, recreate
`persistence.conf`) preserves every download. If ISOs lived in the overlay,
resetting the system would cost tens of GB of re-downloading.

**The `persistence` label is not ours to choose.** Debian live-boot scans for a
partition labelled exactly `persistence` containing `persistence.conf`. That
name is live-boot's contract; renaming it silently breaks the overlay, which is
why it kept its name when everything else was renamed to PenLive.

### Secure Boot

The ESP carries Debian's signed chain rather than a bootloader built here:
Microsoft-signed `shimx64.efi` as `BOOTX64.EFI`, Debian-signed `grubx64.efi`
beside it, and Debian's already-signed kernel. That is the only combination a
factory-configured machine will execute, and it works just as well with Secure
Boot off, so there is no reason to build the unsigned one when the pieces are
present. `grub-mkstandalone` remains the fallback for hosts without
`shim-signed` / `grub-efi-amd64-signed`.

Debian's signed GRUB has `/EFI/debian` baked in as its prefix and cannot be
told to look elsewhere without resigning it, so the stub config goes there and
hands off to the real menu on PENSYS - the same indirection the standalone
build uses, just at a fixed path.

Verifying the layout treats a *partial* chain as an error: a shim that cannot
find `grubx64.efi` is worse than no shim, because the firmware launches it and
then stops at a message the user cannot act on.

Booting *downloaded* systems needs one more step. shim trusts Debian's and
Microsoft's keys, so a kernel extracted from an Ubuntu or Fedora ISO - signed
by Canonical or Red Hat - is refused. Rather than telling the user to give up
on Secure Boot, PenLive uses the mechanism Secure Boot provides for exactly
this: a machine owner key, enrolled once through MokManager, then used to
counter-sign each extracted kernel.

None of that is presented as a decision. Pressing Boot creates the key, queues
it and signs the kernel; the confirmation dialog carries the digits MokManager
will ask for, and that is the user's whole involvement.

The pending-boot banner repeats it, and that repetition is the point. The
firmware enforces its rules after PenLive is gone: a user who restarts, meets
the blue MOK screen, does not know the code and presses Continue lands on
`error: bad shim signature` followed by `you need to load the kernel first` —
on a black screen, with nothing to work backwards from and no way to look the
answer up. Anything the firmware will still ask for has to be on the screen
they are looking at *before* they press Restart, not only in a dialog they have
already dismissed. Making it a
prerequisite - refusing the boot and pointing at a Settings panel - asked
someone to authorise a mechanism they have no basis to reason about, in order
to do the thing they had just asked for. The Settings panel remains as a status
readout and as the place to re-read the code, since MokManager prompts for it
at a screen that appears before PenLive is running.

`sbsign` appends rather than replaces, so the vendor's signature survives and
nothing is forged - the added claim is "the owner of this machine also vouches
for this file", which is what a MOK is for. The kernel being vouched for came
out of an ISO whose SHA-256 was checked against the vendor's own published
checksum, so the trust is not blind.

Enrolment deliberately stops at the firmware screen: MokManager demands
physical presence, and that is the property that makes the mechanism worth
anything. The API generates the confirmation code rather than letting the user
choose it, because that screen runs before any keymap is loaded and reliably
accepts only digits.

The boot route refuses to schedule a boot that would be rejected, instead of
writing a pending entry and letting the machine fail silently after a reboot -
the user would see a flash, land back in the manager via the watchdog, and
have nothing to go on.

Debian's signed GRUB also has no `exfat` module, so chainloading an ISO from
the exFAT PENDATA partition needs `--data-fs ext4`.

### Filling the stick

Partitioning a device directly uses all of it, but flashing a fixed-size image
does not: a 20 GiB image on a 57 GB stick strands 37 GB that should be holding
ISOs. Two mechanisms cover that.

The Windows writer sizes the image to the target device, which is only
affordable because it skips all-zero blocks — a 57 GiB image carries roughly
1.3 GiB of real data, so the write is minutes rather than half an hour. Zero
blocks are safe to skip: `Clear-Disk` has already removed the old partition
table, filesystem free space is tracked in metadata that *is* written, and the
GPT backup header at the very end is non-zero and therefore always written.
`-FullWrite` forces every byte for anyone who wants the old contents
overwritten.

`penlive-expand.service` is the backstop, for images flashed by Rufus,
balenaEtcher or `dd`. On boot it grows `PENDATA` to fill the device. Its
trigger is deliberately narrow — a GPT whose backup header is not at the end of
the device — which is true exactly once, right after flashing a
smaller-than-device image, and false forever after it runs. It finds its own
disk through the `PENSYS` label so it can never touch another drive, refuses if
`PENDATA` already holds files, and recreates rather than resizes the partition
because exfatprogs has no resize tool (acceptable only because `PENDATA` is
empty at that point).

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

The Terminal tab is intentionally different: it is a real interactive shell,
but it is spawned by the already unprivileged API as `penlive`, never by the
root daemon. It can work with PENDATA and user-writable mounted drives, while
the API service sandbox and ordinary Unix permissions still protect the live
system and privileged device operations.

## Kiosk: why the lockdown has three layers

Locking the browser is not enough — a user leaves the application through paths
the browser never sees:

| Escape | Where it is closed |
|---|---|
| Ctrl+Alt+F2 (virtual terminal) | `getty@ttyN` masked + `NAutoVTs=0` in logind |
| Ctrl+Alt+Del | `ctrl-alt-del.target` masked |
| Ctrl+Alt+Backspace | `DontZap` in Xorg |
| Alt+F4, Alt+Tab, right-click menu | `openbox-rc.xml`: empty `<mouse>`, and a no-op binding per escape key |
| Ctrl+J, Ctrl+H, F12, view-source | managed Chromium policy: `chrome://*`, `devtools://*` and `file://*` are blocked, leaving those keys nowhere to navigate |
| Ctrl+R, Ctrl+O, dropping a file | `src/kiosk.js` inside the app |
| Closing Chromium somehow | a `while true` loop in `xsession.sh` reopens it |
| Something ending up in front of the app | `keep_kiosk_in_front` in `xsession.sh` re-raises and re-focuses the kiosk window |
| Password-save/autofill prompts | managed Chromium policy |

The browser layer is the last one, not the only one: Chromium never receives
Alt+F4, because the window manager consumes it first. And `kiosk.js` disables
itself on the dev server — locking out reload and devtools would make the UI
impossible to work on.

The three keyboard layers have to be read together. Openbox grabs keys
*globally*, so anything it takes never reaches the page at all — which is why
Ctrl+C, Ctrl+D and Ctrl+L are deliberately left to the browser: the Terminal
tab binds them to interrupt, EOF and clear, and none of them can leave the app
(in kiosk mode Ctrl+L has no address bar to focus). Everything else that opens
a browser surface, navigates away, or exits fullscreen is grabbed.

Chromium runs with sign-in, sync, password storage, address/card autofill,
translation prompts and the default-browser prompts disabled by managed
policy. Wi-Fi credentials belong to NetworkManager, not to the browser
profile; the runtime profile also lives in `/run`, so nothing survives a
reboot. Incognito is disabled by policy rather than by `--incognito`: a second
window type in a kiosk with no way to switch windows is how the manager ends up
behind something.

## Kiosk: getting to the first frame

X starts before the API is listening, so the browser opens
`opt/penlive/loading.html` — a local splash that polls `/api/health` and
forwards to the manager. Three things about that handoff are load-bearing:

- **It waits for a manager it can actually show.** `/api/health` reports
  `frontend`, whether the built `dist/` is mounted. The splash gets exactly one
  navigation and nothing brings the kiosk back, so answering the port is not
  enough. Reading that body cross-origin is why the API allows the `null`
  origin a `file://` page sends.
- **`index.html` repeats the same splash inline.** The live system reads a
  440 kB bundle off a compressed squashfs on a USB stick; anything that waits
  for the script leaves that gap black, which reads as a crash. `App` removes it
  in a mount effect, once there is something to replace it with.
- **Nothing on the first-paint path may block.** Network status goes through the
  daemon, which handles one request at a time and shells out to `nmcli`. It is
  fetched without being awaited, cached for a few seconds so the status bar and
  `/api/setup/state` share one round trip, and every API call has a timeout.

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
  `PENSYS/extracted/<id>/` and builds a `menuentry` that hands the
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
aria2 also saves its session on `PENDATA` every 30 seconds. Transient network
failures retry indefinitely with a delay; they do not discard the partial ISO
or silently turn the UI back into a fresh Download button. If a permanent
source error occurs, the WebSocket sends aria2's error text to the visible UI.

RPC disappearing while systemd restarts aria2 no longer marks the transfer as
failed: the watcher waits and reconnects. On a new Download request, PenLive
attaches to an existing aria2 transfer for the same path. A complete orphan is
verified and adopted; an incomplete file with no `.aria2` control file is
removed because aria2 cannot reliably resume it, preventing a permanent
"file already exists" loop.

The ordering that makes "it exists in `images/`" mean "it is trustworthy":

```
aria2 → images/.downloads/x.iso.part → SHA-256 → atomic rename → images/x.iso
```

Verification failed → file deleted, status `corrupted`. The `rename()` only
happens after the hash matches, so nothing half-downloaded or tampered with
ever appears ready to boot.

## Files and locally copied ISOs

The Files tab exposes PENDATA, read-only mounted ISOs and filesystem partitions
reported by `lsblk`. Unmounted partitions are mounted by one fixed daemon
operation under `/run/penlive/drives`; removable mounts can be safely removed
again. Every requested path is resolved below its validated source root, so
traversal and absolute paths are rejected. Copy and Move work across sources,
while the catalog, logs, download staging area and top-level images directory
cannot be renamed or deleted.

ISOs copied manually into `/data/images` are scanned at API startup and on
demand. A filename matching a catalog entry is checked against that entry's
SHA-256 before it becomes verified. Other ISOs receive a stable local ID and
are inspected by the adapter registry, but remain visibly unverified and need
an extra confirmation before native boot. Missing local files are removed from
the database, so the catalog never retains a stale Boot button.

## Embedded virtual machines

QEMU runs as `penlive` and exposes VNC plus its WebSocket transport only on
loopback. noVNC is bundled into the frontend, so **Run VM** changes to the VM
tab instead of opening a GTK window behind fullscreen Chromium. The kiosk can
therefore display and control the guest without Alt+Tab or another desktop.
By default the VM receives the selected ISO only as a read-only CD-ROM: no
physical disk and no persistent virtual hard disk is attached, so an installer
cannot alter the notebook and live-session changes disappear when the VM
stops. An explicit **Enable drive access** flow is available for installation:
it lists whole disks, hides PenLive and read-only devices, requires the exact
device path to be typed back, and restarts QEMU with only that disk attached.
The root daemon independently revalidates the device, refuses PenLive labels,
active swap/storage layers and system mount points, safely unmounts ordinary
partitions, and grants the `penlive` user a lease-bound ACL. The ACL is removed
when QEMU stops or exits. On a UEFI-booted host this mode uses a private OVMF
variable store and a q35/SATA guest, with the installer CD first for one boot.
The notebook's real UEFI NVRAM is never shared, so the user may still need F12
to select the installed drive after installation.

The VM tab can expand over the whole kiosk; `Ctrl+Alt+F` or the toolbar
revealed at the top returns to the normal interface without restarting the
guest.

### Which drive is a whole drive

sysfs answers that, not `lsblk`: every block device has a directory under
`/sys/class/block`, and only a partition has a `partition` file in it. The
check used to be "lsblk printed exactly one device and its path string equals
ours", which conflated three different things — whether the device is a drive,
whether lsblk's view is current, and whether two tools spell the same path the
same way. Any disagreement came back as *could not confirm that /dev/sda is one
whole disk*, which named nothing the user could act on and was usually not
about the drive at all. lsblk is still what supplies labels, mount points and
stacked layers; it just no longer gets to decide what the device is.

Two more things about `lsblk` are worth knowing before reading its JSON:
boolean columns (`RM`, `RO`) are only real JSON booleans from util-linux 2.38
— before that they are the strings `"0"` and `"1"`, and `bool("0")` is `True`,
which marks every drive read-only and empties the picker that filters those
out. And `MOUNTPOINTS` (plural) arrived in 2.37, so the older singular column
is tried before concluding a drive cannot be inspected.

### Saved sessions: suspending a guest to the stick

QEMU already knows how to serialise a running machine — that is what live
migration is. Pointing a migration at a local file instead of another host
gives suspend-to-disk: RAM, CPU and device state become one stream on PENDATA,
and `-incoming` on an identically configured QEMU reads it back with the guest
carrying on mid-sentence. **Save & suspend** does that and shuts the VM down;
the session survives restarting the notebook, which is the entire point, since
a live environment with terminals open and work in progress otherwise dies with
the session.

Three consequences shape the implementation:

* **The destination must match the source.** `session.json` records memory, CPU
  count, KVM and the ISO, and resuming rebuilds that command line rather than
  honouring whatever the caller asked for. A stream loaded into a differently
  sized machine does not fail politely — it fails inside the kernel it just
  restored.
* **Block devices are re-opened, not restored.** For the read-only ISO that is
  exactly right, and the ISO's size is recorded so a re-download invalidates the
  session instead of resuming a guest against a disk that changed underneath it.
  For a real drive it is not right at all, so a VM holding one is refused: the
  drive can change while the guest sleeps, and it would wake up writing on top
  of a filesystem it believes it still owns.
* **It is minutes of writing.** Guest memory goes through `zstd` (falling back
  to `gzip`) and the request returns immediately, with progress polled from
  `GET /api/vm/sessions`. Free space is checked against guest RAM up front:
  filling PENDATA and failing at 90% costs the session, the space and the time.

Resuming consumes the session — but only once the guest is actually running, so
a resume that falls over leaves the stream to try again. An interrupted save
leaves a `.part` file that nothing can read, which the API sweeps at startup;
on a stick where space is why saves fail, leaving gigabytes of it behind makes
the next attempt fail too.

**A thawed guest has to be told to run.** QEMU restores the runstate the source
had, and the source is deliberately paused before being written out — so a
destination left to itself comes up fully loaded and *stopped*, showing the
frozen last frame and never moving again. From the outside that is
indistinguishable from a resume that did not work at all, which is exactly how
it was first reported. `finish_incoming` waits on `query-status` (defined at
every moment, where the incoming migration record is not yet populated in the
first instants) and then issues `cont`.

That wait runs in a background task, behind a `restoring` flag on the VM
status. Reading a compressed guest back off a USB stick is tens of seconds at
best, the display answers long before the machine inside it does, and a request
held open for it would time out. While the flag is set the VM tab says the
session is being read back, and noVNC keeps retrying instead of concluding
after five attempts that the machine has stopped.

### One machine at a time

`start()` stops whatever else is running first, and every lookup goes through
`_live()`, which forgets an entry whose process has exited.

Both halves were real failures. Starting a second VM used to leave the first
one running with nothing on screen pointing at it — invisible, holding its
display and its memory — and then refusing to start it again with *"a VM for X
is already running"* about a machine the user had no way to see or stop. The UI
can only ever show one machine, so more than one running is not a feature with
a missing interface; it is a leak.

The one thing that is *not* interrupted is a session being written out: a save
half-way through is a truncated stream and a lost session, so starting a
machine while one is saving is refused with a reason rather than silently
costing the user the save.

## Runtime storage ownership

PENSYS does not exist while the squashfs is built, so its `state/` and
`extracted/` directories originally arrived owned by root. The
`penlive-storage` one-shot runs after fstab/first-boot expansion and before the
daemon, aria2 and API, creating those runtime directories and granting the
unprivileged manager only the write access it needs. This allows adapter
extraction and native Boot without running ISO parsing as root.

PENSYS is four gigabytes, most of it the live squashfs, so `extracted/` holds
exactly one image: the one currently scheduled. Extraction happens when Boot is
pressed and prunes everything else first. Extracting for every download instead
filled the partition, and a full ext4 that then records a write error remounts
itself read-only — after which every subsequent Boot failed with a storage
error that had nothing to do with the ISO being booted. Post-download
inspection is therefore detection only: it parses the ISO directory and writes
nothing to PENSYS.

## Catalog

`catalog.json` points straight at each distribution's official servers — the
project hosts no ISOs at all. Fallback order: remote → cache on `PENDATA` →
the copy bundled in the squashfs. Offline, the UI still shows the catalog.

Not every image arrives over HTTP. Kali publishes its live build as a torrent
only, so a source URL ending in `.torrent` is handed to aria2 as BitTorrent
metadata rather than as a URL — passing the URL would make aria2 download the
`.torrent` as an ordinary file and spawn a second, separately identified
download for the contents, which the progress watcher would never see. The
SHA-256 check afterwards is the same one every other image gets.

Maintaining hashes by hand is how this kind of catalog rots: the distribution
ships a point release, the URL starts serving a different file, and every
download fails verification. `tools/update_catalog.py` fetches each vendor's
official checksum file and rewrites the entries. It understands the three
formats vendors actually use (`hash  file`, the same with a `*` binary marker,
and Fedora's BSD-style `SHA256 (file) = hash` inside a PGP-signed block).

Entries whose URL is a rolling alias — Arch's `latest/archlinux-x86_64.iso` —
also carry a `version_pattern`, because otherwise the hash gets corrected on
every run while the version shown in the UI silently rots.

Some of those aliases do not even keep the same file name: openSUSE serves
`...-Current.iso`, but its checksum file names the snapshot the alias currently
points at, so looking up the URL's own file name finds nothing and the entry
would report drift forever. Those carry a `checksum_filename_pattern` that
matches the real name instead.

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

1. **A/B updates of the manager** — `system-a.squashfs` / `system-b.squashfs`
   with GRUB rollback. The partition layout already reserves room for it.
2. **Legacy BIOS** — only if a real need appears. Supporting UEFI x86-64 alone
   eliminates an enormous number of special cases.

Windows *is* implemented now, through wimboot — a third boot method alongside
`linux` and `chainload`, described in [ADAPTERS.md](ADAPTERS.md#windows). It is
the least-proven part of the codebase: synthetic-ISO tests cover the file
selection and the generated menu entry, but nothing here has started a real
WinPE. Treat QEMU as the first real test.
