# PenLive

A USB stick that **is** an operating-system manager: power on the machine,
connect to Wi-Fi, pick a system from a catalog, download it, verify it and boot
it — with no second computer, and without reflashing the stick for every ISO.

```
UEFI → GRUB → Debian Live (SquashFS + OverlayFS) → FastAPI + Chromium kiosk
                                                        ↓
                                          catalog · download · boot · VM
```

## What is implemented

| Layer | State |
|---|---|
| `penlive` CLI (GPT partitioning, formatting, GRUB install, provisioning) | done, with `--dry-run` |
| GRUB: boot manager, pending boot, 3-attempt watchdog, recovery | done |
| Debian `live-build` (packages, hooks, systemd, kiosk) | done |
| FastAPI + SQLite backend | done |
| Privileged daemon (Unix socket, fixed command set) | done |
| Resumable downloads via aria2 + SHA-256 verification | done |
| Adapters: Debian, Ubuntu, Fedora, Arch, Proxmox, generic EFI | done |
| React kiosk frontend | done |
| Native boot, Run VM (QEMU/KVM), Mount, Write-to-USB | done |
| OS-style status bar: IP, keyboard, storage, clock | done |
| First-run setup wizard (keyboard + network) | done |
| Three-layer kiosk lockdown (VT, WM, browser) | done |
| 8 tools: backup, restore, SMART, repair, recovery, provisioning | done |
| Windows / wimboot, A/B updates, Secure Boot, legacy BIOS | **not implemented** — see [Scope](#scope-and-limits) |

## Quick start (development)

Runs on any OS — no USB stick, no root. The backend detects that it is not on
the live system and uses `./devdata/` instead of the real partitions.

```bash
make backend-deps
```

```bash
cd manager/frontend && npm ci && npm run build
```

```bash
cd manager/backend && PENLIVE_DEV=1 python -m uvicorn app.main:app --port 7777
```

Open <http://127.0.0.1:7777>. The catalog loads from the local copy in
`catalog/catalog.json`; Wi-Fi, boot, mount and the tools return `503` because
they depend on the live system — the interface handles that and stays usable,
naming the reason in each case.

## Interface

```
┌──────────────────────────────────────────────────────────────┐
│ ▣ PenLive   ● HomeWifi 192.168.1.42  ⌨ BR  ▤ 127 GB  09:41 ⏻│
├──────────────────────────────────────────────────────────────┤
│  Systems  │  Tools  │  Settings                              │
└──────────────────────────────────────────────────────────────┘
```

A status bar is always visible with connection state, IP address, keyboard
layout, free space, clock and shutdown. Three tabs: **Systems** (catalog,
download, boot, VM, mount), **Tools** (the 8 tools) and **Settings** (network,
keyboard, system information).

The first run shows a two-step wizard — keyboard, then network. After that,
booting goes straight to the catalog, and the wizard only returns when the
machine has no connection.

## Tools

Backup · Restore · SMART · Filesystem Check & Repair · Linux Boot Repair ·
Windows Repair · Deleted File Recovery · Provision Machine

All driven from the interface, with a live output console and cancellation.
Destructive actions require explicit confirmation, and the stick's own
partitions are marked `⚠ PenLive`.

A plugin **cannot** widen what the system is able to do: it produces the *name*
of an operation plus structured arguments, and the daemon is what builds the
command line. See [docs/PLUGINS.md](docs/PLUGINS.md).

## Tests

```bash
make test
```

182 tests: 28 in the builder (partitioning, safety guards, provisioning plans,
the image path) and 154 in the backend (adapters against synthetic ISOs, GRUB
menuentry generation, SHA-256 verification, the daemon protocol, adversarial
validation of privileged arguments, the job runner, disk inventory, the HTTP
API and the catalog schema).

## Building a real stick

Needs a **Debian/Ubuntu** host and root. One guided command handles everything —
dependencies, the interface, the live system, device selection and writing:

```bash
sudo ./scripts/make-usb.sh
```

It asks before every irreversible step, skips whatever is already built (the
live system takes 20-40 minutes and does not need repeating to reflash a
stick), and adjusts the partition sizes by itself on small sticks. At the end
it validates the result and explains how to boot it.

Reflashing a second stick, reusing the existing build:

```bash
sudo ./scripts/make-usb.sh --device /dev/sdb --skip-build
```

**On Windows**, from an elevated PowerShell — it also does everything: installs
WSL/Debian if missing, builds inside it, and writes the stick.

```powershell
.\scripts\make-usb.ps1
```

Where `wsl --mount --bare` is available the raw stick is handed to WSL and the
same Linux installer runs against it, using the whole device. Otherwise it
falls back to building an image and writing that from Windows.

> Writing erases the entire disk. The script always requires you to type the
> device path to confirm — not even `--yes` skips that — and refuses to write
> to the disk the running system booted from.

The manual steps, and how to test in QEMU before touching real hardware, are in
[docs/BUILD.md](docs/BUILD.md).

## Stick layout

```
GPT
├── p1  PENEFI       FAT32   512M   EFI/BOOT/BOOTX64.EFI
├── p2  PENSYS       ext4    4G     live/ (kernel, initrd, squashfs) + boot/state + boot/extracted
├── p3  persistence  ext4    8G     OverlayFS (live-boot) — settings, Wi-Fi
└── p4  PENDATA      exFAT   rest   images/ (ISOs), catalog/, logs/
```

ISOs live on `PENDATA`, **separate** from persistence: a factory reset erases
no downloads. `PENDATA` is exFAT so the stick can be plugged into
Windows/macOS to copy ISOs onto it by hand.

The `persistence` label is not ours to choose — Debian live-boot scans for a
partition with exactly that name.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — design decisions and the reasoning behind each
- [docs/BUILD.md](docs/BUILD.md) — building, writing and testing in QEMU
- [docs/ADAPTERS.md](docs/ADAPTERS.md) — how boot adapters work and how to write one
- [docs/PLUGINS.md](docs/PLUGINS.md) — the tools, the security model, and how to add one

## Scope and limits

**Verified in this implementation:** the automated tests, the frontend build,
the API running end to end with the interface driven in a browser (wizard,
catalog, tools, the backup form and a streaming job console), the builder's
command plan under `--dry-run`, and the catalog checksums re-derived from each
distribution's own published checksum file.

**Not verified:** nothing has been tested on real hardware or UEFI. Native
boot, OverlayFS persistence, the Chromium kiosk, the virtual-terminal lockdown
and the adapter `cmdline` values all need validation in QEMU or on hardware
before any serious use — start with [docs/BUILD.md](docs/BUILD.md), section
"Testing in QEMU".

The privileged tools were exercised against a simulated daemon (argument
validation and the job runner have their own tests), but none has run against
real disks. Before trusting Backup/Restore/Provisioning, try them on
disposable disks inside a VM.

**Out of scope for now** (a deliberate decision, in the suggested order):
Windows via wimboot, A/B updates of the manager itself, Secure Boot, and
legacy BIOS.
