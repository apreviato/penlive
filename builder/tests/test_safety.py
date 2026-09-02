import pytest

from penlive.safety import UnsafeTargetError, assert_target_is_safe, base_disk, confirm_or_raise


def test_base_disk_sd():
    assert base_disk("/dev/sda2") == "/dev/sda"
    assert base_disk("/dev/sdb") == "/dev/sdb"


def test_base_disk_nvme():
    assert base_disk("/dev/nvme0n1p2") == "/dev/nvme0n1"


def test_base_disk_mmcblk():
    assert base_disk("/dev/mmcblk0p1") == "/dev/mmcblk0"


def test_rejects_partition_suffix_as_target():
    with pytest.raises(UnsafeTargetError, match="whole-disk"):
        assert_target_is_safe("/dev/sdb1")


def test_accepts_whole_disk_path():
    # No system disk is detectable on a non-Linux test host, so this should not raise.
    assert_target_is_safe("/dev/sdb")


def test_confirm_or_raise_requires_exact_match():
    with pytest.raises(UnsafeTargetError):
        confirm_or_raise("/dev/sdb", "/dev/sdc")
    confirm_or_raise("/dev/sdb", "/dev/sdb")  # should not raise


def test_confirm_or_raise_rejects_blank():
    with pytest.raises(UnsafeTargetError):
        confirm_or_raise("/dev/sdb", "")


def test_loop_devices_are_rejected_by_default():
    """The physical-device install path must never accept /dev/loopN."""
    with pytest.raises(UnsafeTargetError, match="whole-disk"):
        assert_target_is_safe("/dev/loop0")


def test_loop_devices_are_accepted_when_explicitly_allowed():
    """Building a disk image partitions a sparse file attached to /dev/loopN.
    Without this the entire `penlive image` path raises before writing a byte."""
    assert_target_is_safe("/dev/loop0", allow_loop=True)
    assert_target_is_safe("/dev/loop12", allow_loop=True)


def test_allow_loop_does_not_weaken_other_checks():
    for bad in ("/dev/loop0p1", "/etc/passwd", "/dev/sdb1"):
        with pytest.raises(UnsafeTargetError):
            assert_target_is_safe(bad, allow_loop=True)
