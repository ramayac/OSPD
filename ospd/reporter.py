"""Report generator for OSPD.

Produces both a Markdown summary (_Manifest/report.md) and an interactive
HTML dashboard (_Manifest/report.html).
"""

from __future__ import annotations

import html
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def generate_reports(conn: sqlite3.Connection, manifest_dir: Path) -> Tuple[Path, Path]:
    """Generate both Markdown and HTML status/results reports."""
    cursor = conn.cursor()

    # 1. Gather stats
    cursor.execute("SELECT COUNT(*) as c, COALESCE(SUM(size_bytes), 0) as s FROM resources")
    row = cursor.fetchone()
    total_files = row["c"]
    total_bytes = row["s"]

    cursor.execute("SELECT folder_class, COUNT(*) as c FROM folders GROUP BY folder_class")
    folder_classes = {r["folder_class"]: r["c"] for r in cursor.fetchall()}

    cursor.execute("SELECT date_source, COUNT(*) as c FROM resources GROUP BY date_source")
    date_sources = {r["date_source"]: r["c"] for r in cursor.fetchall()}

    cursor.execute("SELECT date_confidence, COUNT(*) as c FROM resources GROUP BY date_confidence")
    date_conf = {r["date_confidence"]: r["c"] for r in cursor.fetchall()}

    cursor.execute("SELECT COUNT(*) as c FROM resources WHERE is_lowres_candidate = 1")
    lowres_candidates = cursor.fetchone()["c"]

    try:
        cursor.execute("SELECT COUNT(*) as c FROM resources WHERE has_gps = 1")
        gps_count = cursor.fetchone()["c"]
    except sqlite3.OperationalError:
        gps_count = 0
    gps_pct = (gps_count / max(1, total_files)) * 100

    cursor.execute("SELECT COUNT(*) as c FROM dup_groups")
    dup_groups = cursor.fetchone()["c"]

    cursor.execute(
        """
        SELECT match_type, confidence, COUNT(*) as group_count
        FROM dup_groups
        GROUP BY match_type, confidence
        ORDER BY group_count DESC
        """
    )
    dup_type_stats = cursor.fetchall()

    cursor.execute("SELECT COUNT(DISTINCT resource_id) as c FROM dup_members")
    total_dup_files_tracked = cursor.fetchone()["c"]

    # Top folder breakdown
    cursor.execute(
        """
        SELECT top_folder, COUNT(*) as c, COALESCE(SUM(size_bytes), 0) as s
        FROM resources
        WHERE status = 'present'
        GROUP BY top_folder
        ORDER BY c DESC
        """
    )
    top_folder_stats = cursor.fetchall()

    # Overlap analysis (Root vs Galería de Samsung)
    cursor.execute("SELECT current_path, top_folder, size_bytes FROM resources WHERE status = 'present'")
    all_res = cursor.fetchall()
    root_names = {Path(r["current_path"]).name: r["size_bytes"] for r in all_res if r["top_folder"] == "."}
    samsung_names = {Path(r["current_path"]).name: r["size_bytes"] for r in all_res if r["top_folder"] == "Galería de Samsung"}
    shared_names = set(root_names.keys()) & set(samsung_names.keys())
    shared_exact_size = sum(1 for n in shared_names if root_names[n] == samsung_names[n])
    shared_diff_size = len(shared_names) - shared_exact_size
    root_only_count = len(root_names) - len(shared_names)
    samsung_only_count = len(samsung_names) - len(shared_names)

    cursor.execute("SELECT category, op_type, COUNT(*) as c FROM operations GROUP BY category, op_type")
    planned_ops_raw = cursor.fetchall()
    planned_ops = {f"{r['op_type']}:{r['category']}": r["c"] for r in planned_ops_raw}
    total_ops = sum(planned_ops.values())

    cursor.execute("SELECT status, COUNT(*) as c FROM operations GROUP BY status")
    op_statuses = {r["status"]: r["c"] for r in cursor.fetchall()}

    cursor.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 10")
    recent_runs = cursor.fetchall()

    cursor.execute(
        """
        SELECT folder_path, folder_class, target_category, item_count, lowres_count, lowres_ratio
        FROM folders
        ORDER BY item_count DESC
        LIMIT 50
        """
    )
    folder_rows = cursor.fetchall()

    cursor.execute(
        """
        SELECT op_type, src, dst, category, reason
        FROM operations
        ORDER BY seq ASC
        LIMIT 50
        """
    )
    sample_ops = cursor.fetchall()

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # -------------------------------------------------------------
    # Generate Markdown Report (_Manifest/report.md)
    # -------------------------------------------------------------
    md_lines = [
        "# OSPD Execution & Results Report",
        f"\n*Generated on: {now_str}*\n",
        "## 1. Executive Summary",
        f"- **Indexed Resources:** {total_files:,} files ({total_bytes / (1024*1024*1024):.2f} GB / {total_bytes / (1024*1024):.2f} MB)",
        f"- **Folders Categorized:** {sum(folder_classes.values()):,} folders",
        f"- **Planned Operations:** {total_ops:,} moves",
        f"- **Dry-Run Status:** {op_statuses.get('dryrun-ok', 0):,} validated OK / {op_statuses.get('dryrun-fail', 0):,} failed",
        f"- **Geolocation (GPS):** {gps_count:,} files ({gps_pct:.1f}% with embedded coordinates)",
        f"- **Duplicate Candidate Groups:** {dup_groups:,} ({total_dup_files_tracked:,} duplicate candidate files tracked)",
        f"- **Low-Resolution Candidates (<100KB or <800px):** {lowres_candidates:,}",
        "\n## 2. Sync & Folder Distribution",
        "| Folder / Source | File Count | Size (MB) |",
        "|---|---|---|",
    ]
    for tf in top_folder_stats[:15]:
        fname = "Root (.)" if tf["top_folder"] == "." else tf["top_folder"]
        md_lines.append(f"| `{fname}` | {tf['c']:,} | {tf['s'] / (1024*1024):.1f} MB |")

    pct_root_dup = (len(shared_names) / max(1, len(root_names))) * 100
    md_lines.extend([
        "\n## 3. Duplicate Candidates & Overlap Analysis",
        f"- **Shared Basenames (Root ↔ Galería de Samsung):** {len(shared_names):,} files ({pct_root_dup:.1f}% of Root)",
        f"  - *Exact Byte Match (SHA-256):* {shared_exact_size:,} files",
        f"  - *Near-Duplicates (EXIF / Minor header difference):* {shared_diff_size:,} files",
        f"- **Unique to Root (iPhone .heic/.mov, Web downloads):** {root_only_count:,} files",
        f"- **Unique to Galería de Samsung (Older Galaxy, Kodak dumps):** {samsung_only_count:,} files",
        "\n| Duplicate Match Type | Confidence | Candidate Groups |",
        "|---|---|---|",
    ])
    for dt in dup_type_stats:
        md_lines.append(f"| `{dt['match_type']}` | {dt['confidence']} | {dt['group_count']:,} |")

    md_lines.extend([
        "\n## 4. Planned Operations by Category",
        "| Operation Type | Category | Count |",
        "|---|---|---|",
    ])
    for key, count in sorted(planned_ops.items()):
        op_t, cat = key.split(":", 1)
        md_lines.append(f"| {op_t.title()} | {cat} | {count:,} |")

    md_lines.extend([
        "\n## 5. Date Resolution Confidence",
        "| Source | Confidence | File Count |",
        "|---|---|---|",
    ])
    for src, count in sorted(date_sources.items(), key=lambda x: -x[1]):
        src_label = (src or "none").upper()
        conf = "High" if src in ("exif", "video", "json") else ("Medium" if src == "name" else ("Low" if src == "mtime" else "None"))
        md_lines.append(f"| {src_label} | {conf} | {count:,} |")

    md_lines.extend([
        "\n## 6. Top Categorized Folders (Preserved Whole)",
        "| Source Folder | Classified As | Target Category | Total Items | Low-Res Items |",
        "|---|---|---|---|---|",
    ])
    for f in folder_rows[:25]:
        md_lines.append(
            f"| `{f['folder_path']}` | **{f['folder_class']}** | `{f['target_category']}` | {f['item_count']} | {f['lowres_count']} ({f['lowres_ratio']*100:.0f}%) |"
        )

    md_lines.extend([
        "\n## 7. Sample Planned Operations (First 25)",
        "| Type | Source | Planned Destination | Reason |",
        "|---|---|---|---|",
    ])
    for op in sample_ops[:25]:
        md_lines.append(
            f"| {op['op_type']} | `{op['src']}` | `{op['dst']}` | {op['reason']} |"
        )

    md_lines.extend([
        "\n## 8. Run History",
        "| Run ID | Kind | Status | Started At | Notes |",
        "|---|---|---|---|---|",
    ])
    for r in recent_runs:
        notes = r["notes"] or ""
        md_lines.append(f"| `{r['run_id']}` | {r['kind']} | **{r['status']}** | {r['started_at']} | {notes} |")

    review_dir = manifest_dir / "review"
    history_dir = review_dir / "history"
    review_dir.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)

    cursor.execute("SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1")
    run_row = cursor.fetchone()
    active_run = run_row["run_id"] if run_row else "run000"

    md_content = "\n".join(md_lines)
    md_path = review_dir / "report.md"
    for p in (md_path, history_dir / f"{active_run}_report.md"):
        with open(p, "w", encoding="utf-8") as f:
            f.write(md_content)

    # -------------------------------------------------------------
    # Generate Standalone Visual HTML Dashboard (_Manifest/report.html)
    # -------------------------------------------------------------
    folder_tr_html = []
    for f in folder_rows:
        folder_tr_html.append(f"""
        <tr>
            <td><code>{html.escape(f['folder_path'])}</code></td>
            <td><span class="badge badge-cat">{html.escape(f['folder_class'])}</span></td>
            <td><code>{html.escape(f['target_category'])}</code></td>
            <td>{f['item_count']}</td>
            <td>{f['lowres_count']} ({f['lowres_ratio']*100:.0f}%)</td>
        </tr>
        """)

    ops_tr_html = []
    for op in sample_ops:
        ops_tr_html.append(f"""
        <tr>
            <td><span class="badge {'badge-folder' if op['op_type']=='folder' else 'badge-file'}">{op['op_type']}</span></td>
            <td><code>{html.escape(op['src'])}</code></td>
            <td><code>{html.escape(op['dst'])}</code></td>
            <td>{html.escape(op['reason'])}</td>
        </tr>
        """)

    runs_tr_html = []
    for r in recent_runs:
        status_badge = "badge-ok" if r["status"] == "ok" else ("badge-warn" if r["status"] == "running" else "badge-err")
        runs_tr_html.append(f"""
        <tr>
            <td><b>{html.escape(r['run_id'])}</b></td>
            <td>{html.escape(r['kind'])}</td>
            <td><span class="badge {status_badge}">{html.escape(r['status'])}</span></td>
            <td>{html.escape(r['started_at'])}</td>
            <td>{html.escape(r['notes'] or '')}</td>
        </tr>
        """)

    dup_type_tr_html = []
    for dt in dup_type_stats:
        dup_type_tr_html.append(f"""
        <tr>
            <td><code>{html.escape(dt['match_type'])}</code></td>
            <td><span class="badge badge-cat">{html.escape(dt['confidence'])}</span></td>
            <td><b>{dt['group_count']:,}</b></td>
        </tr>
        """)

    top_folders_tr_html = []
    for tf in top_folder_stats[:15]:
        fname = "Root (.)" if tf["top_folder"] == "." else tf["top_folder"]
        top_folders_tr_html.append(f"""
        <tr>
            <td><code>{html.escape(fname)}</code></td>
            <td>{tf['c']:,}</td>
            <td>{tf['s'] / (1024*1024):.1f} MB ({tf['s'] / (1024*1024*1024):.2f} GB)</td>
        </tr>
        """)

    ops_breakdown_html = []
    for key, count in sorted(planned_ops.items()):
        op_t, cat = key.split(":", 1)
        ops_breakdown_html.append(f"""
        <div class="stat-pill">
            <span class="pill-title">{html.escape(cat)} ({op_t})</span>
            <span class="pill-val">{count:,}</span>
        </div>
        """)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>OSPD Photo Cleanup & Sorting Dashboard</title>
    <style>
        :root {{
            --bg: #121418;
            --surface: #1e222b;
            --surface-hover: #262c37;
            --border: #323b49;
            --text: #e6edf3;
            --text-dim: #8b949e;
            --accent: #58a6ff;
            --success: #3fb950;
            --warn: #d29922;
            --danger: #f85149;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background: var(--bg);
            color: var(--text);
            padding: 30px;
            line-height: 1.5;
        }}
        .header {{ margin-bottom: 25px; border-bottom: 1px solid var(--border); padding-bottom: 15px; }}
        .header h1 {{ font-size: 24px; color: var(--accent); display: flex; align-items: center; gap: 10px; }}
        .header .meta {{ color: var(--text-dim); font-size: 13px; margin-top: 5px; }}
        .cards-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 15px;
            margin-bottom: 25px;
        }}
        .card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 16px;
        }}
        .card .title {{ font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-dim); }}
        .card .value {{ font-size: 26px; font-weight: 700; margin-top: 5px; color: var(--text); }}
        .card .sub {{ font-size: 11px; color: var(--text-dim); margin-top: 4px; }}
        .section {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 25px;
        }}
        .section h2 {{ font-size: 16px; margin-bottom: 15px; color: var(--accent); border-bottom: 1px solid var(--border); padding-bottom: 8px; }}
        .pill-container {{ display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 10px; }}
        .stat-pill {{
            background: #252b36;
            border: 1px solid var(--border);
            padding: 8px 14px;
            border-radius: 6px;
            display: flex;
            flex-direction: column;
        }}
        .pill-title {{ font-size: 11px; color: var(--text-dim); }}
        .pill-val {{ font-size: 16px; font-weight: 600; color: #fff; }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }}
        th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--border); }}
        th {{ color: var(--text-dim); font-weight: 600; background: rgba(0,0,0,0.15); }}
        tr:hover td {{ background: var(--surface-hover); }}
        code {{
            background: rgba(110,118,129,0.2);
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 12px;
            font-family: ui-monospace, SFMono-Regular, SF Mono, Menlo, monospace;
        }}
        .badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
        }}
        .badge-ok {{ background: rgba(63, 185, 80, 0.2); color: var(--success); border: 1px solid var(--success); }}
        .badge-warn {{ background: rgba(210, 153, 34, 0.2); color: var(--warn); border: 1px solid var(--warn); }}
        .badge-err {{ background: rgba(248, 81, 73, 0.2); color: var(--danger); border: 1px solid var(--danger); }}
        .badge-cat {{ background: rgba(88, 166, 255, 0.2); color: var(--accent); border: 1px solid var(--accent); }}
        .badge-folder {{ background: #238636; color: white; }}
        .badge-file {{ background: #1f6feb; color: white; }}
        .actions {{ margin-top: 15px; display: flex; gap: 10px; }}
        .btn {{
            display: inline-block;
            padding: 8px 16px;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 500;
            text-decoration: none;
            background: #238636;
            color: white;
        }}
        .btn-outline {{ background: transparent; border: 1px solid var(--border); color: var(--text); }}
    </style>
</head>
<body>
    <div class="header">
        <h1>📸 OSPD Photo Cleanup Dashboard</h1>
        <div class="meta">Report generated: {now_str} • Path: <code>{html.escape(str(manifest_dir.parent))}</code></div>
    </div>

    <div class="cards-grid">
        <div class="card">
            <div class="title">Total Files</div>
            <div class="value">{total_files:,}</div>
            <div class="sub">{total_bytes / (1024*1024):.1f} MB indexed</div>
        </div>
        <div class="card">
            <div class="title">Folders</div>
            <div class="value">{sum(folder_classes.values()):,}</div>
            <div class="sub">Albums, Wallpapers, Art, etc.</div>
        </div>
        <div class="card">
            <div class="title">Planned Moves</div>
            <div class="value">{total_ops:,}</div>
            <div class="sub">{op_statuses.get('dryrun-ok', 0)} verified dry-run OK</div>
        </div>
        <div class="card">
            <div class="title">Geolocation (GPS)</div>
            <div class="value">{gps_pct:.1f}%</div>
            <div class="sub">{gps_count:,} photos with coordinates</div>
        </div>
        <div class="card">
            <div class="title">Duplicates</div>
            <div class="value">{dup_groups:,}</div>
            <div class="sub">{total_dup_files_tracked:,} duplicate files tracked</div>
        </div>
        <div class="card">
            <div class="title">Root ↔ Samsung Overlap</div>
            <div class="value">{len(shared_names):,}</div>
            <div class="sub">{pct_root_dup:.1f}% of Root is duplicated in Samsung</div>
        </div>
        <div class="card">
            <div class="title">Low-Res Candidates</div>
            <div class="value">{lowres_candidates:,}</div>
            <div class="sub">&lt;100KB or &lt;800px short side</div>
        </div>
    </div>

    <div class="section">
        <h2>Duplicate Candidates & Overlap Analysis</h2>
        <div class="pill-container">
            <div class="stat-pill">
                <span class="pill-title">Shared Names (Root ↔ Samsung)</span>
                <span class="pill-val">{len(shared_names):,} files</span>
            </div>
            <div class="stat-pill">
                <span class="pill-title">Exact Byte Matches (SHA-256)</span>
                <span class="pill-val">{shared_exact_size:,} files</span>
            </div>
            <div class="stat-pill">
                <span class="pill-title">Near-Duplicates (EXIF / Minor header diff)</span>
                <span class="pill-val">{shared_diff_size:,} files</span>
            </div>
            <div class="stat-pill">
                <span class="pill-title">Unique to Root (iPhone .heic/.mov, Web)</span>
                <span class="pill-val">{root_only_count:,} files</span>
            </div>
            <div class="stat-pill">
                <span class="pill-title">Unique to Samsung (Older Galaxy / Kodak)</span>
                <span class="pill-val">{samsung_only_count:,} files</span>
            </div>
        </div>
        <p style="color:var(--text-dim); margin-bottom: 12px; font-size: 13px;">
            Candidate duplicate groups are tracked in <code>OSPD/photo_sort.db</code> and will only be quarantined to <code>_Duplicates/</code> after explicit review. Album photos are always protected.
        </p>
        <table>
            <thead>
                <tr>
                    <th>Duplicate Match Type</th>
                    <th>Confidence</th>
                    <th>Candidate Groups</th>
                </tr>
            </thead>
            <tbody>
                {''.join(dup_type_tr_html) if dup_type_tr_html else '<tr><td colspan=\"3\">No duplicate groups detected yet. Run <code>make plan</code>.</td></tr>'}
            </tbody>
        </table>
        <div class="actions">
            <a class="btn" href="review/duplicates.html" target="_blank">🔍 Open Duplicate Candidates Contact Sheet</a>
            <a class="btn btn-outline" href="review/duplicates.csv" target="_blank">📊 Open Duplicates CSV</a>
        </div>
    </div>

    <div class="section">
        <h2>Sync & Folder Distribution</h2>
        <table>
            <thead>
                <tr>
                    <th>Folder / Source</th>
                    <th>Item Count</th>
                    <th>Total Size</th>
                </tr>
            </thead>
            <tbody>
                {''.join(top_folders_tr_html)}
            </tbody>
        </table>
    </div>

    <div class="section">
        <h2>Planned Moves Breakdown</h2>
        <div class="pill-container">
            {''.join(ops_breakdown_html) if ops_breakdown_html else '<p style="color:var(--text-dim)">No operations planned yet. Run <code>make plan</code>.</p>'}
        </div>
        <div class="actions">
            <a class="btn btn-outline" href="review/duplicates.html" target="_blank">🔍 Open Duplicate Candidates Contact Sheet</a>
            <a class="btn btn-outline" href="report.md" target="_blank">📄 Open Markdown Report</a>
        </div>
    </div>

    <div class="section">
        <h2>Categorized Folders (Moving as whole units)</h2>
        <table>
            <thead>
                <tr>
                    <th>Folder</th>
                    <th>Class</th>
                    <th>Target</th>
                    <th>Items</th>
                    <th>Low-Res Ratio</th>
                </tr>
            </thead>
            <tbody>
                {''.join(folder_tr_html) if folder_tr_html else '<tr><td colspan="5">No folders indexed.</td></tr>'}
            </tbody>
        </table>
    </div>

    <div class="section">
        <h2>Sample Operations (First 50)</h2>
        <table>
            <thead>
                <tr>
                    <th>Type</th>
                    <th>Source</th>
                    <th>Destination</th>
                    <th>Reason</th>
                </tr>
            </thead>
            <tbody>
                {''.join(ops_tr_html) if ops_tr_html else '<tr><td colspan="4">No operations planned.</td></tr>'}
            </tbody>
        </table>
    </div>

    <div class="section">
        <h2>Run History</h2>
        <table>
            <thead>
                <tr>
                    <th>Run ID</th>
                    <th>Kind</th>
                    <th>Status</th>
                    <th>Started</th>
                    <th>Notes</th>
                </tr>
            </thead>
            <tbody>
                {''.join(runs_tr_html)}
            </tbody>
        </table>
    </div>
</body>
</html>
"""
    html_review_content = (
        html_content.replace('href="review/duplicates.html"', 'href="duplicates.html"')
        .replace('href="review/duplicates.csv"', 'href="duplicates.csv"')
        .replace('href="report.md"', 'href="report.md"')
    )
    html_path = review_dir / "report.html"
    for p in (html_path, history_dir / f"{active_run}_report.html"):
        with open(p, "w", encoding="utf-8") as f:
            f.write(html_review_content)

    return md_path, html_path
