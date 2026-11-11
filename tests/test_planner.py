"""Unit tests for operation planning, renaming, and Live Photo pairing."""

from pathlib import Path
import sqlite3
import pytest

from ospd.db import Database
from ospd.planner import format_gallery_filename, plan_operations


def test_format_gallery_filename():
    fn = format_gallery_filename("2024-08-25 12:28:30", ".heic")
    assert fn == "20240825_122830.heic"

    fn_collision = format_gallery_filename("2024-08-25 12:28:30", ".jpg", index=1)
    assert fn_collision == "20240825_122830_1.jpg"


def test_planner_moves(tmp_path: Path):
    db_file = tmp_path / "test_planner.db"
    db = Database(db_file)

    with db.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO runs (run_id, kind, started_at, status)
            VALUES ('run001', 'plan', '2026-11-07', 'running')
            """
        )
        # 1. Whole-folder entry
        cursor.execute(
            """
            INSERT INTO folders (folder_path, folder_class, target_category, item_count)
            VALUES ('Madrid - 2018', 'Album', 'Albums', 5)
            """
        )
        # 2. Camera dump files: Live photo pair (HEIC + MOV)
        cursor.execute(
            """
            INSERT INTO resources (
                original_path, current_path, parent_folder, top_folder, folder_class,
                kind, ext, size_bytes, mtime_trusted, resolved_dt, date_source, live_pair_id, status
            ) VALUES
            ('IMG_4542.HEIC', 'IMG_4542.HEIC', '.', '.', 'CameraDump', 'image', '.heic', 2000, 1,
             '2024-08-25 12:28:30', 'exif', 'IMG_4542', 'present'),
            ('IMG_4542.MOV', 'IMG_4542.MOV', '.', '.', 'CameraDump', 'video', '.mov', 8000, 1,
             '2024-08-25 12:28:30', 'video', 'IMG_4542', 'present')
            """
        )

        ops = plan_operations(conn, "run001")
        assert len(ops) == 3

        # Folder op
        folder_op = next(o for o in ops if o.op_type == "folder")
        assert folder_op.src == "Madrid - 2018"
        assert folder_op.dst == "Albums/Madrid - 2018"

        # Live photo pair should share the stem
        file_ops = [o for o in ops if o.op_type == "file"]
        stems = {Path(o.dst).stem for o in file_ops}
        assert len(stems) == 1
        assert "20240825_122830" in list(stems)[0]
