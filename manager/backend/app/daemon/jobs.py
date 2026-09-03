"""Job runner: long-lived privileged work with cancellation and a log tail.

Lives in the daemon because the work is privileged. The API only ever refers to
a job by id, so a bug there can start/stop/read jobs but cannot influence what
a job actually executes — that is fixed by operations.py and procedures.py.

The live tail is capped because a PhotoRec run over a 2 TB disk can emit
hundreds of thousands of lines. A bounded persistent copy is also written to
PENDATA/logs so failures survive daemon and machine restarts.
"""
from __future__ import annotations

import itertools
import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from .. import paths
from .operations import OperationError, build_argv
from .procedures import PROCEDURES, PROCEDURE_REQUIREMENTS, set_process_callback

LOG_LIMIT = 2000
LOG_LINE_LIMIT = 16 * 1024
DISK_LOG_LIMIT_BYTES = 10 * 1024 * 1024
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
    log: deque[str] = field(default_factory=lambda: deque(maxlen=LOG_LIMIT))
    log_file: str | None = None
    log_error: str | None = None
    _process: subprocess.Popen | None = field(default=None, repr=False)
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _log_total: int = field(default=0, repr=False)
    _log_path: Path | None = field(default=None, repr=False)
    _log_stream: TextIO | None = field(default=None, repr=False)
    _disk_log_bytes: int = field(default=0, repr=False)
    _disk_log_truncated: bool = field(default=False, repr=False)
    _last_flush: float = field(default_factory=time.monotonic, repr=False)

    def attach_log(self) -> None:
        """Open a persistent log on PENDATA without making logging a job dependency."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        safe_kind = re.sub(r"[^A-Za-z0-9_-]", "_", self.kind)[:48] or "tool"
        filename = f"{stamp}-{self.id:04d}-{safe_kind}.log"
        try:
            paths.JOB_LOG_DIR.mkdir(parents=True, exist_ok=True)
            self._log_path = paths.JOB_LOG_DIR / filename
            # Line buffering makes a running/hung tool visible in Files without
            # forcing an fsync for every line; the kernel still batches USB I/O.
            self._log_stream = self._log_path.open("x", encoding="utf-8", buffering=1)
            self.log_file = f"logs/{filename}"
            self._write_persistent_locked(f"PenLive tool job {self.id}", force=True)
            self._write_persistent_locked(f"Kind: {self.kind}", force=True)
            self._write_persistent_locked(f"Title: {_single_line(self.title)}", force=True)
            self._write_persistent_locked(
                f"Started: {datetime.fromtimestamp(self.started_at, timezone.utc).isoformat()}",
                force=True,
            )
            self._write_persistent_locked("", force=True)
            if self._log_stream:
                self._log_stream.flush()
        except OSError as exc:
            self.log_error = f"could not save the job log: {exc}"
            self._close_stream_locked()

    def append(self, line: str) -> None:
        if len(line) > LOG_LINE_LIMIT:
            line = line[:LOG_LINE_LIMIT] + " ... line truncated ..."
        with self._lock:
            self.log.append(line)
            self._log_total += 1
            self._write_persistent_locked(line)
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
            buffered = list(self.log)
            first_available = self._log_total - len(buffered)
            if log_offset < first_available:
                visible_log = [
                    f"... earlier output is available in {self.log_file or 'the persistent log'} ..."
                ] + buffered
            else:
                visible_log = buffered[max(0, log_offset - first_available):]
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
                "log": visible_log,
                "log_total": self._log_total,
                "log_file": self.log_file,
                "log_error": self.log_error,
            }

    def finish(self, state: str) -> None:
        with self._lock:
            self.state = state
            self.finished_at = time.time()
            if self._log_stream:
                self._write_persistent_locked("", force=True)
                self._write_persistent_locked(
                    f"Finished: {datetime.fromtimestamp(self.finished_at, timezone.utc).isoformat()}",
                    force=True,
                )
                self._write_persistent_locked(f"State: {state}", force=True)
                if self.exit_code is not None:
                    self._write_persistent_locked(f"Exit code: {self.exit_code}", force=True)
                if self.error:
                    self._write_persistent_locked(f"Error: {_single_line(self.error)}", force=True)
                try:
                    if self._log_stream:
                        self._log_stream.flush()
                        os.fsync(self._log_stream.fileno())
                except OSError as exc:
                    self.log_error = f"could not finish saving the job log: {exc}"
                finally:
                    self._close_stream_locked()

    def _write_persistent_locked(self, line: str, *, force: bool = False) -> None:
        if not self._log_stream or (self._disk_log_truncated and not force):
            return
        encoded_size = len((line + "\n").encode("utf-8", errors="replace"))
        try:
            if not force and self._disk_log_bytes + encoded_size > DISK_LOG_LIMIT_BYTES:
                marker = f"... persistent log truncated at {DISK_LOG_LIMIT_BYTES} bytes ...\n"
                self._log_stream.write(marker)
                self._disk_log_bytes += len(marker.encode("utf-8"))
                self._disk_log_truncated = True
                self._log_stream.flush()
                return
            self._log_stream.write(line + "\n")
            self._disk_log_bytes += encoded_size
            now = time.monotonic()
            if now - self._last_flush >= 1:
                self._log_stream.flush()
                self._last_flush = now
        except OSError as exc:
            self.log_error = f"job output could no longer be saved: {exc}"
            self._close_stream_locked()

    def _close_stream_locked(self) -> None:
        if self._log_stream:
            try:
                self._log_stream.close()
            except OSError:
                pass
        self._log_stream = None


_jobs: dict[int, Job] = {}
_jobs_lock = threading.Lock()


def start_job(kind: str, args: dict, title: str) -> Job:
    """Validate up front, then run in a background thread.

    Validation happens on the calling thread so that a bad request fails the
    RPC immediately with a useful message, instead of creating a job that dies
    a moment later.
    """
    try:
        if kind in PROCEDURES:
            missing = [tool for tool in PROCEDURE_REQUIREMENTS.get(kind, ()) if not shutil.which(tool)]
            if missing:
                raise OperationError(f"procedure {kind!r} needs missing tool(s): {', '.join(missing)}")
            destructive = kind in ("windows_repair", "provision_apply")
            job = Job(id=next(_ids), kind=kind, title=title, destructive=destructive)
            runner = _run_procedure
            payload: Any = args
        else:
            argv, op = build_argv(kind, args)  # raises OperationError on bad input
            job = Job(id=next(_ids), kind=kind, title=title, destructive=op.destructive)
            runner = _run_argv
            payload = argv
    except OperationError as exc:
        _persist_rejection(kind, exc)
        raise

    with _jobs_lock:
        _jobs[job.id] = job

    job.attach_log()
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
    set_process_callback(lambda proc: setattr(job, "_process", proc))
    try:
        for line in PROCEDURES[job.kind](args):
            if job._cancel.is_set():
                job.append("Cancelled by user.")
                _set_state(job, "cancelled")
                return
            job.append(line)
        job.exit_code = 0
        _set_state(job, "success")
    except OperationError as exc:
        if job._cancel.is_set():
            job.append("Cancelled by user.")
            _set_state(job, "cancelled")
        else:
            _fail(job, str(exc))
    except Exception as exc:  # noqa: BLE001
        if job._cancel.is_set():
            job.append("Cancelled by user.")
            _set_state(job, "cancelled")
        else:
            _fail(job, f"{type(exc).__name__}: {exc}")
    finally:
        set_process_callback(None)
        job._process = None


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
    job.finish(state)


def _single_line(value: str) -> str:
    return " ".join(str(value).replace("\x00", "").splitlines())[:1000]


def _persist_rejection(kind: str, error: Exception) -> None:
    """Leave evidence when validation fails before a Job object can start."""
    try:
        paths.JOB_LOG_DIR.mkdir(parents=True, exist_ok=True)
        target = paths.JOB_LOG_DIR / "tool-rejections.log"
        stamp = datetime.now(timezone.utc).isoformat()
        with target.open("a", encoding="utf-8") as stream:
            stream.write(f"{stamp} kind={_single_line(kind)!r} error={_single_line(str(error))}\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        # A read-only or absent PENDATA must not mask the useful validation
        # error the caller is already receiving.
        pass


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
