import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point the DB layer at a throwaway sqlite file for one test."""
    from app import db, paths

    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "manager.db")
    db.reset_for_tests()
    yield
    db.reset_for_tests()
