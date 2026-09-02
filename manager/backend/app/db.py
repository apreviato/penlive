"""Plain stdlib sqlite3 (sync) access, one connection per thread.

FastAPI runs `def` route handlers in a threadpool, but the download watchers,
the progress WebSockets and the boot/VM routers all touch the database straight
from the event loop. A single shared connection behind one global lock meant a
worker thread doing a long series of writes (a rescan, or post-download
inspection) could park the event loop on lock acquisition, freezing every
in-flight download and leaving the next request hanging. A connection per
thread plus WAL lets readers run while a writer commits, and sqlite's own
busy_timeout — not a Python lock the event loop can be caught on — serialises
the writers.

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
    verified INTEGER NOT NULL DEFAULT 0,
    origin TEXT NOT NULL DEFAULT 'catalog',
    inspection_error TEXT,
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

# Bumped by reset_for_tests() so a thread that already holds a connection to the
# previous DB_PATH reopens instead of quietly writing to the old file.
_generation = 0
_schema_lock = threading.Lock()
_local = threading.local()


def _connect() -> sqlite3.Connection:
    paths.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # timeout/busy_timeout: wait for another thread's write rather than failing
    # a download update with "database is locked".
    conn = sqlite3.connect(paths.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    with _schema_lock:
        _apply_schema(conn)
    return conn


def _apply_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # Existing sticks keep their SQLite database across manager upgrades.
    # CREATE TABLE IF NOT EXISTS does not add new columns, so apply the small
    # forward-only migrations here before any repository query can use them.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(images)")}
    if "verified" not in columns:
        conn.execute("ALTER TABLE images ADD COLUMN verified INTEGER NOT NULL DEFAULT 0")
    if "origin" not in columns:
        conn.execute("ALTER TABLE images ADD COLUMN origin TEXT NOT NULL DEFAULT 'catalog'")
    if "inspection_error" not in columns:
        conn.execute("ALTER TABLE images ADD COLUMN inspection_error TEXT")
    conn.commit()


def db() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is not None and _local.generation == _generation:
        return conn
    if conn is not None:
        conn.close()
    conn = _connect()
    _local.conn = conn
    _local.generation = _generation
    return conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    conn = db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def reset_for_tests() -> None:
    """Test-only: force a fresh connection (e.g. after pointing DB_PATH at a temp file)."""
    global _generation
    _generation += 1
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None
