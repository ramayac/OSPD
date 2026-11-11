"""Unit tests for executor moves, preflight checks, and rollback."""

from pathlib import Path
import sqlite3
import pytest

from ospd.db import Database
from ospd.executor import Executor


def test_executor_moves_and_rollback(tmp_path: Path):
    root = tmp_path / "Pictures"
    root.mkdir()
    (root / "folder1").mkdir()
    (root / "folder1" / "fileA.txt").write_text("hello")
    (root / "loose.txt").write_text("world")

    db = Database(root / "_Manifest" / "db" / "test.db")
    with db.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO runs (run_id, kind, started_at, status)
            VALUES ('run001', 'plan', '2026-11-07', 'ok')
            """
        )
        cursor.execute(
            """
            INSERT INTO operations (run_id, seq, op_type, src, dst, category, reason, status)
            VALUES
            ('run001', 1, 'folder', 'folder1', 'Albums/folder1', 'Album', 'test', 'planned'),
            ('run001', 2, 'file', 'loose.txt', 'Gallery/loose.txt', 'Gallery', 'test', 'planned')
            """
        )

    exec_inst = Executor(root, db.get_connection())

    # Preflight check with ignore_insync=True for test sandbox
    ok, _ = exec_inst.preflight_check(ignore_insync=True)
    assert ok is True

    # Execute moves
    ok_c, skip_c, err_c, csv_path = exec_inst.execute_planned(
        run_id="run001",
        ignore_insync=True,
    )
    assert ok_c == 2
    assert skip_c == 0
    assert err_c == 0
    assert csv_path.exists()

    # Verify moved paths
    assert not (root / "folder1").exists()
    assert (root / "Albums" / "folder1" / "fileA.txt").exists()
    assert not (root / "loose.txt").exists()
    assert (root / "Gallery" / "loose.txt").exists()

    # Rollback run
    undone_c, err_undo, undo_csv = exec_inst.rollback_run(
        target_run_id="run001",
        undo_run_id="run002",
        ignore_insync=True,
    )
    assert undone_c == 2
    assert err_undo == 0
    assert undo_csv.exists()

    # Verify restored state
    assert (root / "folder1" / "fileA.txt").exists()
    assert (root / "loose.txt").exists()


def test_executor_prunes_empty_source_folders(tmp_path: Path):
    root = tmp_path / "Pictures"
    root.mkdir()
    camera_dir = root / "Galería de Samsung" / "DCIM" / "Camera"
    camera_dir.mkdir(parents=True)
    (camera_dir / "20210516_185000.jpg").write_text("photo data")

    db = Database(root / "OSPD" / "test.db")
    with db.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO runs (run_id, kind, started_at, status)
            VALUES ('run001', 'plan', '2026-11-07', 'ok')
            """
        )
        cursor.execute(
            """
            INSERT INTO operations (run_id, seq, op_type, src, dst, category, reason, status)
            VALUES
            ('run001', 1, 'file',
             'Galería de Samsung/DCIM/Camera/20210516_185000.jpg',
             'Gallery/2021/2021-05/20210516_185000.jpg',
             'Gallery', 'test', 'planned')
            """
        )

    exec_inst = Executor(root, db.get_connection())
    ok_c, _, err_c, _ = exec_inst.execute_planned(run_id="run001", ignore_insync=True)
    assert ok_c == 1
    assert err_c == 0

    # Target file exists
    assert (root / "Gallery" / "2021" / "2021-05" / "20210516_185000.jpg").exists()

    # Empty source tree pruned completely
    assert not (root / "Galería de Samsung").exists()
    assert not camera_dir.exists()
    assert not (root / "Albums" / "folder1").exists()
    assert not (root / "Gallery" / "loose.txt").exists()
