"""SMART transport detection and USB bridge fallback."""
from __future__ import annotations

import io

import pytest

from app.daemon import operations, procedures
from app.daemon.operations import OperationError


class FakeProcess:
    def __init__(self, argv, responses, calls):
        self.argv = argv
        calls.append(argv)
        code, output = responses.pop(0)
        self.returncode = code
        self.stdout = io.StringIO(output)

    def wait(self):
        return self.returncode


@pytest.fixture
def fake_disk(monkeypatch):
    original_exists = operations.Path.exists
    monkeypatch.setattr(
        operations.Path,
        "exists",
        lambda self: True if self.as_posix() == "/dev/sda" else original_exists(self),
    )


def install_processes(monkeypatch, responses):
    calls = []
    monkeypatch.setattr(
        procedures.subprocess,
        "Popen",
        lambda argv, **_kwargs: FakeProcess(argv, responses, calls),
    )
    return calls


def test_smart_uses_device_type_reported_by_scan_open(fake_disk, monkeypatch):
    calls = install_processes(monkeypatch, [
        (0, "/dev/sda -d sat # /dev/sda [SAT], ATA device\n"),
        (0, "SMART overall-health self-assessment test result: PASSED\n"),
    ])

    output = list(procedures.smart_scan({"device": "/dev/sda"}))

    assert calls == [
        ["smartctl", "--scan-open"],
        ["smartctl", "-a", "-d", "sat", "/dev/sda"],
    ]
    assert any("completed successfully" in line for line in output)


def test_smart_retries_invalid_usb_scsi_command_as_sat(fake_disk, monkeypatch):
    calls = install_processes(monkeypatch, [
        (0, ""),
        (4, "Read Device Identity failed: Invalid Field in Command\n"),
        (0, "Device Model: USB bridged SATA disk\n"),
    ])

    output = list(procedures.smart_scan({"device": "/dev/sda"}))

    assert calls[1] == ["smartctl", "-a", "/dev/sda"]
    assert calls[2] == ["smartctl", "-a", "-d", "sat", "/dev/sda"]
    assert any("trying another compatible mode" in line for line in output)


def test_smart_health_bits_are_a_completed_diagnostic(fake_disk, monkeypatch):
    install_processes(monkeypatch, [
        (0, ""),
        (8, "SMART overall-health self-assessment test result: FAILED\n"),
    ])

    output = list(procedures.smart_scan({"device": "/dev/sda"}))

    assert any("drive-health warning bits" in line for line in output)


def test_smart_selftest_validates_mode_before_running(fake_disk):
    with pytest.raises(OperationError, match="must be one of"):
        list(procedures.smart_selftest({"device": "/dev/sda", "test": "erase"}))
