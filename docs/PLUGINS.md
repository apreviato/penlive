# Tools (plugins)

The Tools tab exposes ten capabilities for backup, diagnostics, repair,
recovery and provisioning. All of them run privileged work, so the interesting
part of this document is how that is kept safe.

## The security model

The whole plugin system is built so that **adding a plugin cannot widen what
the system is able to do**.

```
Tools UI            (browser, untrusted input)
   │  POST /api/tools/<id>/run  {form values}
   ▼
Plugin              (unprivileged API process)
   │  JobSpec(kind="backup_partition", args={...})
   ▼  Unix socket, JSON per line
Daemon              (root)
   │  operations.build_argv(kind, args)   ← validates, then builds the argv
   ▼
["partclone.extfs", "-c", "-s", "/dev/sda2", "-O", "/data/backups/x.pcl", ...]
```

A plugin never produces a command line. It produces an operation *name* plus
structured arguments. The daemon looks that name up in a fixed table
(`daemon/operations.py`, `daemon/procedures.py`), validates every argument, and
constructs the argv itself. A plugin that named an operation outside that table
would simply be rejected.

The rules the daemon enforces, and that `tests/test_operations.py` attacks
directly:

| Rule | Why |
|---|---|
| No `shell=True`, ever; every command is an argv list | `"/dev/sda; rm -rf /"` cannot become a second command |
| Device arguments must match a strict device-node regex **and** exist | blocks `/etc/passwd`, `/dev/../etc/shadow`, argument splitting |
| Choices are validated against enums | no caller-supplied string ever becomes a flag |
| Filenames are reduced to a basename and re-rooted | `../../etc/shadow` becomes `shadow` under `backups/` |
| Whole-disk writes reuse the builder's guards | refuses partitions and the running system disk |
| Disk and partition arguments are distinct | SMART cannot receive a partition; filesystem tools cannot receive a whole disk |
| Filesystem writers require an unmounted partition | prevents repair, backup or restore against a live mounted filesystem |
| Existing backup/recovery destinations are refused | never silently overwrites prior results |

## Available tools

| Tool | Category | Risk | Backed by |
|---|---|---|---|
| Backup Partition | Backup | safe | `partclone.<fs>`, or `dd` for unknown filesystems |
| Restore Partition | Backup | **destructive** | `partclone.restore` / `dd` |
| Drive Health (SMART) | Diagnostics | safe | `smartctl --scan-open`, adaptive `smartctl -d ... -a/-t` |
| Filesystem Check & Repair | Repair | caution | `e2fsck` / `ntfsfix` / `fsck.vfat` / `fsck.exfat` |
| Linux Boot Repair | Repair | caution | chroot + `grub-install`, `update-initramfs` |
| Windows Repair | Repair | caution | `ntfsfix`, restore of Windows EFI boot files |
| Deleted File Recovery | Recovery | safe | `photorec` (source is read-only) |
| Provision Machine | Provisioning | **destructive** | `dd` + optional answer-file seed + verify |
| Hardware Report | Diagnostics | safe | fixed `uname`, `free`, `lsblk`, `lspci`, `lsusb` inventory |
| Network Diagnostics | Diagnostics | safe | fixed NetworkManager, address, route, DNS, ping and HTTPS checks |

Backups are written to `backups/` and recovered files to `recovered/` on the
PENDATA partition, so they survive reboots and a factory reset of the
persistence layer.

## Safety affordances in the UI

- Destructive tools require an explicit "I understand" checkbox before Run is enabled.
- Device pickers mark PenLive's own partitions with `⚠ PenLive` and show a
  warning when one is selected. They are deliberately **listed rather than
  hidden**: a user doing recovery may legitimately need to inspect the stick,
  but must never image over it by accident.
- Mounted partitions are flagged in the UI and rejected again by the daemon,
  since imaging or repairing a mounted filesystem is unsafe.
- A tool whose underlying binary is missing is greyed out and names the missing
  package, instead of accepting a form that could only fail.

## What Windows Repair does not do

Rebuilding the BCD store needs `bcdboot`, a Windows binary with no faithful
Linux equivalent. This tool therefore offers only what can be done honestly
from Linux — clearing the NTFS dirty bit, and restoring the Windows EFI boot
files that another OS installer overwrote — and says so in its own output.
Anything deeper needs Windows recovery media.

## Jobs

Privileged work runs as a job in the daemon, not in the request:

- `POST /api/tools/<id>/run` → `{id, state: "running", ...}`
- `WS /api/jobs/<id>/stream` → snapshots roughly once a second
- `POST /api/jobs/<id>/cancel` → terminates the active command, then kills after 5s

The live UI keeps the most recent 2000 lines in memory. Every job is also
written to `logs/<timestamp>-<job>-<kind>.log` on PENDATA, visible from the
Files tab and preserved across reboots. Persistent logs are capped at 10 MiB
per job so a verbose PhotoRec pass cannot fill the stick; completion state and
errors are still appended after the cap.

SMART is transport-aware. It first reads the device type reported by
`smartctl --scan-open`, then falls back through automatic, SAT and SCSI modes.
This handles USB-to-SATA bridges that reject the generic probe with “Invalid
Field in Command”. Only smartctl exit bits 0–2 mean the invocation failed;
health-warning bits remain a completed diagnostic and are called out in its
log.

## Adding a tool

1. If it needs a new privileged action, add an `Operation` to
   `daemon/operations.py` (single command) or a generator to
   `daemon/procedures.py` (multi-step, e.g. mount → chroot → unmount).
   **This is the security-relevant step** — validate every argument.
2. Add a `Plugin` subclass under `app/plugins/` describing the form and mapping
   values to a `JobSpec`.
3. Register it in `app/plugins/__init__.py` (an explicit list, not filesystem
   autodiscovery — what can run as root should be reviewable in one place).
4. Add the required binaries to `live/config/package-lists/penlive.list.chroot`.
5. `tests/test_plugins.py` automatically checks that every plugin's `JobSpec`
   names a real operation, which catches the typo that would otherwise only
   show up at runtime.

```python
class MyToolPlugin(Plugin):
    id = "my-tool"
    name = "My Tool"
    description = "What it does, and what it will not do."
    category = "diagnostics"
    danger = "safe"
    required_tools = ("mybinary",)
    params = (Param(name="device", label="Drive", type="device"),)

    def build_job(self, values):
        return JobSpec("my_operation", {"device": values["device"]}, "My tool run")
```
