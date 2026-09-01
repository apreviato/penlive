"""Job runner behaviour, driven through real subprocesses.

Uses `python -c` as the workload so these run identically on Windows and Linux
rather than depending on sh.
"""
import sys
import time

import pytest

from app.daemon import jobs


def wait_for(predicate, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def python_op(monkeypatch):
    """Register a throwaway operation that runs a python snippet."""
    from app.daemon import operations

    op = operations.Operation(
        name="_test_python",
        build=lambda args: [sys.executable, "-c", args["code"]],
        description="test only",
    )
    monkeypatch.setitem(operations.OPERATIONS, "_test_python", op)
    return op


def test_successful_job_captures_output(python_op):
    job = jobs.start_job("_test_python", {"code": "print('hello from job')"}, "echo test")
    assert wait_for(lambda: job.state != "running")

    snapshot = job.snapshot()
    assert snapshot["state"] == "success"
    assert snapshot["exit_code"] == 0
    assert any("hello from job" in line for line in snapshot["log"])
    assert snapshot["progress"] == 100.0


def test_failing_job_is_marked_failed(python_op):
    job = jobs.start_job("_test_python", {"code": "import sys; sys.exit(3)"}, "failing test")
    assert wait_for(lambda: job.state != "running")

    snapshot = job.snapshot()
    assert snapshot["state"] == "failed"
    assert snapshot["exit_code"] == 3
    assert "3" in (snapshot["error"] or "")


def test_stderr_is_captured_too(python_op):
    job = jobs.start_job(
        "_test_python", {"code": "import sys; print('to stderr', file=sys.stderr)"}, "stderr test"
    )
    assert wait_for(lambda: job.state != "running")
    assert any("to stderr" in line for line in job.snapshot()["log"])


def test_bad_arguments_fail_before_a_job_is_created():
    """Validation happens on the caller's thread so the RPC returns a useful
    error, rather than creating a job that dies immediately."""
    from app.daemon.operations import OperationError

    with pytest.raises(OperationError):
        jobs.start_job("smart_scan", {"device": "/etc/passwd"}, "hostile")


def test_unknown_kind_is_rejected():
    from app.daemon.operations import OperationError

    with pytest.raises(OperationError):
        jobs.start_job("definitely_not_an_operation", {}, "nope")


def test_cancel_stops_a_long_running_job(python_op):
    job = jobs.start_job("_test_python", {"code": "import time; time.sleep(60)"}, "long job")
    assert wait_for(lambda: job._process is not None)

    assert jobs.cancel_job(job.id) is True
    assert wait_for(lambda: job.state != "running")
    assert job.snapshot()["state"] == "cancelled"


def test_cancelling_a_finished_job_returns_false(python_op):
    job = jobs.start_job("_test_python", {"code": "pass"}, "quick")
    assert wait_for(lambda: job.state != "running")
    assert jobs.cancel_job(job.id) is False


def test_log_offset_returns_only_new_lines(python_op):
    job = jobs.start_job("_test_python", {"code": "print('a'); print('b')"}, "offset test")
    assert wait_for(lambda: job.state != "running")

    full = job.snapshot()
    assert len(full["log"]) >= 3  # the "$ ..." command line, plus a and b
    tail = job.snapshot(log_offset=full["log_total"])
    assert tail["log"] == []


def test_progress_is_parsed_from_partclone_style_output(python_op):
    job = jobs.start_job("_test_python", {"code": "print('Completed:  42.50%')"}, "progress test")
    assert wait_for(lambda: job.state != "running")
    # The final exit sets 100; check the parser saw the intermediate value.
    assert any("42.50%" in line for line in job.snapshot()["log"])


def test_log_is_capped(python_op):
    """A photorec run over a large disk can emit hundreds of thousands of lines;
    the daemon must not grow without bound."""
    job = jobs.start_job(
        "_test_python",
        {"code": f"[print(i) for i in range({jobs.LOG_LIMIT + 500})]"},
        "flood test",
    )
    assert wait_for(lambda: job.state != "running", timeout=30)

    snapshot = job.snapshot()
    assert snapshot["log_total"] <= jobs.LOG_LIMIT + 1
    assert any("truncated" in line for line in snapshot["log"])


def test_listing_excludes_log_bodies(python_op):
    jobs.start_job("_test_python", {"code": "print('x')"}, "listed job")
    listing = jobs.list_jobs()
    assert listing
    assert all("log" not in entry for entry in listing)


def test_get_job_returns_none_for_unknown_id():
    assert jobs.get_job(999_999) is None
