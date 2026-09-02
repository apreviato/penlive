"""penlive: partitions, formats and provisions a PenLive live-USB.

    penlive devices                          list candidate block devices
    penlive install /dev/sdX --live-dir DIR   wipe + provision a real disk
    penlive image out.img --live-dir DIR      build a flashable image via a loop device
    penlive validate /dev/sdX                 sanity-check an already-provisioned disk

Every subcommand that touches a device supports --dry-run, which runs the
full command sequence through CommandRunner without executing anything.
"""
from __future__ import annotations

import argparse
import json
import logging
import platform
import subprocess
import sys
from pathlib import Path

from . import disk, grubinstall
from .image import attached_loop_device, compress_image, create_sparse_image
from .mount import mounted
from .provision import ProvisionInputs, provision
from .runner import CommandRunner
from .safety import UnsafeTargetError, assert_target_is_safe
from .verify import validate_mounted_layout

log = logging.getLogger("penlive")

DEFAULT_MOUNT_ROOT = Path("/tmp/penlive-install")


def _require_linux() -> None:
    if platform.system() != "Linux":
        print(
            "error: penlive must run on a Linux host with sgdisk/mkfs/grub-mkstandalone "
            f"available (detected {platform.system()}). Use --dry-run to preview the plan "
            "on any OS.",
            file=sys.stderr,
        )
        sys.exit(2)


def cmd_devices(args: argparse.Namespace) -> int:
    _require_linux()
    proc = subprocess.run(
        ["lsblk", "-J", "-b", "-o", "NAME,PATH,SIZE,MODEL,TRAN,TYPE,RM"],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(proc.stdout)
    print(f"{'DEVICE':<16}{'SIZE':>12}  {'TRAN':<6}{'RM':<4}MODEL")
    for dev in data.get("blockdevices", []):
        if dev.get("type") != "disk":
            continue
        size_gib = int(dev["size"]) / (1024**3)
        print(
            f"{dev['path']:<16}{size_gib:>10.1f}G  {dev.get('tran') or '-':<6}"
            f"{'yes' if dev.get('rm') else 'no':<4}{dev.get('model') or ''}"
        )
    return 0


def _disk_size_mib(device: str) -> int:
    proc = subprocess.run(["blockdev", "--getsize64", device], capture_output=True, text=True, check=True)
    return int(proc.stdout.strip()) // disk.MIB


def _build_layout(args: argparse.Namespace) -> disk.DiskLayout:
    return disk.default_layout(
        efi_mib=args.efi_mib,
        system_mib=args.system_mib,
        persist_mib=args.persist_mib,
        data_fs=args.data_fs,
    )


def _provision_inputs(args: argparse.Namespace) -> ProvisionInputs:
    return ProvisionInputs(
        live_dir=Path(args.live_dir),
        grub_cfg=Path(args.grub_cfg),
        recovery_cfg=Path(args.recovery_cfg),
        catalog_seed=Path(args.catalog) if args.catalog else None,
    )


def cmd_install(args: argparse.Namespace) -> int:
    device = args.device
    if not args.dry_run:
        _require_linux()
        try:
            assert_target_is_safe(device, allow_system_disk=args.allow_system_disk)
        except UnsafeTargetError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    layout = _build_layout(args)
    disk_size_mib = 256 * 1024 if args.dry_run else _disk_size_mib(device)
    try:
        disk.validate_layout(layout, disk_size_mib)
    except ValueError as exc:
        # A size mismatch is an operator mistake, not a crash; a traceback here
        # buries the one line that says which number to change.
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("=" * 70)
    print(f"  TARGET DEVICE : {device}")
    print(f"  SIZE          : {disk_size_mib / 1024:.1f} GiB")
    print("  ALL DATA ON THIS DEVICE WILL BE PERMANENTLY ERASED.")
    print("=" * 70)
    if not args.yes and not args.dry_run:
        typed = input(f"Type the device path ({device}) to continue: ")
    else:
        typed = device

    runner = CommandRunner(dry_run=args.dry_run, log_path=Path(args.log) if args.log else None)
    try:
        disk.apply_layout(
            runner, device, layout,
            assume_yes=args.yes, typed_confirmation=typed,
            allow_system_disk=args.allow_system_disk,
        )
    except UnsafeTargetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    problems = provision(runner, device, layout, _provision_inputs(args), Path(args.mount_root))
    if problems:
        print("VALIDATION FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    print("penlive: install complete." if not args.dry_run else "penlive: dry-run complete, nothing was written.")
    return 0


def cmd_image(args: argparse.Namespace) -> int:
    if not args.dry_run:
        _require_linux()
    layout = _build_layout(args)
    try:
        disk.validate_layout(layout, args.size_mib)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(
            f"hint: --size-mib must be at least "
            f"{layout.fixed_size_mib + disk.MIN_DATA_MIB} for this layout, "
            f"or lower --persist-mib / --system-mib.",
            file=sys.stderr,
        )
        return 1

    image_path = Path(args.output)
    runner = CommandRunner(dry_run=args.dry_run, log_path=Path(args.log) if args.log else None)
    create_sparse_image(runner, image_path, args.size_mib)

    with attached_loop_device(runner, image_path) as loop_dev:
        # allow_loop: the target here is a sparse file attached to /dev/loopN,
        # not a physical disk, so the whole-disk-node check must accept it.
        disk.apply_layout(
            runner, loop_dev, layout,
            assume_yes=True, allow_system_disk=True, allow_loop=True,
        )
        problems = provision(runner, loop_dev, layout, _provision_inputs(args), Path(args.mount_root))

    if problems:
        print("VALIDATION FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    if args.compress and not args.dry_run:
        final = compress_image(runner, image_path)
        print(f"penlive: image ready at {final}")
    else:
        print(f"penlive: image ready at {image_path}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    _require_linux()
    device = args.device
    layout = _build_layout(args)
    by_label = {p.label: p for p in layout.partitions}
    runner = CommandRunner(dry_run=False)
    mount_root = Path(args.mount_root)

    with mounted(runner, disk.partition_path(device, by_label["PENEFI"].number), mount_root / "efi", fstype="vfat") as efi_mp, \
         mounted(runner, disk.partition_path(device, by_label["PENSYS"].number), mount_root / "bootsys", fstype="ext4") as bootsys_mp, \
         mounted(runner, disk.partition_path(device, by_label["PENDATA"].number), mount_root / "data", fstype=by_label["PENDATA"].fstype) as data_mp:
        problems = validate_mounted_layout(efi_mp, bootsys_mp, data_mp)

    if problems:
        print("problems found:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("OK: layout looks correct")
    return 0


def _add_layout_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--efi-mib", type=int, default=512)
    p.add_argument("--system-mib", type=int, default=4096)
    p.add_argument("--persist-mib", type=int, default=8192)
    p.add_argument("--data-fs", choices=["exfat", "ext4"], default="exfat")


def _add_provision_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--live-dir", required=True, help="dir with vmlinuz, initrd.img, filesystem.squashfs")
    p.add_argument("--grub-cfg", default="grub/grub.cfg")
    p.add_argument("--recovery-cfg", default="grub/recovery.cfg")
    p.add_argument("--catalog", default="catalog/catalog.json")
    p.add_argument("--mount-root", default=str(DEFAULT_MOUNT_ROOT))
    p.add_argument("--log", default=None, help="append a command audit log to this path")
    p.add_argument("--dry-run", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="penlive")
    sub = parser.add_subparsers(dest="command", required=True)

    p_dev = sub.add_parser("devices", help="list candidate block devices")
    p_dev.set_defaults(func=cmd_devices)

    p_install = sub.add_parser("install", help="wipe and provision a real device")
    p_install.add_argument("device", help="e.g. /dev/sdb (whole disk, no partition suffix)")
    p_install.add_argument("--yes", action="store_true", help="skip the typed confirmation prompt")
    p_install.add_argument("--allow-system-disk", action="store_true", help="danger: allow targeting the running OS disk")
    _add_layout_args(p_install)
    _add_provision_args(p_install)
    p_install.set_defaults(func=cmd_install)

    p_image = sub.add_parser("image", help="build a flashable .img via a loop device")
    p_image.add_argument("output", help="output path, e.g. dist/penlive-amd64.img")
    p_image.add_argument("--size-mib", type=int, default=16384, help="total image size (default 16 GiB)")
    p_image.add_argument("--compress", action="store_true", help="zstd-compress the result and remove the raw image")
    _add_layout_args(p_image)
    _add_provision_args(p_image)
    p_image.set_defaults(func=cmd_image)

    p_validate = sub.add_parser("validate", help="sanity-check an already-provisioned device")
    p_validate.add_argument("device")
    p_validate.add_argument("--mount-root", default=str(DEFAULT_MOUNT_ROOT))
    _add_layout_args(p_validate)
    p_validate.set_defaults(func=cmd_validate)

    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
