"""Database layer for OSPD (Organize and Sorting Photo Dump).

Uses standard library sqlite3 with:
- Journal mode = DELETE (no -wal / -shm residue in synced folders)
- Snapshot backups on exit/commit
- Strong foreign keys and transactional updates
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,                -- 'scan', 'plan', 'test', 'apply', 'undo', 'review-import'
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,              -- 'running', 'ok', 'failed', 'aborted'
    args TEXT,
    undo_of_run_id TEXT,
    ops_planned INTEGER DEFAULT 0,
    ops_ok INTEGER DEFAULT 0,
    ops_skipped INTEGER DEFAULT 0,
    ops_failed INTEGER DEFAULT 0,
    files_before INTEGER,
    bytes_before INTEGER,
    files_after INTEGER,
    bytes_after INTEGER,
    insync_running INTEGER DEFAULT 0,
    insyncdl_found INTEGER DEFAULT 0,
    dup_detection_complete INTEGER DEFAULT 0,
    csv_log_path TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS folders (
    folder_path TEXT PRIMARY KEY,       -- relative to Pictures root, e.g. 'Madrid - 2018'
    folder_class TEXT NOT NULL,         -- 'Album', 'Wallpapers', 'Art', 'LowRes', 'CameraDump', 'Screenshots', 'Ignore'
    target_category TEXT NOT NULL,      -- 'Albums', 'Wallpapers', 'Art', 'Unsorted/LowRes', 'Gallery', etc.
    item_count INTEGER DEFAULT 0,
    lowres_count INTEGER DEFAULT 0,
    lowres_ratio REAL DEFAULT 0.0,
    decision TEXT DEFAULT 'pending',    -- 'pending', 'approved', 'custom'
    notes TEXT
);

CREATE TABLE IF NOT EXISTS resources (
    resource_id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_path TEXT NOT NULL UNIQUE,  -- relative to Pictures root
    current_path TEXT NOT NULL,          -- updated on move
    parent_folder TEXT NOT NULL,
    top_folder TEXT NOT NULL,
    folder_class TEXT NOT NULL,
    kind TEXT NOT NULL,                  -- 'image', 'video', 'sidecar', 'other'
    ext TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT,
    width INTEGER,
    height INTEGER,
    camera_make TEXT,
    camera_model TEXT,
    exif_dt TEXT,
    json_dt TEXT,
    name_dt TEXT,
    video_dt TEXT,
    mtime TEXT,
    mtime_trusted INTEGER NOT NULL,      -- 0 if in Oct 30 - Nov 1 2026 window, else 1
    resolved_dt TEXT,
    date_source TEXT,                    -- 'exif', 'json', 'name', 'video', 'mtime', 'none'
    date_confidence TEXT,                -- 'high', 'medium', 'low', 'none'
    is_lowres_candidate INTEGER DEFAULT 0,
    lowres_match TEXT,                   -- 'none', 'size', 'res', 'both'
    lowres_decision TEXT DEFAULT 'pending', -- 'pending', 'LowRes', 'keep'
    proposed_category TEXT,
    proposed_dst TEXT,
    live_pair_id TEXT,                   -- links HEIC + MOV with same stem
    has_gps INTEGER DEFAULT 0,           -- 1 if GPS latitude/longitude present
    latitude REAL,
    longitude REAL,
    status TEXT DEFAULT 'present',       -- 'present', 'moved', 'missing'
    issues TEXT,
    first_seen_run TEXT,
    last_seen_run TEXT
);

CREATE TABLE IF NOT EXISTS dup_groups (
    group_id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_type TEXT NOT NULL,            -- 'exact_sha256', 'same_exif_time_camera', 'heic_jpg_pair', 'name_variant'
    confidence TEXT NOT NULL,            -- '100%', 'high', 'medium'
    detected_run TEXT NOT NULL,
    decision TEXT DEFAULT 'pending',     -- 'pending', 'approved', 'rejected'
    reviewed_at TEXT,
    reviewer_note TEXT
);

CREATE TABLE IF NOT EXISTS dup_members (
    group_id INTEGER NOT NULL REFERENCES dup_groups(group_id) ON DELETE CASCADE,
    resource_id INTEGER NOT NULL REFERENCES resources(resource_id) ON DELETE CASCADE,
    proposed_role TEXT NOT NULL,         -- 'keep', 'duplicate'
    final_role TEXT,                     -- 'keep', 'duplicate'
    PRIMARY KEY (group_id, resource_id)
);

CREATE TABLE IF NOT EXISTS operations (
    op_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    seq INTEGER NOT NULL,
    op_type TEXT NOT NULL,               -- 'file', 'folder', 'mkdir'
    resource_id INTEGER REFERENCES resources(resource_id),
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    category TEXT NOT NULL,
    reason TEXT NOT NULL,
    date_used TEXT,
    date_source TEXT,
    files_inside INTEGER,
    bytes INTEGER,
    status TEXT NOT NULL,                -- 'planned', 'dryrun-ok', 'dryrun-fail', 'started', 'ok', 'skipped-conflict', 'skipped-missing', 'error', 'undone'
    error_msg TEXT,
    started_at TEXT,
    finished_at TEXT,
    undone_by_run TEXT
);

CREATE INDEX IF NOT EXISTS idx_resources_sha256 ON resources(sha256);
CREATE INDEX IF NOT EXISTS idx_resources_current_path ON resources(current_path);
CREATE INDEX IF NOT EXISTS idx_resources_top_folder ON resources(top_folder);
CREATE INDEX IF NOT EXISTS idx_operations_run_seq ON operations(run_id, seq);
"""


class Database:
    """Encapsulates SQLite access and snapshotting."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir = self.db_path.parent / "snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = DELETE;")
        conn.execute("PRAGMA synchronous = FULL;")
        return conn

    def init_schema(self) -> None:
        with self.get_connection() as conn:
            conn.executescript(SCHEMA)
            # Safe column migration for new fields
            for col, ctype in (("has_gps", "INTEGER DEFAULT 0"), ("latitude", "REAL"), ("longitude", "REAL")):
                try:
                    conn.execute(f"ALTER TABLE resources ADD COLUMN {col} {ctype};")
                except sqlite3.OperationalError:
                    pass

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def create_snapshot(self, run_id: str) -> Path:
        """Create a point-in-time copy of the DB file."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = self.snapshots_dir / f"photo_sort_{run_id}_{ts}.db"
        with self.get_connection() as src_conn:
            dest_conn = sqlite3.connect(str(dest))
            src_conn.backup(dest_conn)
            dest_conn.close()
        return dest

    def record_run_start(
        self,
        run_id: str,
        kind: str,
        args: str = "",
        insync_running: bool = False,
        insyncdl_found: bool = False,
        undo_of_run_id: Optional[str] = None,
        csv_log_path: Optional[str] = None,
        notes: str = "",
    ) -> None:
        now = datetime.now().isoformat()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    run_id, kind, started_at, status, args, undo_of_run_id,
                    insync_running, insyncdl_found, csv_log_path, notes
                ) VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    kind,
                    now,
                    args,
                    undo_of_run_id,
                    int(insync_running),
                    int(insyncdl_found),
                    csv_log_path,
                    notes,
                ),
            )

    def record_run_finish(
        self,
        run_id: str,
        status: str,
        ops_planned: int = 0,
        ops_ok: int = 0,
        ops_skipped: int = 0,
        ops_failed: int = 0,
        files_before: Optional[int] = None,
        bytes_before: Optional[int] = None,
        files_after: Optional[int] = None,
        bytes_after: Optional[int] = None,
        dup_complete: bool = False,
        notes: Optional[str] = None,
    ) -> None:
        now = datetime.now().isoformat()
        with self.transaction() as conn:
            conn.execute(
                """
                UPDATE runs SET
                    finished_at = ?,
                    status = ?,
                    ops_planned = ?,
                    ops_ok = ?,
                    ops_skipped = ?,
                    ops_failed = ?,
                    files_before = COALESCE(?, files_before),
                    bytes_before = COALESCE(?, bytes_before),
                    files_after = COALESCE(?, files_after),
                    bytes_after = COALESCE(?, bytes_after),
                    dup_detection_complete = MAX(dup_detection_complete, ?),
                    notes = CASE WHEN ? IS NOT NULL THEN ? ELSE notes END
                WHERE run_id = ?
                """,
                (
                    now,
                    status,
                    ops_planned,
                    ops_ok,
                    ops_skipped,
                    ops_failed,
                    files_before,
                    bytes_before,
                    files_after,
                    bytes_after,
                    int(dup_complete),
                    notes,
                    notes,
                    run_id,
                ),
            )
        try:
            self.create_snapshot(run_id)
        except Exception:
            pass
