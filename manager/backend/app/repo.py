"""Repository functions — the only place routers should write raw SQL."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .db import db, transaction


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    if "capabilities_json" in d:
        d["capabilities"] = json.loads(d.pop("capabilities_json") or "{}")
    if "verified" in d:
        d["verified"] = bool(d["verified"])
    return d


# ---- images ----------------------------------------------------------------

def upsert_image_from_catalog(entry: dict[str, Any]) -> None:
    with transaction() as conn:
        existing = conn.execute(
            "SELECT status, path, sha256, verified FROM images WHERE id = ?", (entry["id"],)
        ).fetchone()
        conn.execute(
            """
            INSERT INTO images (id, name, family, version, architecture, adapter, source_url,
                                 sha256, size_bytes, capabilities_json, status, verified, origin)
            VALUES (:id, :name, :family, :version, :architecture, :adapter, :source_url,
                    :sha256, :size_bytes, :capabilities_json, :status, :verified, 'catalog')
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, family=excluded.family, version=excluded.version,
                architecture=excluded.architecture, adapter=excluded.adapter,
                source_url=excluded.source_url, sha256=excluded.sha256,
                size_bytes=excluded.size_bytes, capabilities_json=excluded.capabilities_json,
                origin='catalog',
                updated_at=datetime('now')
            """,
            {
                "id": entry["id"],
                "name": entry["name"],
                "family": entry["family"],
                "version": entry.get("version"),
                "architecture": entry.get("architecture", "amd64"),
                "adapter": entry.get("adapter"),
                "source_url": (entry.get("sources") or [{}])[0].get("url"),
                "sha256": entry.get("sha256"),
                "size_bytes": entry.get("size"),
                "capabilities_json": json.dumps(entry.get("capabilities", {})),
                "status": existing["status"] if existing else "not_downloaded",
                "verified": existing["verified"] if existing else 0,
            },
        )


def upsert_local_image(image_id: str, name: str, path: str, size_bytes: int) -> None:
    """Register an ISO copied directly into PENDATA/images."""
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO images (id, name, family, architecture, path, size_bytes,
                                capabilities_json, status, verified, origin)
            VALUES (?, ?, 'local', 'amd64', ?, ?, ?, 'inspecting', 0, 'local')
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, path=excluded.path, size_bytes=excluded.size_bytes,
                origin='local', updated_at=datetime('now')
            """,
            (image_id, name, path, size_bytes,
             json.dumps({"nativeBoot": True, "vm": True, "mount": True})),
        )


def list_images() -> list[dict[str, Any]]:
    rows = db().execute("SELECT * FROM images ORDER BY family, name").fetchall()
    return [_row_to_dict(r) for r in rows]


def get_image(image_id: str) -> dict[str, Any] | None:
    row = db().execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()
    return _row_to_dict(row) if row else None


def set_image_status(image_id: str, status: str, **fields: Any) -> None:
    cols = ", ".join(f"{k} = :{k}" for k in fields)
    sep = ", " if cols else ""
    with transaction() as conn:
        conn.execute(
            f"UPDATE images SET status = :status{sep}{cols}, updated_at = datetime('now') WHERE id = :id",
            {"status": status, "id": image_id, **fields},
        )


def delete_image(image_id: str) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM images WHERE id = ?", (image_id,))


# ---- downloads ---------------------------------------------------------------

def create_download(image_id: str, gid: str | None, total_bytes: int | None) -> int:
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO downloads (image_id, gid, total_bytes, state, started_at) "
            "VALUES (?, ?, ?, 'active', datetime('now'))",
            (image_id, gid, total_bytes),
        )
        return cur.lastrowid


def update_download_progress(download_id: int, *, progress_bytes: int, speed_bps: int, state: str | None = None) -> None:
    with transaction() as conn:
        if state:
            conn.execute(
                "UPDATE downloads SET progress_bytes=?, speed_bps=?, state=? WHERE id=?",
                (progress_bytes, speed_bps, state, download_id),
            )
        else:
            conn.execute(
                "UPDATE downloads SET progress_bytes=?, speed_bps=? WHERE id=?",
                (progress_bytes, speed_bps, download_id),
            )


def finish_download(download_id: int, *, state: str, error: str | None = None) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE downloads SET state=?, error=?, finished_at=datetime('now') WHERE id=?",
            (state, error, download_id),
        )


def get_download(download_id: int) -> dict[str, Any] | None:
    row = db().execute("SELECT * FROM downloads WHERE id = ?", (download_id,)).fetchone()
    return dict(row) if row else None


def latest_download_for_image(image_id: str) -> dict[str, Any] | None:
    row = db().execute(
        "SELECT * FROM downloads WHERE image_id = ? ORDER BY id DESC LIMIT 1", (image_id,)
    ).fetchone()
    return dict(row) if row else None


def find_download_by_gid(gid: str) -> dict[str, Any] | None:
    row = db().execute("SELECT * FROM downloads WHERE gid = ? ORDER BY id DESC LIMIT 1", (gid,)).fetchone()
    return dict(row) if row else None


# ---- boots (append-only history of what was scheduled) -------------------------

def create_boot(image_id: str, adapter: str, method: str) -> int:
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO boots (image_id, adapter, method) VALUES (?, ?, ?)",
            (image_id, adapter, method),
        )
        return cur.lastrowid


# ---- settings ------------------------------------------------------------------

def get_setting(key: str, default: str | None = None) -> str | None:
    row = db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


# ---- networks (recently-used SSIDs; NOT credential storage) --------------------

def touch_network(ssid: str) -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT INTO networks (ssid, last_connected_at) VALUES (?, datetime('now')) "
            "ON CONFLICT(ssid) DO UPDATE SET last_connected_at=datetime('now')",
            (ssid,),
        )


def recent_networks(limit: int = 10) -> list[str]:
    rows = db().execute(
        "SELECT ssid FROM networks ORDER BY last_connected_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [r["ssid"] for r in rows]
