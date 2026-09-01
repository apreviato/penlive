import os
import sys
from pathlib import Path

# Set before any `app` import: paths.py reads these at module import time.
# Tests must not depend on internet access, and must not pay a network
# round-trip for every FastAPI app instance they create.
os.environ.setdefault("BOOTSTACK_DEV", "1")
os.environ.setdefault("BOOTSTACK_OFFLINE", "1")

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
