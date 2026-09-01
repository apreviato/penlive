"""Plain stdlib sqlite3 (sync) access. FastAPI runs `def` route handlers in a
threadpool, so a blocking sqlite3 connection here doesn't stall the event loop.

Networks table is a "recently used" convenience index for the UI only — Wi-Fi
credentials themselves live in NetworkManager's own connection profiles under
/etc/NetworkManager/system-connections (root-only, persisted via OverlayFS),
never duplicated into our sqlite file.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

from . import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    family TEXT NOT NULL,
    version TEXT,
    architecture TEXT NOT NULL DEFAULT 'amd64',
    adapter TEXT,
    path TEXT,
    sha256 TEXT,
    size_bytes INTEGER,
    status TEXT NOT NULL DEFAULT 'not_downloaded',
    source_url TEXT,
    capabilities_json TEXT NOT NULL DEFAULT '{}',
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS downloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    image_id TEXT NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    gid TEXT,
    progress_bytes INTEGER NOT NULL DEFAULT 0,
    total_bytes INTEGER,
    speed_bps INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'queued',
    error TEXT,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_downloads_image ON downloads(image_id);

CREATE TABLE IF NOT EXISTS boots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    image_id TEXT NOT NULL REFERENCES images(id),
    adapter TEXT NOT NULL,
    method TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS networks (
    ssid TEXT PRIMARY KEY,
    last_connected_at TEXT,
    autoconnect INTEGER NOT NULL DEFAULT 1
);
"""

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _connect() -> sqlite3.Connection:
    paths.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(paths.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        with _lock:
            if _conn is None:
                _conn = _connect()
    return _conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    conn = db()
    with _lock:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def reset_for_tests() -> None:
    """Test-only: force a fresh in-process connection (e.g. after pointing DB_PATH at a temp file)."""
    global _conn
    if _conn is not None:
        _conn.close()
    _conn = None
