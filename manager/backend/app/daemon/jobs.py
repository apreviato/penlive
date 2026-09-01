"""Job runner: long-lived privileged work with cancellation and a log tail.

Lives in the daemon because the work is privileged. The API only ever refers to
a job by id, so a bug there can start/stop/read jobs but cannot influence what
a job actually executes — that is fixed by operations.py and procedures.py.

Logs are capped (LOG_LIMIT lines) because a photorec run over a 2 TB disk will
happily emit hundreds of thousands of lines, and this process must not grow
without bound on a machine booted from a USB stick.
"""
from __future__ import annotations

import itertools
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .operations import OperationError, build_argv
from .procedures import PROCEDURES

LOG_LIMIT = 2000
_ids = itertools.count(1)

# dd:        "1234567 bytes (1.2 MB, 1.2 MiB) copied, 1 s, 1.2 MB/s"
# partclone: "Completed:  45.67%"
_PROGRESS_PATTERNS = (
    re.compile(r"Completed:\s*([0-9.]+)%"),
    re.compile(r"([0-9.]+)%\s*(?:completed|done)", re.IGNORECASE),
)


@dataclass
class Job:
    id: int
    kind: str            # operation or procedure name
    title: str
    destructive: bool
    state: str = "running"   # running | success | failed | cancelled
    progress: float | None = None
    exit_code: int | None = None
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    log: list[str] = field(default_factory=list)
    _process: subprocess.Popen | None = field(default=None, repr=False)
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def append(self, line: str) -> None:
        with self._lock:
            self.log.append(line)
            if len(self.log) > LOG_LIMIT:
                # Keep the head (what was launched) and the most recent tail.
                self.log = self.log[:50] + ["... log truncated ..."] + self.log[-(LOG_LIMIT - 51):]
            for pattern in _PROGRESS_PATTERNS:
                m = pattern.search(line)
                if m:
                    try:
                        self.progress = min(100.0, float(m.group(1)))
                    except ValueError:
                        pass
                    break

    def snapshot(self, log_offset: int = 0) -> dict[str, Any]:
        with self._lock:
            return {
                "id": self.id,
                "kind": self.kind,
                "title": self.title,
                "destructive": self.destructive,
                "state": self.state,
                "progress": self.progress,
                "exit_code": self.exit_code,
                "error": self.error,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "log": self.log[log_offset:],
                "log_total": len(self.log),
            }


_jobs: dict[int, Job] = {}
_jobs_lock = threading.Lock()


def start_job(kind: str, args: dict, title: str) -> Job:
    """Validate up front, then run in a background thread.

    Validation happens on the calling thread so that a bad request fails the
    RPC immediately with a useful message, instead of creating a job that dies
    a moment later.
    """
    if kind in PROCEDURES:
        destructive = kind in ("windows_repair", "provision_apply")
        job = Job(id=next(_ids), kind=kind, title=title, destructive=destructive)
        runner = _run_procedure
        payload: Any = args
    else:
        argv, op = build_argv(kind, args)  # raises OperationError on bad input
        job = Job(id=next(_ids), kind=kind, title=title, destructive=op.destructive)
        runner = _run_argv
        payload = argv

    with _jobs_lock:
        _jobs[job.id] = job

    thread = threading.Thread(target=runner, args=(job, payload), daemon=True)
    thread.start()
    return job


def _run_argv(job: Job, argv: list[str]) -> None:
    job.append(f"$ {' '.join(argv)}")
    try:
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        job._process = proc
        for line in proc.stdout:  # type: ignore[union-attr]
            if job._cancel.is_set():
                break
            job.append(line.rstrip("\n"))
        proc.wait()
        _finish_from_exit_code(job, proc.returncode)
    except FileNotFoundError as exc:
        _fail(job, f"tool not found: {exc}")
    except Exception as exc:  # noqa: BLE001 - report, never take the daemon down
        _fail(job, str(exc))


def _run_procedure(job: Job, args: dict) -> None:
    try:
        for line in PROCEDURES[job.kind](args):
            if job._cancel.is_set():
                job.append("Cancelled by user.")
                _set_state(job, "cancelled")
                return
            job.append(line)
        _set_state(job, "success")
        job.exit_code = 0
    except OperationError as exc:
        _fail(job, str(exc))
    except Exception as exc:  # noqa: BLE001
        _fail(job, f"{type(exc).__name__}: {exc}")


def _finish_from_exit_code(job: Job, code: int) -> None:
    job.exit_code = code
    if job._cancel.is_set():
        _set_state(job, "cancelled")
    elif code == 0:
        job.progress = 100.0
        _set_state(job, "success")
    else:
        job.error = f"exited with code {code}"
        _set_state(job, "failed")


def _fail(job: Job, message: str) -> None:
    job.error = message
    job.append(f"ERROR: {message}")
    _set_state(job, "failed")


def _set_state(job: Job, state: str) -> None:
    job.state = state
    job.finished_at = time.time()


def get_job(job_id: int) -> Job | None:
    with _jobs_lock:
        return _jobs.get(job_id)


def list_jobs() -> list[dict[str, Any]]:
    with _jobs_lock:
        jobs = list(_jobs.values())
    # Log excluded: a listing with every job's full log would be enormous.
    return [{k: v for k, v in j.snapshot().items() if k != "log"} for j in jobs]


def cancel_job(job_id: int) -> bool:
    job = get_job(job_id)
    if job is None or job.state != "running":
        return False
    job._cancel.set()
    if job._process and job._process.poll() is None:
        job._process.terminate()
        try:
            job._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            job._process.kill()
    return True
