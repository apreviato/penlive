import os
import sys
from pathlib import Path

# Set before any `app` import: paths.py reads these at module import time.
# Tests must not depend on internet access, and must not pay a network
# round-trip for every FastAPI app instance they create.
os.environ.setdefault("PENLIVE_DEV", "1")
os.environ.setdefault("PENLIVE_OFFLINE", "1")

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point the DB layer at a throwaway sqlite file for one test."""
    from app import db, paths

    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "manager.db")
    db.reset_for_tests()
    yield
    db.reset_for_tests()


@pytest.fixture(autouse=True)
def clear_download_watchers():
    """downloader._active is module state that outlives a test.

    Each async test gets its own event loop, so a watcher task left behind by
    an earlier one belongs to a loop that has since been closed — and anything
    that then waits on it raises "Event loop is closed" from whichever test
    happens to run next. The manager itself has a single loop for the life of
    the process, so this is purely a test-isolation concern.
    """
    from app.services import downloader

    downloader._active.clear()
    downloader._cancelling.clear()
    downloader._queue_resume_task = None
    yield
    for task in downloader._cancelling.values():
        task.cancel()
    downloader._active.clear()
    downloader._cancelling.clear()
    if downloader._queue_resume_task is not None:
        downloader._queue_resume_task.cancel()
    downloader._queue_resume_task = None
