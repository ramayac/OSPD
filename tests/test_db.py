"""Unit tests for SQLite database operations and snapshots."""

from pathlib import Path
import sqlite3
import pytest

from ospd.db import Database


def test_db_lifecycle(tmp_path: Path):
    db_file = tmp_path / "test_photo_sort.db"
    db = Database(db_file)

    # Verify journal mode
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0].lower()
        assert mode == "delete"

    # Verify run recording
    db.record_run_start("run001", "scan", args="--limit 10")
    db.record_run_finish("run001", "ok", ops_planned=10, ops_ok=10, files_before=10)

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM runs WHERE run_id = 'run001'")
        row = cursor.fetchone()
        assert row["status"] == "ok"
        assert row["ops_ok"] == 10

    # Verify snapshot creation
    snapshot = db.create_snapshot("run001")
    assert snapshot.exists()
    assert snapshot.stat().st_size > 0
