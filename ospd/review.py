"""Review exports and HTML contact sheet generator."""

from __future__ import annotations

import csv
import html
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def export_review_csvs(
    conn: sqlite3.Connection,
    manifest_dir: Path,
    run_id: Optional[str] = None,
) -> Tuple[Path, Path, Path]:
    """Export review CSVs for folders, lowres candidates, and duplicates.

    Writes canonical CSVs in review/ and archives run-stamped copies in review/history/.
    """
    review_dir = manifest_dir / "review"
    history_dir = review_dir / "history"
    review_dir.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)
    cursor = conn.cursor()

    if not run_id:
        cursor.execute("SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1")
        row = cursor.fetchone()
        active_run = row["run_id"] if row else "run000"
    else:
        active_run = run_id

    # 1. Folders
    folders_csv = review_dir / "folders.csv"
    folders_hist = history_dir / f"{active_run}_folders.csv"
    cursor.execute("SELECT folder_path, folder_class, target_category, item_count, notes FROM folders")
    folder_rows = cursor.fetchall()

    for p in (folders_csv, folders_hist):
        with open(p, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["run_id", "folder_path", "folder_class", "target_category", "item_count", "decision", "notes"])
            for r in folder_rows:
                writer.writerow([active_run, r["folder_path"], r["folder_class"], r["target_category"], r["item_count"], "pending", r["notes"]])

    # 2. Low-res candidates
    lowres_csv = review_dir / "lowres.csv"
    lowres_hist = history_dir / f"{active_run}_lowres.csv"
    cursor.execute(
        """
        SELECT resource_id, current_path, size_bytes, width, height, lowres_match, lowres_decision
        FROM resources
        WHERE is_lowres_candidate = 1
        """
    )
    lowres_rows = cursor.fetchall()

    for p in (lowres_csv, lowres_hist):
        with open(p, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["run_id", "resource_id", "current_path", "size_bytes", "width", "height", "match_rule", "decision"])
            for r in lowres_rows:
                default_dec = "LowRes" if r["lowres_match"] == "both" else "keep"
                writer.writerow([active_run, r["resource_id"], r["current_path"], r["size_bytes"], r["width"], r["height"], r["lowres_match"], default_dec])

    # 3. Duplicates
    dups_csv = review_dir / "duplicates.csv"
    dups_hist = history_dir / f"{active_run}_duplicates.csv"
    cursor.execute(
        """
        SELECT dg.group_id, dg.match_type, dg.confidence, dm.resource_id, dm.proposed_role,
               r.current_path, r.folder_class, r.size_bytes, r.resolved_dt
        FROM dup_groups dg
        JOIN dup_members dm ON dg.group_id = dm.group_id
        JOIN resources r ON dm.resource_id = r.resource_id
        ORDER BY dg.group_id, dm.proposed_role DESC
        """
    )
    dup_rows = cursor.fetchall()

    for p in (dups_csv, dups_hist):
        with open(p, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["run_id", "group_id", "match_type", "confidence", "resource_id", "proposed_role", "current_path", "folder_class", "size_bytes", "resolved_dt", "decision"])
            for r in dup_rows:
                writer.writerow([
                    active_run, r["group_id"], r["match_type"], r["confidence"], r["resource_id"],
                    r["proposed_role"], r["current_path"], r["folder_class"],
                    r["size_bytes"], r["resolved_dt"], "pending",
                ])

    return folders_csv, lowres_csv, dups_csv


def generate_html_contact_sheet(
    conn: sqlite3.Connection,
    pictures_root: Path,
    manifest_dir: Path,
    run_id: Optional[str] = None,
) -> Path:
    """Generate HTML contact sheet for visual comparison of duplicate candidates."""
    review_dir = manifest_dir / "review"
    history_dir = review_dir / "history"
    review_dir.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)

    html_path = review_dir / "duplicates.html"
    cursor = conn.cursor()

    if not run_id:
        cursor.execute("SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1")
        row = cursor.fetchone()
        active_run = row["run_id"] if row else "run000"
    else:
        active_run = run_id

    hist_html_path = history_dir / f"{active_run}_duplicates.html"

    cursor.execute(
        """
        SELECT dg.group_id, dg.match_type, dg.confidence, dm.resource_id, dm.proposed_role,
               r.current_path, r.folder_class, r.size_bytes, r.width, r.height, r.resolved_dt
        FROM dup_groups dg
        JOIN dup_members dm ON dg.group_id = dm.group_id
        JOIN resources r ON dm.resource_id = r.resource_id
        ORDER BY dg.group_id, dm.proposed_role DESC
        """
    )
    rows = cursor.fetchall()

    groups: Dict[int, List[dict]] = {}
    for r in rows:
        gid = r["group_id"]
        if gid not in groups:
            groups[gid] = []
        groups[gid].append(dict(r))

    cards_html = []
    for gid, members in groups.items():
        match_type = members[0]["match_type"]
        confidence = members[0]["confidence"]
        member_cards = []

        for m in members:
            rel_file = m["current_path"]
            abs_file = pictures_root / rel_file
            role = m["proposed_role"]
            badge_class = "keep-badge" if role == "keep" else "dup-badge"
            dim = f"{m['width']}x{m['height']}" if m["width"] else "unknown"
            size_kb = f"{m['size_bytes'] / 1024:.1f} KB"
            dt = m["resolved_dt"] or "No date"

            member_cards.append(f"""
            <div class="photo-card">
                <div class="badge {badge_class}">{role.upper()}</div>
                <div class="thumb-box">
                    <img src="file://{html.escape(str(abs_file))}" loading="lazy" alt="thumb">
                </div>
                <div class="meta">
                    <div class="path" title="{html.escape(rel_file)}">{html.escape(rel_file)}</div>
                    <div>Folder: <b>{html.escape(m['folder_class'])}</b></div>
                    <div>Res: {dim} | Size: {size_kb}</div>
                    <div>Date: {html.escape(dt)}</div>
                </div>
            </div>
            """)

        cards_html.append(f"""
        <div class="group-section">
            <h3>Group #{gid} — {html.escape(match_type)} (Confidence: {html.escape(confidence)})</h3>
            <div class="photos-grid">
                {''.join(member_cards)}
            </div>
        </div>
        """)

    full_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>OSPD Duplicate Candidates Review</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #1a1a1a; color: #e0e0e0; margin: 20px; }}
        h1 {{ color: #4dabf7; }}
        .group-section {{ background: #252525; padding: 15px; margin-bottom: 25px; border-radius: 8px; border: 1px solid #333; }}
        .photos-grid {{ display: flex; flex-wrap: wrap; gap: 15px; }}
        .photo-card {{ background: #1e1e1e; border: 1px solid #444; border-radius: 6px; width: 280px; overflow: hidden; position: relative; }}
        .thumb-box {{ width: 100%; height: 200px; background: #000; display: flex; align-items: center; justify-content: center; }}
        .thumb-box img {{ max-width: 100%; max-height: 100%; object-fit: contain; }}
        .badge {{ position: absolute; top: 8px; right: 8px; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 11px; }}
        .keep-badge {{ background: #2b8a3e; color: white; }}
        .dup-badge {{ background: #e03131; color: white; }}
        .meta {{ padding: 10px; font-size: 12px; line-height: 1.5; }}
        .path {{ font-weight: bold; font-size: 11px; word-break: break-all; margin-bottom: 4px; color: #74c0fc; }}
    </style>
</head>
<body>
    <h1>OSPD Duplicate Candidates Contact Sheet (Run: {active_run})</h1>
    <p>Showing {len(groups)} candidate groups for run <code>{active_run}</code>. Decisions are edited in <code>duplicates.csv</code>.</p>
    {''.join(cards_html) if cards_html else '<p>No duplicate candidate groups found.</p>'}
</body>
</html>
"""
    for p in (html_path, hist_html_path):
        with open(p, "w", encoding="utf-8") as f:
            f.write(full_html)

    return html_path
