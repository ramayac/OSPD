"""Unit tests for review exports, history archiving, and reporting."""

from pathlib import Path
import sqlite3
import pytest

from ospd.db import Database
from ospd.date_fixer import DateDiscrepancy, export_discrepancies_csv
from ospd.reporter import generate_reports
from ospd.review import export_review_csvs, generate_html_contact_sheet


def test_review_csv_export_and_history(tmp_path: Path):
    manifest_dir = tmp_path / "OSPD"
    manifest_dir.mkdir()
    db = Database(manifest_dir / "test.db")

    with db.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO runs (run_id, kind, started_at, status) VALUES ('run010', 'scan', '2026-11-07', 'ok')")
        cursor.execute(
            """
            INSERT INTO folders (folder_path, folder_class, target_category, item_count, notes)
            VALUES ('Wallpapers', 'Wallpapers', 'Wallpapers', 12, 'nature')
            """
        )
        cursor.execute(
            """
            INSERT INTO resources (
                original_path, current_path, parent_folder, top_folder, folder_class,
                kind, ext, size_bytes, is_lowres_candidate, lowres_match, lowres_decision,
                mtime_trusted, status
            ) VALUES
            ('icon.png', 'icon.png', '.', '.', 'CameraDump', 'image', '.png', 4000, 1, 'size', 'pending', 1, 'present')
            """
        )
        cursor.execute(
            """
            INSERT INTO dup_groups (group_id, match_type, confidence, detected_run, decision)
            VALUES (1, 'same_name_cross_folder', 'high', 'run010', 'pending')
            """
        )
        cursor.execute(
            """
            INSERT INTO dup_members (group_id, resource_id, proposed_role)
            VALUES (1, 1, 'duplicate')
            """
        )

        f_csv, l_csv, d_csv = export_review_csvs(conn, manifest_dir, run_id="run010")

    # Verify canonical files exist
    assert f_csv.exists()
    assert l_csv.exists()
    assert d_csv.exists()

    # Verify header has run_id
    header = f_csv.read_text().splitlines()[0]
    assert header.startswith("run_id,")
    row = f_csv.read_text().splitlines()[1]
    assert row.startswith("run010,")

    # Verify history archive was created
    history_dir = manifest_dir / "review" / "history"
    assert (history_dir / "run010_folders.csv").exists()
    assert (history_dir / "run010_lowres.csv").exists()
    assert (history_dir / "run010_duplicates.csv").exists()


def test_contact_sheet_and_history(tmp_path: Path):
    manifest_dir = tmp_path / "OSPD"
    manifest_dir.mkdir()
    pics_root = tmp_path / "Pictures"
    pics_root.mkdir()
    (pics_root / "test.jpg").write_text("data")

    db = Database(manifest_dir / "test.db")
    with db.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO runs (run_id, kind, started_at, status) VALUES ('run010', 'scan', '2026-11-07', 'ok')")
        cursor.execute(
            """
            INSERT INTO resources (
                original_path, current_path, parent_folder, top_folder, folder_class,
                kind, ext, size_bytes, width, height, resolved_dt, mtime_trusted, status
            ) VALUES
            ('test.jpg', 'test.jpg', '.', '.', 'CameraDump', 'image', '.jpg', 5000, 1920, 1080, '2024-01-01', 1, 'present')
            """
        )
        cursor.execute(
            """
            INSERT INTO dup_groups (group_id, match_type, confidence, detected_run, decision)
            VALUES (1, 'exact_sha256', '100%', 'run010', 'pending')
            """
        )
        cursor.execute(
            """
            INSERT INTO dup_members (group_id, resource_id, proposed_role)
            VALUES (1, 1, 'keep')
            """
        )

        html_p = generate_html_contact_sheet(conn, pics_root, manifest_dir, run_id="run010")

    assert html_p.exists()
    html_content = html_p.read_text()
    assert "Run: run010" in html_content

    # History archive
    history_html = manifest_dir / "review" / "history" / "run010_duplicates.html"
    assert history_html.exists()


def test_date_discrepancies_export_with_history(tmp_path: Path):
    manifest_dir = tmp_path / "OSPD"
    manifest_dir.mkdir()
    csv_out = manifest_dir / "review" / "date_discrepancies.csv"

    from datetime import datetime
    discs = [
        DateDiscrepancy(
            resource_id=42,
            rel_path="photo.jpg",
            pattern="full_timestamp_sec",
            filename_dt=datetime(2021, 5, 16, 18, 50, 0),
            confidence=0.98,
            current_mtime="2026-10-31 10:00:00",
            current_exif="None",
            discrepancy_type="mtime_mismatch",
            diff_seconds=100000.0,
        )
    ]

    export_discrepancies_csv(discs, csv_out, run_id="run010")
    assert csv_out.exists()
    lines = csv_out.read_text().splitlines()
    assert lines[0].startswith("run_id,")
    assert lines[1].startswith("run010,42,photo.jpg")

    # History copy
    hist_csv = manifest_dir / "review" / "history" / "run010_date_discrepancies.csv"
    assert hist_csv.exists()


def test_reports_generation_in_review_and_history(tmp_path: Path):
    manifest_dir = tmp_path / "OSPD"
    manifest_dir.mkdir()
    db = Database(manifest_dir / "test.db")

    with db.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO runs (run_id, kind, started_at, status) VALUES ('run010', 'scan', '2026-11-07', 'ok')")
        cursor.execute(
            """
            INSERT INTO resources (
                original_path, current_path, parent_folder, top_folder, folder_class,
                kind, ext, size_bytes, has_gps, latitude, longitude, mtime_trusted, status
            ) VALUES
            ('photo_gps.jpg', 'photo_gps.jpg', '.', '.', 'CameraDump', 'image', '.jpg', 5000, 1, 49.28, -123.12, 1, 'present'),
            ('Galería de Samsung/photo_gps.jpg', 'Galería de Samsung/photo_gps.jpg', 'Galería de Samsung', 'Galería de Samsung', 'CameraDump', 'image', '.jpg', 5000, 0, NULL, NULL, 1, 'present')
            """
        )

        md_p, html_p = generate_reports(conn, manifest_dir)

    # Reports exist in review/
    assert md_p.exists()
    assert html_p.exists()
    assert md_p == manifest_dir / "review" / "report.md"
    assert html_p == manifest_dir / "review" / "report.html"

    # Root OSPD/ does NOT have duplicated report files
    assert not (manifest_dir / "report.md").exists()
    assert not (manifest_dir / "report.html").exists()

    # History reports exist
    history_html = manifest_dir / "review" / "history" / "run010_report.html"
    history_md = manifest_dir / "review" / "history" / "run010_report.md"
    assert history_html.exists()
    assert history_md.exists()

    # Geolocation GPS stats included
    md_text = md_p.read_text()
    assert "Geolocation (GPS)" in md_text
    assert "50.0%" in md_text  # 1 out of 2 files has GPS

    # Root vs Samsung overlap stats included
    assert "Root ↔ Galería de Samsung" in md_text
    assert "Shared Basenames" in md_text
