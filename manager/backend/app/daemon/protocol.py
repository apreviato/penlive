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
    "write_nextboot",
    "clear_nextboot",
    "reboot",
    "poweroff",
    "kexec_boot",
    "write_usb",
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
