import pytest

from bootstack import disk


def test_partition_path_sd():
    assert disk.partition_path("/dev/sdb", 1) == "/dev/sdb1"
    assert disk.partition_path("/dev/sdb", 4) == "/dev/sdb4"


def test_partition_path_nvme():
    assert disk.partition_path("/dev/nvme0n1", 1) == "/dev/nvme0n1p1"


def test_default_layout_labels_and_types():
    layout = disk.default_layout()
    labels = [p.label for p in layout.partitions]
    assert labels == ["BOOTEFI", "BOOTSYS", "persistence", "BOOTDATA"]
    assert layout.partitions[0].fstype == "fat32"
    assert layout.partitions[-1].size_mib is None


def test_default_layout_data_fs_override():
    layout = disk.default_layout(data_fs="ext4")
    assert layout.partitions[-1].fstype == "ext4"
    assert layout.partitions[-1].gpt_type == "8300"


def test_validate_layout_rejects_too_small_disk():
    layout = disk.default_layout()
    with pytest.raises(ValueError, match="too small"):
        disk.validate_layout(layout, disk_size_mib=8000)


def test_validate_layout_accepts_roomy_disk():
    layout = disk.default_layout()
    disk.validate_layout(layout, disk_size_mib=64000)  # should not raise


def test_validate_layout_requires_open_ended_last_partition():
    bad = disk.DiskLayout(
        partitions=(
            disk.Partition(1, "A", "fat32", 512, "ef00"),
            disk.Partition(2, "B", "ext4", 1024, "8300"),
        )
    )
    with pytest.raises(ValueError, match="last partition"):
        disk.validate_layout(bad, disk_size_mib=64000)


def test_sgdisk_commands_cover_every_partition():
    layout = disk.default_layout()
    cmds = disk.sgdisk_commands("/dev/sdx", layout)
    assert cmds[0] == ["sgdisk", "--zap-all", "/dev/sdx"]
    assert len(cmds) == 1 + len(layout.partitions)
    last = cmds[-1]
    assert "4:0:0" in last  # remainder-of-disk partition uses size 0


def test_mkfs_commands_match_fstypes():
    layout = disk.default_layout()
    cmds = disk.mkfs_commands("/dev/sdx", layout)
    assert cmds[0][0] == "mkfs.vfat"
    assert cmds[1][0] == "mkfs.ext4"
    assert cmds[2][0] == "mkfs.ext4"
    assert cmds[3][0] == "mkfs.exfat"
