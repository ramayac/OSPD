"""Unit tests for date discrepancy detection and high-confidence date restoration."""

import os
from datetime import datetime
from pathlib import Path
import sqlite3
import pytest

from ospd.date_fixer import (
    apply_date_fixes,
    extract_filename_date_with_confidence,
    find_date_discrepancies,
)
from ospd.db import Database


def test_three_letter_and_common_prefix_patterns():
    # 1. IMG_ prefix with camera modifiers
    dt, conf, pat = extract_filename_date_with_confidence("IMG_20200912_172100-COLLAGE.jpg")
    assert dt == datetime(2020, 9, 12, 17, 21, 0)
    assert conf >= 0.90

    dt, conf, pat = extract_filename_date_with_confidence("IMG_20221114_074140_HDR.jpg")
    assert dt == datetime(2022, 11, 14, 7, 41, 40)
    assert conf >= 0.90

    # 2. DJI_ drone video and photo
    dt, conf, pat = extract_filename_date_with_confidence("DJI_20200830_153443_152_video.mp4")
    assert dt == datetime(2020, 8, 30, 15, 34, 43)
    assert conf >= 0.90

    dt, conf, pat = extract_filename_date_with_confidence("DJI_20200830_154834_162.jpg")
    assert dt == datetime(2020, 8, 30, 15, 48, 34)
    assert conf >= 0.90

    # 3. VID_ video prefix
    dt, conf, pat = extract_filename_date_with_confidence("VID_20230715_172700.mp4")
    assert dt == datetime(2023, 7, 15, 17, 27, 0)
    assert conf >= 0.90

    # 4. PXL_ Google Pixel with milliseconds and suffixes
    dt, conf, pat = extract_filename_date_with_confidence("PXL_20260528_182630085.jpg")
    assert dt == datetime(2026, 5, 28, 18, 26, 30)
    assert conf >= 0.90

    dt, conf, pat = extract_filename_date_with_confidence("PXL_20201025_230609623.NIGHT.jpg")
    assert dt == datetime(2020, 10, 25, 23, 6, 9)
    assert conf >= 0.90

    # 5. Other camera modes & tools
    dt, conf, pat = extract_filename_date_with_confidence("PANO_20200828_164657.jpg")
    assert dt == datetime(2020, 8, 28, 16, 46, 57)
    assert conf >= 0.90

    dt, conf, pat = extract_filename_date_with_confidence("hyperlapse-20200422-110643-16x.mp4")
    assert dt == datetime(2020, 4, 22, 11, 6, 43)
    assert conf >= 0.90

    dt, conf, pat = extract_filename_date_with_confidence("SmartSelect_20260521_172603_WhatsApp.jpg")
    assert dt == datetime(2026, 5, 21, 17, 26, 3)
    assert conf >= 0.90

    dt, conf, pat = extract_filename_date_with_confidence("AISelect_20260612_201333_Chrome.jpg")
    assert dt == datetime(2026, 6, 12, 20, 13, 33)
    assert conf >= 0.90

    # VideoCapture pattern
    dt, conf, pat = extract_filename_date_with_confidence("VideoCapture_20210122-160351.jpg")
    assert dt == datetime(2021, 1, 22, 16, 3, 51)
    assert conf >= 0.90

    # Hyphen-separated full timestamp (VIDEO-YYYY-MM-DD-HH-MM-SS)
    dt, conf, pat = extract_filename_date_with_confidence("VIDEO-2026-02-14-11-37-45.mp4")
    assert dt == datetime(2026, 2, 14, 11, 37, 45)
    assert conf >= 0.90
    assert pat == "full_timestamp_sec"

    # Hyphen-separated with milliseconds
    dt, conf, pat = extract_filename_date_with_confidence("2020-12-11-14-55-20-003.jpg")
    assert dt == datetime(2020, 12, 11, 14, 55, 20)
    assert conf >= 0.90

    # Tezza photo editing app format (Tezza_YYYY_MM_DD_HHMMSSmmm)
    dt, conf, pat = extract_filename_date_with_confidence("Tezza_2021_04_02_160615101.jpg")
    assert dt == datetime(2021, 4, 2, 16, 6, 15)
    assert conf >= 0.90
    assert pat == "full_timestamp_sec"

    # 6. Prefixed Unix timestamp (WA_...)
    dt, conf, pat = extract_filename_date_with_confidence("WA_1787945670490.mp4")
    assert dt is not None
    assert conf >= 0.90
    assert dt.year == 2026

    # 7. Security Camera filenames ([MAC]_YYYYMMDDHHMMSSmmm_camX)
    dt, conf, pat = extract_filename_date_with_confidence("482254B3B00C_20250916130700244_cam0.jpeg")
    assert dt == datetime(2025, 9, 16, 13, 7, 0)
    assert conf >= 0.90
    assert pat == "security_cam_full_sec"

    dt, conf, pat = extract_filename_date_with_confidence("CCBABD4D4F4D_20260403084645822_cam0.jpeg")
    assert dt == datetime(2026, 4, 3, 8, 46, 45)
    assert conf >= 0.90

    # 8. Exported/edited filename with embedded timestamp
    dt, conf, pat = extract_filename_date_with_confidence("IMG_3077_exported_1333_1780865313420.heic")
    assert dt is not None
    assert conf >= 0.90

    # 9. Facebook download timestamp (FB_IMG_...)
    dt, conf, pat = extract_filename_date_with_confidence("FB_IMG_1588535572343.jpg")
    assert dt is not None
    assert conf >= 0.90

    # 10. Unix second timestamp (EMJS-...)
    dt, conf, pat = extract_filename_date_with_confidence("EMJS-1750829312.png")
    assert dt is not None
    assert conf >= 0.90

    # 11. Copy with duplicate index suffix like (0)
    dt, conf, pat = extract_filename_date_with_confidence("20261012_175659(0).heic")
    assert dt == datetime(2026, 10, 12, 17, 56, 59)
    assert conf >= 0.90


def test_confidence_scoring():
    # 90%+ confidence: full precision timestamps with seconds
    dt, conf, pat = extract_filename_date_with_confidence("20250419_090530.heic")
    assert dt == datetime(2025, 4, 19, 9, 5, 30)
    assert conf >= 0.90
    assert pat == "full_timestamp_sec"

    dt, conf, pat = extract_filename_date_with_confidence("Screenshot_20261010_215825_UGREEN.jpg")
    assert dt == datetime(2026, 10, 10, 21, 58, 25)
    assert conf >= 0.90

    dt, conf, pat = extract_filename_date_with_confidence("1790912862896_1.jpg")
    assert dt is not None
    assert conf >= 0.90
    assert pat == "unix_timestamp_ms"

    # Lower confidence (< 90%): date-only without hours/mins/secs
    dt, conf, pat = extract_filename_date_with_confidence("IMG-20231215-WA0004.jpg")
    assert dt == datetime(2023, 12, 15, 12, 0, 0)
    assert conf < 0.90

    dt, conf, pat = extract_filename_date_with_confidence("holiday-2018-07-20.jpg")
    assert conf < 0.90


def test_find_discrepancies_and_apply_fix(tmp_path: Path):
    root = tmp_path / "Pictures"
    root.mkdir()
    manifest_dir = root / "OSPD"
    manifest_dir.mkdir()

    # Create dummy photo whose filename says 2024-05-10 14:30:00
    # But current filesystem mtime is set to 2026-10-31 (the untrusted move window)
    photo = root / "20240510_143000.jpg"
    photo.write_text("dummy")

    bad_mtime = datetime(2026, 10, 31, 12, 0, 0).timestamp()
    os.utime(str(photo), (bad_mtime, bad_mtime))

    db = Database(manifest_dir / "photo_sort.db")
    with db.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO resources (
                original_path, current_path, parent_folder, top_folder, folder_class,
                kind, ext, size_bytes, mtime, mtime_trusted, resolved_dt, status
            ) VALUES
            ('20240510_143000.jpg', '20240510_143000.jpg', '.', '.', 'CameraDump',
             'image', '.jpg', 5, '2026-10-31 12:00:00', 0, '2026-10-31 12:00:00', 'present')
            """
        )

    # 1. Detect discrepancy
    conn = db.get_connection()
    discs = find_date_discrepancies(conn, root, min_confidence=0.90)
    assert len(discs) == 1
    assert discs[0].filename_dt == datetime(2024, 5, 10, 14, 30, 0)
    assert "mtime_in_untrusted_move_window" in discs[0].discrepancy_type

    # 2. Apply fix
    ok_c, err_c, csv_path = apply_date_fixes(
        conn=conn,
        pictures_root=root,
        manifest_dir=manifest_dir,
        run_id="run001",
        min_confidence=0.90,
        ignore_insync=True,
    )
    conn.close()

    assert ok_c == 1
    assert err_c == 0
    assert csv_path.exists()

    # Verify filesystem timestamp was updated on disk
    stat_after = photo.stat()
    mtime_after = datetime.fromtimestamp(stat_after.st_mtime)
    assert mtime_after == datetime(2024, 5, 10, 14, 30, 0)

    # Verify database was updated
    with db.get_connection() as c2:
        cursor = c2.cursor()
        cursor.execute("SELECT mtime, mtime_trusted, date_source FROM resources WHERE original_path = '20240510_143000.jpg'")
        row = cursor.fetchone()
        assert row["mtime"] == "2024-05-10 14:30:00"
        assert row["mtime_trusted"] == 1
        assert row["date_source"] == "name_restored"
