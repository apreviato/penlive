# Building, writing and testing

## Requirements

A **Debian 13 / Ubuntu 24.04+** host, with root. `live-build` only runs on
Debian or a derivative — on any other OS, use a Debian VM or container.

```bash
sudo apt install live-build grub-efi-amd64-bin grub-common gdisk dosfstools exfatprogs e2fsprogs zstd qemu-system-x86 ovmf nodejs npm python3 rsync
```

## Fast path: the guided script

Runs every step in this document in one sequence, asking before each
irreversible stage:

```bash
sudo ./scripts/make-usb.sh
```

What it handles beyond chaining commands:

- **Dependencies** — detects what is missing and offers to install it with apt.
- **Work already done** — reuses an existing frontend and live system instead
  of rebuilding (live-build takes 20-40 minutes).
- **npm as the invoking user** — runs `npm ci` under `SUDO_USER`, so a
  root-owned `node_modules` is not left in your repository.
- **Small sticks** — a "16 GB" stick holds about 14.9 GiB and does not fit the
  default layout, which needs 16.5 GiB. The script shrinks the persistence
  partition automatically instead of failing with a raw error partway through.
  Below roughly 13 GiB it refuses, with an explanation.
- **Target selection** — lists disks marking the system disk and non-removable
  drives, and unmounts mounted partitions before writing.
- **Confirmation** — requires typing the device path. Not even `--yes` skips it.
- **Progress** — the live-build stage prints a status line on each phase and
  every 15 seconds, so a long silence (squashfs compression emits nothing for
  minutes) is distinguishable from a hang.

Useful options:

```bash
sudo ./scripts/make-usb.sh --device /dev/sdb --skip-build
```

```bash
sudo ./scripts/make-usb.sh --data-fs ext4 --persist-mib 16384
```

Full log in `make-usb.log`.

## On Windows

One command does everything — installs what is missing, builds, and writes:

```powershell
.\scripts\make-usb.ps1
```

Needs PowerShell **as Administrator**. If there is no WSL distribution from the
Debian family, it offers to install one (`wsl --install -d Debian --no-launch`);
then it installs the build dependencies, builds the interface and the live
system, and writes the stick.

`live-build` only runs on Debian, so the Linux half happens inside WSL. Two
routes, tried in this order:

**1. Disk passthrough (preferred).** `wsl --mount --bare` hands the raw stick to
WSL, and the same `make-usb.sh` that Linux users run partitions and writes it.
It uses the **whole** stick, and there is a single tested code path rather than
a Windows reimplementation.

**2. Image (fallback).** Where passthrough is unavailable, WSL produces a
fixed-size `.img` and PowerShell writes it from Windows. Simpler, but the data
partition is capped at the image size instead of filling the stick.

The build happens inside the WSL filesystem, never on `/mnt/d`: `debootstrap`
needs device nodes and real ownership, which DrvFs cannot represent, so a
chroot built there breaks partway through.

Writing an image that already exists, building nothing:

```powershell
.\scripts\make-usb.ps1 -Image .\dist\penlive-amd64.img.zst
```

The same guarantees as the Linux script apply: the system disk and
non-removable drives are refused, and writing requires typing the disk number.
On the image route, the disk is read back and compared byte for byte
(`-NoVerify` skips that).

`.img.zst` files need `zstd.exe` on PATH
(`winget install Facebook.Zstandard`).

> The `docker-desktop` distribution that Docker Desktop creates is **not**
> usable as a build environment — it has neither apt nor systemd — and the
> script skips it explicitly rather than failing 30 minutes later.

> **WSL interop.** WSL appends the entire Windows PATH to the Linux one, so
> `command -v npm` inside Debian can find `C:\Program Files\nodejs\npm`. The
> dependency check would pass, Node would never be installed in Debian, and the
> build would break much later inside `CMD.EXE` complaining that UNC paths are
> unsupported. The script strips `/mnt/` entries from PATH and treats any
> binary resolving under `/mnt/` as absent.

> If WSL has only just been enabled, Windows needs a reboot before
> distributions can be installed. The script detects that and says so, rather
> than failing obscurely.

The rest of this document describes the same steps manually, which is useful
for debugging one stage in isolation.

## 1. Frontend

Required before the live build: the API serves `dist/` as static files, so
without it the live system comes up with no interface.

```bash
cd manager/frontend && npm ci && npm run build
```

Vite 8 needs Node `^20.19` or `>=22.12`. Debian trixie ships 20.19.2, which
just qualifies; an older base fails deep inside the bundler with an error that
never mentions Node.

## 2. Live system

```bash
sudo ./live/build.sh
```

Takes a while (it downloads an entire Debian) and produces:

```
live/build/out/vmlinuz
live/build/out/initrd.img
live/build/out/filesystem.squashfs
```

The script copies the backend, builder, catalog, systemd units and the
frontend's `dist/` into the chroot, and the `0200-install-manager` hook creates
the venv with dependencies already resolved — the live system has to work with
no network.

## 3a. Writing straight to a stick

```bash
sudo PYTHONPATH=builder python -m penlive.cli devices
```

**Always run the dry run first.** It prints the exact command sequence without
executing anything:

```bash
sudo PYTHONPATH=builder python -m penlive.cli install /dev/sdb --dry-run --live-dir live/build/out
```

Once the plan looks right:

```bash
sudo PYTHONPATH=builder python -m penlive.cli install /dev/sdb --live-dir live/build/out
```

It requires you to type `/dev/sdb` to confirm. The guards refuse partition
names (`/dev/sdb1`) and the disk the running system booted from.

Useful options: `--data-fs ext4` (if the stick will never see Windows),
`--persist-mib`, `--system-mib`, `--log audit.txt`.

Verify afterwards:

```bash
sudo PYTHONPATH=builder python -m penlive.cli validate /dev/sdb
```

## 3b. Producing a distributable image

Touches no physical disk — it does everything on a loop device:

```bash
sudo PYTHONPATH=builder python -m penlive.cli image dist/penlive-amd64.img \
    --size-mib 20480 --live-dir live/build/out --compress
```

Result: `dist/penlive-amd64.img.zst`, which an end user writes with Rufus,
balenaEtcher or `dd`.

`--size-mib` must exceed the fixed partitions (12800 MiB) plus a 4096 MiB
minimum data partition. 16384 looks like a natural default but is rejected for
leaving only 3584 MiB.

## 4. Testing in QEMU (do this before hardware)

```bash
sudo qemu-system-x86_64 -enable-kvm -m 4096 -smp 2 \
    -bios /usr/share/ovmf/OVMF.fd \
    -drive file=/dev/sdb,format=raw,if=virtio \
    -netdev user,id=n0 -device virtio-net-pci,netdev=n0
```

Or against the image, with no stick:

```bash
sudo qemu-system-x86_64 -enable-kvm -m 4096 -smp 2 \
    -bios /usr/share/ovmf/OVMF.fd \
    -drive file=dist/penlive-amd64.img,format=raw,if=virtio
```

Minimum validation script:

1. GRUB appears and "PenLive Manager" boots.
2. Chromium comes up fullscreen on the network screen (no desktop visible).
3. Connect to Wi-Fi (or use QEMU's network) and reach the catalog.
4. Download an ISO; kill the VM at roughly 50% and restart — the download
   should resume.
5. SHA-256 verification passes and the status becomes `ready`.
6. "Boot" schedules the pending boot; restarting should land in the installer.
7. Restart 3 more times with a broken pending boot — the watchdog should give
   up and return to the manager.
8. Persistence: restart and confirm Wi-Fi reconnects by itself.

## Catalog maintenance

```bash
python tools/update_catalog.py
```

Exits 1 if any hash, size or version differs from what the vendor publishes.
A good candidate for a weekly CI job.

```bash
python tools/update_catalog.py --write
```

## Secure Boot

PenLive boots with Secure Boot **enabled**. The stick ships the standard Debian
chain, so no key enrolment and no firmware changes are needed:

```
firmware  ->  shimx64.efi   signed by Microsoft
          ->  grubx64.efi   signed by Debian, verified by shim
          ->  vmlinuz       signed by Debian, verified through shim
```

`live/build.sh` uses Debian's stock kernel, which is already signed, so all
three links are covered. Verified under OVMF with Microsoft's keys enrolled -
the same firmware that rejects an unsigned build reaches the kiosk.

The builder picks this chain automatically whenever `shim-signed` and
`grub-efi-amd64-signed` are installed on the build host, and falls back to an
unsigned `grub-mkstandalone` binary when they are not. The guided scripts list
both as dependencies, so a normal build gets the signed chain.

### What still needs Secure Boot off

Booting a *downloaded* system is a different matter, because shim only trusts
Debian's and Microsoft's keys:

| Action | Secure Boot on |
|---|---|
| Booting PenLive itself | works |
| Running an ISO in a VM (Run VM) | works - QEMU does not involve firmware |
| Mount, backup, repair, provisioning tools | work |
| Booting a Debian or Ubuntu ISO by chainloading it | usually works: their own bootloader is signed too |
| Booting an ISO via the extracted kernel (`linux` method) | fails unless that kernel is Debian-signed |

The `linux` method hands GRUB a kernel taken out of the ISO. Ubuntu's kernels
are signed by Canonical and Fedora's by Red Hat, and shim trusts neither, so
GRUB refuses to start them.

**PenLive solves this with a machine owner key.** In Settings there is a Secure
Boot panel: enrol a key once, and every kernel PenLive extracts afterwards is
counter-signed with it. `sbsign` appends a signature rather than replacing one,
so the distribution's own signature stays intact - what is added is "the owner
of this machine also vouches for this file", which is exactly what a MOK means.

Enrolment takes one reboot:

1. Settings -> Secure Boot -> **Enrol a key for this machine**.
2. Write down the 8-digit code it shows. It is digits only because MokManager
   runs before any keymap is loaded and reads a bare US layout, so letters
   could be untypeable on the very screen that demands them.
3. Reboot. A blue MokManager screen appears: **Enroll MOK** -> Continue -> Yes,
   then type the code.
4. It reboots again, and downloaded systems now boot with Secure Boot on.

Until a key is enrolled, PenLive refuses to schedule such a boot with a clear
message rather than letting the firmware fail silently after a reboot.

One more limitation: Debian's signed GRUB has no `exfat` module, so
chainloading an ISO stored on the exFAT `PENDATA` partition fails under Secure
Boot. Build with `--data-fs ext4` if you need that combination and can give up
reading the stick on Windows.

So: install systems with Secure Boot off, then leave it on for everyday use.

## Common problems

**GRUB does not appear / the machine ignores the stick** — with a current
build this should not happen, because the stick ships a signed boot chain (see
"Secure Boot" above). It looks like the firmware simply skips the stick: you
select it in the boot menu and the machine carries on without a word.

If it does happen, the build fell back to the unsigned bootloader because
`shim-signed` and `grub-efi-amd64-signed` were missing on the build host.
Under OVMF with Microsoft's keys enrolled that produces:

```
BdsDxe: loading Boot0001 "UEFI Misc Device" from PciRoot(0x0)/Pci(0x3,0x0)
BdsDxe: failed to load Boot0001 "UEFI Misc Device" ... : Access Denied
BdsDxe: No bootable option or device was found.
```

Install the two packages and rebuild:

```bash
sudo apt install shim-signed grub-efi-amd64-signed
```

Check what a stick actually carries — a signed one has `grubx64.efi` beside
`BOOTX64.EFI`:

```bash
sudo mount /dev/sdb1 /mnt && ls -R /mnt/EFI && sudo umount /mnt
```

Otherwise, to turn Secure Boot off, in firmware setup (usually F2, Del or F10
at power-on):

1. Find **Secure Boot** — often under Security, Boot, or Authentication.
2. Set it to **Disabled**. Some firmware requires setting an administrator
   password first, or switching *OS Type* from "Windows UEFI" to "Other OS".
3. While there, confirm **UEFI** boot is enabled and CSM/Legacy is off — a
   GPT/ESP stick is invisible to a machine booting in legacy mode.
4. Save and exit, then pick the USB device from the boot menu (F12, F10, Esc
   or F9 depending on vendor).

**The stick boots in QEMU but not on hardware** — the image is fine; the
difference is firmware policy. Check Secure Boot and CSM as above. To confirm
the image independently:

```bash
sudo qemu-system-x86_64 -machine q35 -m 2048 \
    -drive if=pflash,format=raw,unit=0,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
    -drive if=pflash,format=raw,unit=1,file=/tmp/OVMF_VARS.fd \
    -drive file=dist/penlive-amd64.img,format=raw,if=virtio
```

(copy `/usr/share/OVMF/OVMF_VARS_4M.fd` to `/tmp/OVMF_VARS.fd` first — pflash
needs a writable file).

**GRUB opens but cannot find the kernel** — the `PENSYS` label did not match.
`sudo blkid /dev/sdb2` should show `LABEL="PENSYS"`.

**Comes up in text mode, no Chromium** — check `journalctl -u penlive-kiosk`
and `journalctl -u penlive-api`. Almost always a missing
`manager/frontend/dist` at build time.

**A downloaded system does not boot** — almost always the adapter `cmdline`.
Use the "Ignore pending boot" entry in the Recovery menu, and compare the
generated `cmdline` against the `/boot/grub/grub.cfg` inside that ISO.

**The build succeeds but artifact collection fails** — live-build emits both
`vmlinuz` and `vmlinuz-<version>` as hardlinks. `live/build.sh` prefers the
unversioned name and refuses to guess between multiple kernel flavours.
