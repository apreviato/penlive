from pathlib import Path

from penlive.disk import default_layout
from penlive.provision import ProvisionInputs, provision
from penlive.runner import CommandRunner


def _inputs(tmp_path: Path) -> ProvisionInputs:
    live = tmp_path / "live"
    live.mkdir()
    for name in ("vmlinuz", "initrd.img", "filesystem.squashfs"):
        (live / name).write_bytes(b"x")
    grub_cfg = tmp_path / "grub.cfg"
    grub_cfg.write_text("# grub")
    recovery_cfg = tmp_path / "recovery.cfg"
    recovery_cfg.write_text("# recovery")
    return ProvisionInputs(live_dir=live, grub_cfg=grub_cfg, recovery_cfg=recovery_cfg)


def test_dry_run_reports_no_problems(tmp_path):
    """A dry run writes nothing, so validating the on-disk result would flag
    every file as missing and make a successful preview look like a failure."""
    runner = CommandRunner(dry_run=True)
    problems = provision(runner, "/dev/sdb", default_layout(), _inputs(tmp_path), tmp_path / "mnt")
    assert problems == []


def test_dry_run_plan_covers_the_whole_install(tmp_path):
    runner = CommandRunner(dry_run=True)
    provision(runner, "/dev/sdb", default_layout(), _inputs(tmp_path), tmp_path / "mnt")
    history = " ".join(runner.history)

    assert "grub-mkstandalone" in history
    assert "BOOTX64.EFI" in history
    assert "filesystem.squashfs" in history
    assert "grub-editenv" in history and "next_entry=" in history
    assert str(Path("bootsys") / "grub" / "grubenv") in history
    assert "mount -t vfat /dev/sdb1" in history
    assert "mount -t ext4 /dev/sdb2" in history
    assert "mount -t ext4 /dev/sdb3" in history
    assert "mount -t exfat /dev/sdb4" in history


def test_every_partition_is_unmounted(tmp_path):
    """Leaving a mount behind on a real run would keep the USB busy and risk
    an incomplete flush when the user pulls it."""
    runner = CommandRunner(dry_run=True)
    provision(runner, "/dev/sdb", default_layout(), _inputs(tmp_path), tmp_path / "mnt")
    assert sum(1 for cmd in runner.history if cmd.startswith("umount ")) == 4


def test_persistence_conf_written_to_persist_partition(tmp_path):
    runner = CommandRunner(dry_run=True)
    provision(runner, "/dev/sdb", default_layout(), _inputs(tmp_path), tmp_path / "mnt")
    assert any("persist" in cmd and "persistence.conf" in cmd for cmd in runner.history)
