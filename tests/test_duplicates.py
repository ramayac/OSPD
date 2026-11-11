"""Unit tests for duplicate candidate grouping and precedence rules."""

from pathlib import Path
import sqlite3
import pytest

from ospd.db import Database
from ospd.duplicates import bulk_approve_exact_duplicates, find_duplicate_groups


def test_duplicate_precedence_and_approval(tmp_path: Path):
    db_file = tmp_path / "test_dup.db"
    db = Database(db_file)

    with db.transaction() as conn:
        cursor = conn.cursor()
        # Insert two files with identical SHA256: one in Album, one in CameraDump
        cursor.execute(
            """
            INSERT INTO resources (
                original_path, current_path, parent_folder, top_folder, folder_class,
                kind, ext, size_bytes, sha256, mtime, mtime_trusted, status
            ) VALUES
            ('Madrid - 2018/photo1.jpg', 'Madrid - 2018/photo1.jpg', 'Madrid - 2018', 'Madrid - 2018', 'Album',
             'image', '.jpg', 5000, 'hash123', '2024-01-01', 1, 'present'),
            ('Camera Roll/photo1_copy.jpg', 'Camera Roll/photo1_copy.jpg', 'Camera Roll', 'Camera Roll', 'CameraDump',
             'image', '.jpg', 5000, 'hash123', '2024-01-01', 1, 'present')
            """
        )

        # Detect groups
        groups = find_duplicate_groups(conn, "run001")
        assert groups == 1

        # Check proposed roles: Album copy should be 'keep', CameraDump copy 'duplicate'
        cursor.execute(
            """
            SELECT dm.proposed_role, r.folder_class
            FROM dup_members dm
            JOIN resources r ON dm.resource_id = r.resource_id
            ORDER BY dm.proposed_role ASC
            """
        )
        rows = cursor.fetchall()
        assert rows[0]["proposed_role"] == "duplicate"
        assert rows[0]["folder_class"] == "CameraDump"
        assert rows[1]["proposed_role"] == "keep"
        assert rows[1]["folder_class"] == "Album"

        # Bulk approve should fail without a completed scan
        ok, msg, count = bulk_approve_exact_duplicates(conn)
        assert ok is False

        # Record completed scan
        cursor.execute(
            """
            INSERT INTO runs (run_id, kind, started_at, status, dup_detection_complete)
            VALUES ('run001', 'scan', '2026-11-07', 'ok', 1)
            """
        )

        # Bulk approve should now succeed
        ok, msg, count = bulk_approve_exact_duplicates(conn)
        assert ok is True
        assert count == 1


def test_same_name_cross_folder_matcher(tmp_path: Path):
    db_file = tmp_path / "test_cross_folder.db"
    db = Database(db_file)

    with db.transaction() as conn:
        cursor = conn.cursor()
        # 1. Test Quality Precedence between CameraDumps (larger file wins)
        cursor.execute(
            """
            INSERT INTO resources (
                original_path, current_path, parent_folder, top_folder, folder_class,
                kind, ext, size_bytes, sha256, resolved_dt, mtime, mtime_trusted, status
            ) VALUES
            ('20210516_185000.jpg', '20210516_185000.jpg', '.', '.', 'CameraDump',
             'image', '.jpg', 2000000, NULL, '2021-05-16 18:50:00', '2024-01-01', 1, 'present'),
            ('Galería de Samsung/20210516_185000.jpg', 'Galería de Samsung/20210516_185000.jpg',
             'Galería de Samsung', 'Galería de Samsung', 'CameraDump',
             'image', '.jpg', 2050000, NULL, '2021-05-16 18:50:00', '2024-01-01', 1, 'present')
            """
        )

        groups = find_duplicate_groups(conn, "run002")
        assert groups == 1

        cursor.execute(
            """
            SELECT dg.match_type, dm.proposed_role, r.current_path, r.size_bytes
            FROM dup_members dm
            JOIN dup_groups dg ON dm.group_id = dg.group_id
            JOIN resources r ON dm.resource_id = r.resource_id
            ORDER BY r.size_bytes DESC
            """
        )
        rows = cursor.fetchall()
        assert rows[0]["match_type"] == "same_name_cross_folder"
        # Larger file (2050000) is KEEP
        assert rows[0]["proposed_role"] == "keep"
        assert rows[0]["size_bytes"] == 2050000
        # Smaller file (2000000) is DUPLICATE
        assert rows[1]["proposed_role"] == "duplicate"
        assert rows[1]["size_bytes"] == 2000000

        # 2. Test Album Precedence over loose file (Album copy always wins)
        cursor.execute(
            """
            INSERT INTO resources (
                original_path, current_path, parent_folder, top_folder, folder_class,
                kind, ext, size_bytes, sha256, resolved_dt, mtime, mtime_trusted, status
            ) VALUES
            ('MyCare TICA/event.jpg', 'MyCare TICA/event.jpg', 'MyCare TICA', 'MyCare TICA', 'Album',
             'image', '.jpg', 1000000, NULL, '2024-10-15 10:00:00', '2024-10-15', 1, 'present'),
            ('event.jpg', 'event.jpg', '.', '.', 'CameraDump',
             'image', '.jpg', 1500000, NULL, '2024-10-15 10:00:00', '2024-10-15', 1, 'present')
            """
        )

        groups_after = find_duplicate_groups(conn, "run003")
        # Should now have 2 groups total
        assert groups_after == 2

        cursor.execute(
            """
            SELECT dm.proposed_role, r.current_path, r.folder_class
            FROM dup_members dm
            JOIN resources r ON dm.resource_id = r.resource_id
            WHERE r.current_path LIKE '%event.jpg'
            """
        )
        event_rows = cursor.fetchall()
        for r in event_rows:
            if r["folder_class"] == "Album":
                assert r["proposed_role"] == "keep"
            else:
                assert r["proposed_role"] == "duplicate"
