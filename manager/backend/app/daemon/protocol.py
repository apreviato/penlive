"""Newline-delimited JSON framing shared by the daemon server and client.

Pure stdlib, importable on any platform (including Windows dev machines) —
only server.py and the actual socket calls in client.py are Linux-only.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# The daemon's entire attack surface. Anything not in this set is rejected
# before it reaches a handler — see docs/ARCHITECTURE.md "Privileged daemon".
ALLOWED_COMMANDS = {
    "ping",
    "mount_image",
    "umount",
    "mount_device",
    "umount_device",
    "write_nextboot",
    "clear_nextboot",
    # PENSYS flips to read-only after an unclean unplug, which strands the
    # kernel/initrd extraction the boot router depends on.
    "remount_boot_rw",
    "reboot",
    "poweroff",
    "kexec_boot",
    "write_usb",
    # A narrowly-scoped, temporary ACL lets the unprivileged QEMU process open
    # exactly one user-confirmed whole disk. The daemon removes it when the VM
    # stops; no generic chmod/chown surface is exposed.
    "prepare_vm_disk",
    "release_vm_disk",
    # Tools/plugins. `job_start` takes an operation *name* plus structured
    # arguments — never a command line. What each name may execute is fixed by
    # daemon/operations.py and daemon/procedures.py.
    "job_start",
    "job_status",
    "job_list",
    "job_cancel",
    "run_operation",
    "list_operations",
    "set_keyboard",
    # NetworkManager control needs root/polkit privileges on the live system.
    # These remain fixed operations; there is no generic nmcli/command surface.
    "network_scan",
    "network_status",
    "network_connect",
    # Secure Boot. `sign_kernel` only ever signs a file inside the extracted
    # boot cache with the machine's own key - it cannot be pointed elsewhere.
    "mok_setup",
    "sign_kernel",
}


@dataclass
class Request:
    cmd: str
    args: dict[str, Any] = field(default_factory=dict)

    def encode(self) -> bytes:
        return (json.dumps({"cmd": self.cmd, "args": self.args}) + "\n").encode("utf-8")

    @staticmethod
    def decode(line: bytes) -> "Request":
        obj = json.loads(line.decode("utf-8"))
        return Request(cmd=obj["cmd"], args=obj.get("args", {}))


@dataclass
class Response:
    ok: bool
    result: Any = None
    error: str | None = None

    def encode(self) -> bytes:
        return (json.dumps({"ok": self.ok, "result": self.result, "error": self.error}) + "\n").encode("utf-8")

    @staticmethod
    def decode(line: bytes) -> "Response":
        obj = json.loads(line.decode("utf-8"))
        return Response(ok=obj["ok"], result=obj.get("result"), error=obj.get("error"))
