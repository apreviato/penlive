"""Isolation properties of the embedded QEMU session."""
from types import SimpleNamespace

import pytest

from app.services import vm


def test_vm_receives_only_the_iso_and_no_installable_disk(monkeypatch):
    monkeypatch.setattr(vm, "kvm_available", lambda: True)

    argv = vm._qemu_args(
        "/usr/bin/qemu-system-x86_64",
        "debian-live",
        "/data/images/debian.iso",
        memory_mib=2048,
        cpus=2,
        display=3,
        websocket_port=5703,
        enable_kvm=True,
    )

    assert argv[argv.index("-cdrom") + 1] == "/data/images/debian.iso"
    assert "-drive" not in argv
    assert "-hda" not in argv
    assert "-blockdev" not in argv
    assert "-enable-kvm" in argv


def test_confirmed_physical_disk_uses_sata_and_one_time_iso_boot(monkeypatch):
    monkeypatch.setattr(vm, "kvm_available", lambda: False)

    argv = vm._qemu_args(
        "/usr/bin/qemu-system-x86_64",
        "debian-live",
        "/data/images/debian.iso",
        memory_mib=4096,
        cpus=4,
        display=4,
        websocket_port=5704,
        enable_kvm=True,
        physical_disk="/dev/nvme0n1",
        firmware_args=["-drive", "if=pflash,file=/tmp/vars.fd"],
    )

    assert argv[argv.index("-machine") + 1] == "q35"
    assert argv[argv.index("-boot") + 1] == "once=d,menu=on"
    drives = [argv[index + 1] for index, value in enumerate(argv) if value == "-drive"]
    assert any("file=/dev/nvme0n1" in drive and "format=raw" in drive for drive in drives)
    assert any("if=pflash" in drive for drive in drives)
    assert "-enable-kvm" not in argv


@pytest.mark.asyncio
async def test_direct_access_is_prepared_before_vm_is_restarted(monkeypatch):
    events = []
    vm._running["debian-live"] = SimpleNamespace(
        process=SimpleNamespace(returncode=None),
        physical_disk=None,
        iso_path="/data/images/debian.iso",
        memory_mib=4096,
        cpus=2,
        enable_kvm=True,
    )

    async def fake_call(command, **kwargs):
        events.append((command, kwargs))
        return {"lease": "lease-1"}

    async def fake_stop(image_id):
        events.append(("stop", image_id))

    async def fake_start(image_id, iso_path, **kwargs):
        events.append(("start", image_id, iso_path, kwargs))
        return {"physical_disk": kwargs["physical_disk"]}

    monkeypatch.setattr(vm.daemon_client, "call", fake_call)
    monkeypatch.setattr(vm, "stop", fake_stop)
    monkeypatch.setattr(vm, "start", fake_start)

    try:
        result = await vm.attach_physical_disk("debian-live", "/dev/nvme0n1", "/dev/nvme0n1")
    finally:
        vm._running.clear()

    assert result["physical_disk"] == "/dev/nvme0n1"
    assert events[0] == (
        "prepare_vm_disk",
        {"device": "/dev/nvme0n1", "confirmation": "/dev/nvme0n1"},
    )
    assert events[1] == ("stop", "debian-live")
    assert events[2][0] == "start"
    assert events[2][3]["disk_lease"] == "lease-1"
