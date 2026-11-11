"""Date discrepancy detector and restorer based on high-confidence filename timestamps."""

from __future__ import annotations

import csv
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ospd.classification import is_ignored_path
from ospd.db import Database
from ospd.executor import generate_run_csv_path, is_insync_running
from ospd.metadata import parse_datetime_str


@dataclass
class DateDiscrepancy:
    resource_id: Optional[int]
    rel_path: str
    pattern: str
    filename_dt: datetime
    confidence: float
    current_mtime: str
    current_exif: Optional[str]
    discrepancy_type: str
    diff_seconds: float


def extract_filename_date_with_confidence(filename: str) -> Tuple[Optional[datetime], float, str]:
    """Extract embedded timestamp from filename and score confidence (0.0 to 1.0).

    90%+ confidence requires explicit year, month, day, hour, minute, second.
    Handles:
    - Security cameras: [HEX_ID]_YYYYMMDDHHMMSS[mmm]_camX
    - 3-letter & custom prefixes: IMG_, DJI_, VID_, PXL_, Screenshot_, SmartSelect_, AISelect_, PANO_
    - Bare Samsung/Android: YYYYMMDD_HHMMSS
    - Unix millisecond timestamps: bare 179... or WA_17..., FB_IMG_15...
    - Unix second timestamps: EMJS-175...
    """
    stem = Path(filename).stem

    # 1. Security Camera format: [12-hex-ID]_YYYYMMDDHHMMSS[mmm]_camX
    m = re.search(r"[0-9a-fA-F]{12}_(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", stem)
    if m:
        try:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            hour, minute, second = int(m.group(4)), int(m.group(5)), int(m.group(6))
            if 1990 <= year <= 2040 and 1 <= month <= 12 and 1 <= day <= 31 and hour < 24 and minute < 60 and second < 60:
                dt = datetime(year, month, day, hour, minute, second)
                return dt, 0.98, "security_cam_full_sec"
        except ValueError:
            pass

    # 2. Hyphen/underscore-separated full timestamp:
    # Handles: [PREFIX[-_]]YYYY[-_]MM[-_]DD[-_ ]HH[-_:]MM[-_:]SS[optional mmm]
    # e.g. VIDEO-2026-02-14-11-37-45, 2020-12-11-14-55-20-003, fraksl_2021-11-13_23-09-56_video
    m = re.search(r"(\d{4})[-_](\d{2})[-_](\d{2})[-_ ](\d{2})[-_:](\d{2})[-_:](\d{2})(?:[-_.]\d{3})?", stem)
    if m:
        try:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            hour, minute, second = int(m.group(4)), int(m.group(5)), int(m.group(6))
            if 1990 <= year <= 2040 and 1 <= month <= 12 and 1 <= day <= 31 and hour < 24 and minute < 60 and second < 60:
                dt = datetime(year, month, day, hour, minute, second)
                return dt, 0.98, "full_timestamp_sec"
        except ValueError:
            pass

    # 2b. Mixed separated date and compact time:
    # Handles: [PREFIX[-_]]YYYY[-_]MM[-_]DD[-_]HHMMSS[optional mmm]
    # e.g. Tezza_2021_04_02_160615101.jpg
    m = re.search(r"(\d{4})[-_](\d{2})[-_](\d{2})[-_](\d{2})(\d{2})(\d{2})(?:\d{3})?", stem)
    if m:
        try:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            hour, minute, second = int(m.group(4)), int(m.group(5)), int(m.group(6))
            if 1990 <= year <= 2040 and 1 <= month <= 12 and 1 <= day <= 31 and hour < 24 and minute < 60 and second < 60:
                dt = datetime(year, month, day, hour, minute, second)
                return dt, 0.98, "full_timestamp_sec"
        except ValueError:
            pass

    # 2c. Instasize 2-digit year pattern: instasize_YYMMDDHHMMSS
    m = re.search(r"instasize_(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", stem, re.I)
    if m:
        try:
            y = 2000 + int(m.group(1))
            mo, d = int(m.group(2)), int(m.group(3))
            h, mi, s = int(m.group(4)), int(m.group(5)), int(m.group(6))
            if 2000 <= y <= 2040 and 1 <= mo <= 12 and 1 <= d <= 31 and h < 24 and mi < 60 and s < 60:
                dt = datetime(y, mo, d, h, mi, s)
                return dt, 0.95, "full_timestamp_sec"
        except ValueError:
            pass

    # 3. Compact timestamp with optional prefix and optional milliseconds:
    # Matches: [PREFIX_]YYYYMMDD[-_]HHMMSS[mmm][SUFFIX]
    # e.g. VideoCapture_20210122-160351, IMG_20200815_123456, 20210122_160351
    m = re.search(r"(?:^|[a-zA-Z0-9]{2,20}[-_])(\d{4})(\d{2})(\d{2})[-_](\d{2})(\d{2})(\d{2})(?:\d{3})?", stem)
    if m:
        try:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            hour, minute, second = int(m.group(4)), int(m.group(5)), int(m.group(6))
            if 1990 <= year <= 2040 and 1 <= month <= 12 and 1 <= day <= 31 and hour < 24 and minute < 60 and second < 60:
                dt = datetime(year, month, day, hour, minute, second)
                # 98% confidence: explicit full-precision second timestamp
                return dt, 0.98, "full_timestamp_sec"
        except ValueError:
            pass

    # 3. Unix timestamp in milliseconds (bare or prefixed, e.g. WA_178..., FB_IMG_158..., 17909... or IMG_..._178...)
    m = re.search(r"(?:^|[^0-9])(1[5-9]\d{11})(?:[^0-9]|$)", stem)
    if m:
        try:
            ms = int(m.group(1))
            sec = ms / 1000.0
            dt = datetime.fromtimestamp(sec)
            if 2000 <= dt.year <= 2040:
                # 95% confidence: millisecond epoch timestamp
                return dt, 0.95, "unix_timestamp_ms"
        except (ValueError, OSError, OverflowError):
            pass

    # 4. Unix timestamp in seconds (10 digits starting with 16-19, e.g. EMJS-1750829312)
    m = re.search(r"(?:^|[^0-9])(1[6-9]\d{8})(?:[^0-9]|$)", stem)
    if m:
        try:
            sec = int(m.group(1))
            dt = datetime.fromtimestamp(sec)
            if 2000 <= dt.year <= 2040:
                return dt, 0.92, "unix_timestamp_sec"
        except (ValueError, OSError, OverflowError):
            pass

    # 5. WhatsApp date-only: YYYYMMDD without hour/min/sec -> Lower confidence (<90%)
    m = re.search(r"(?:IMG|VID)[-_](\d{4})(\d{2})(\d{2})[-_]WA", stem, re.I)
    if m:
        try:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1990 <= year <= 2040 and 1 <= month <= 12 and 1 <= day <= 31:
                return datetime(year, month, day, 12, 0, 0), 0.75, "whatsapp_date_only"
        except ValueError:
            pass

    # 6. DD-MM-YY or DD-MM-YYYY (e.g. 27-08-24.jpeg) -> Lower confidence (<90%)
    m = re.match(r"^(\d{2})[-_](\d{2})[-_](\d{2,4})$", stem)
    if m:
        try:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if y < 100:
                y += 2000 if y <= 40 else 1900
            if 1990 <= y <= 2040 and 1 <= mo <= 12 and 1 <= d <= 31:
                conf = 0.85 if d > 12 else 0.65
                return datetime(y, mo, d, 12, 0, 0), conf, "day_month_year"
        except ValueError:
            pass

    # 7. Standard date-only: YYYY-MM-DD -> Lower confidence (<90%)
    m = re.search(r"(\d{4})[-._](\d{2})[-._](\d{2})", stem)
    if m:
        try:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1990 <= year <= 2040 and 1 <= month <= 12 and 1 <= day <= 31:
                return datetime(year, month, day, 12, 0, 0), 0.70, "date_only"
        except ValueError:
            pass

    return None, 0.0, "none"


def find_date_discrepancies(
    conn: sqlite3.Connection,
    pictures_root: Path,
    min_confidence: float = 0.90,
) -> List[DateDiscrepancy]:
    """Scan files and identify discrepancies where filename has high-confidence timestamp."""
    discrepancies: List[DateDiscrepancy] = []
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT resource_id, current_path, mtime, exif_dt, mtime_trusted
        FROM resources
        WHERE status = 'present'
        """
    )
    rows = cursor.fetchall()

    for r in rows:
        rel = r["current_path"]
        fname = Path(rel).name
        fn_dt, conf, pat = extract_filename_date_with_confidence(fname)

        if not fn_dt or conf < min_confidence:
            continue

        curr_mtime_str = r["mtime"]
        curr_mtime = parse_datetime_str(curr_mtime_str)
        curr_exif_str = r["exif_dt"]
        curr_exif = parse_datetime_str(curr_exif_str) if curr_exif_str else None

        # Compare filename date against mtime and exif
        diff_sec = abs((curr_mtime - fn_dt).total_seconds()) if curr_mtime else 99999999

        # If difference is more than 60 seconds, or mtime is untrusted, or exif is mismatched
        is_mtime_diff = diff_sec > 60
        is_untrusted_mtime = not bool(r["mtime_trusted"])

        exif_diff_sec = abs((curr_exif - fn_dt).total_seconds()) if curr_exif else None
        is_exif_diff = exif_diff_sec is not None and exif_diff_sec > 120

        if is_untrusted_mtime or is_mtime_diff or is_exif_diff:
            reasons = []
            if is_untrusted_mtime:
                reasons.append("mtime_in_untrusted_move_window")
            if is_mtime_diff:
                reasons.append("mtime_mismatch")
            if is_exif_diff:
                reasons.append("exif_mismatch")

            discrepancies.append(
                DateDiscrepancy(
                    resource_id=r["resource_id"],
                    rel_path=rel,
                    pattern=pat,
                    filename_dt=fn_dt,
                    confidence=conf,
                    current_mtime=curr_mtime_str or "unknown",
                    current_exif=curr_exif_str,
                    discrepancy_type=";".join(reasons),
                    diff_seconds=diff_sec,
                )
            )

    return discrepancies


def export_discrepancies_csv(
    discrepancies: List[DateDiscrepancy],
    out_path: Path,
    run_id: Optional[str] = None,
) -> None:
    """Save discrepancies list to CSV for review, archiving run-stamped copy in history/."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    history_dir = out_path.parent / "history"
    history_dir.mkdir(parents=True, exist_ok=True)

    targets = [out_path]
    if run_id:
        targets.append(history_dir / f"{run_id}_date_discrepancies.csv")

    for target in targets:
        with open(target, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "run_id", "resource_id", "rel_path", "confidence", "filename_date",
                "current_mtime", "current_exif", "discrepancy_reasons", "diff_seconds",
            ])
            for d in discrepancies:
                writer.writerow([
                    run_id or "run000",
                    d.resource_id,
                    d.rel_path,
                    f"{d.confidence*100:.0f}%",
                    d.filename_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    d.current_mtime,
                    d.current_exif or "None",
                    d.discrepancy_type,
                    f"{d.diff_seconds:.0f}",
                ])


def apply_date_fixes(
    conn: sqlite3.Connection,
    pictures_root: Path,
    manifest_dir: Path,
    run_id: str,
    min_confidence: float = 0.90,
    ignore_insync: bool = False,
) -> Tuple[int, int, Path]:
    """Restore filesystem mtime on disk based on high-confidence filename date."""
    if not ignore_insync and is_insync_running():
        raise RuntimeError("SAFETY BLOCK: Insync process is running. Stop Insync before fixing dates.")

    discrepancies = find_date_discrepancies(conn, pictures_root, min_confidence=min_confidence)
    csv_path = generate_run_csv_path(manifest_dir, run_id)
    cursor = conn.cursor()

    ok_c, err_c = 0, 0

    with open(csv_path, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow([
            "run_id", "seq", "logged_at", "op_type", "src", "dst",
            "category", "reason", "date_used", "date_source",
            "files_inside", "bytes", "status", "error_msg",
        ])

        seq = 0
        for d in discrepancies:
            seq += 1
            abs_p = pictures_root / d.rel_path
            new_ts = d.filename_dt.timestamp()
            dt_str = d.filename_dt.strftime("%Y-%m-%d %H:%M:%S")

            if not abs_p.exists():
                status = "skipped-missing"
                err = f"File {d.rel_path} not found on disk"
                err_c += 1
            else:
                try:
                    # Update filesystem mtime and atime
                    os.utime(str(abs_p), (new_ts, new_ts))

                    # Update database record
                    cursor.execute(
                        """
                        UPDATE resources
                        SET mtime = ?,
                            mtime_trusted = 1,
                            resolved_dt = ?,
                            date_source = 'name_restored'
                        WHERE resource_id = ?
                        """,
                        (dt_str, dt_str, d.resource_id),
                    )
                    status = "ok"
                    err = ""
                    ok_c += 1
                except Exception as e:
                    status = "error"
                    err = str(e)
                    err_c += 1

            if seq % 1000 == 0:
                conn.commit()
                print(f"    Restored {seq:,} / {len(discrepancies):,} files...")

            now_iso = datetime.now().isoformat()
            writer.writerow([
                run_id, seq, now_iso, "utime", d.rel_path, d.rel_path,
                "DateFix", f"Restored mtime from filename ({d.discrepancy_type})",
                dt_str, "filename", 1, None, status, err,
            ])
            csvfile.flush()

        conn.commit()

    return ok_c, err_c, csv_path
