"""Thin wrapper around subprocess used for every privileged/destructive step.

Everything that touches a block device or writes system files goes through a
CommandRunner so that --dry-run can print the exact command sequence without
side effects, and real runs leave an audit trail on disk.
"""
from __future__ import annotations

import logging
import shutil
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("penlive.runner")


class CommandError(RuntimeError):
    def __init__(self, cmd: list[str], returncode: int, stderr: str):
        printable = " ".join(shlex.quote(c) for c in cmd)
        super().__init__(f"command failed ({returncode}): {printable}\n{stderr}")
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr


@dataclass
class CommandRunner:
    dry_run: bool = False
    log_path: Path | None = None
    _history: list[str] = field(default_factory=list)

    def run(
        self,
        cmd: list[str],
        *,
        check: bool = True,
        input: str | None = None,
    ) -> subprocess.CompletedProcess:
        printable = " ".join(shlex.quote(c) for c in cmd)
        self._history.append(printable)
        self._log_line(("[dry-run] " if self.dry_run else "[exec] ") + printable)

        if self.dry_run:
            log.info("DRY-RUN  %s", printable)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        log.info("RUN      %s", printable)
        proc = subprocess.run(cmd, capture_output=True, text=True, input=input)
        if proc.stdout:
            log.debug(proc.stdout)
        if check and proc.returncode != 0:
            self._log_line(f"[fail] rc={proc.returncode} stderr={proc.stderr.strip()}")
            raise CommandError(cmd, proc.returncode, proc.stderr)
        return proc

    def write_file(self, path: Path, content: str) -> None:
        self._history.append(f"write {path}")
        self._log_line(f"[dry-run write] {path}" if self.dry_run else f"[write] {path}")
        if self.dry_run:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def install_file(self, source: Path, target: Path) -> None:
        """Copy one artifact with install -D semantics, without a host CLI dependency."""
        printable = " ".join(shlex.quote(c) for c in ["install", "-D", str(source), str(target)])
        self._history.append(printable)
        self._log_line(("[dry-run] " if self.dry_run else "[copy] ") + printable)
        if self.dry_run:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    def _log_line(self, line: str) -> None:
        if not self.log_path:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    @property
    def history(self) -> list[str]:
        return list(self._history)
