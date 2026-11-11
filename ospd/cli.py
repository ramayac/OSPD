"""Command-line interface for OSPD (Organize and Sorting Photo Dump)."""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional

from ospd.config import Config, load_config
from ospd.classification import (
    classify_folder_name,
    is_ignored_path,
    normalize_top_folder,
)
from ospd.date_fixer import (
    apply_date_fixes,
    export_discrepancies_csv,
    find_date_discrepancies,
)
from ospd.db import Database
from ospd.duplicates import (
    bulk_approve_all_duplicates,
    bulk_approve_exact_duplicates,
    find_duplicate_groups,
)
from ospd.executor import Executor
from ospd.metadata import inspect_file
from ospd.planner import plan_operations
from ospd.reporter import generate_reports
from ospd.review import export_review_csvs, generate_html_contact_sheet
from ospd.simulator import run_full_dry_run_test


_CURRENT_CONFIG: Optional[Config] = None


def get_ospd_root() -> Path:
    """Return OSPD folder (where code, Makefile, and defaults live)."""
    return Path(__file__).resolve().parent.parent


def get_config(args: Optional[argparse.Namespace] = None) -> Config:
    """Return active Config, resolving from args, environment, or config file."""
    global _CURRENT_CONFIG
    if _CURRENT_CONFIG is None or (
        args and (
            getattr(args, "root", None)
            or getattr(args, "data_dir", None)
            or getattr(args, "config", None)
        )
    ):
        _CURRENT_CONFIG = load_config(args)
    return _CURRENT_CONFIG


def get_pictures_root(args: Optional[argparse.Namespace] = None) -> Path:
    """Return pictures root directory to process."""
    return get_config(args).pictures_root


def get_manifest_dir(args: Optional[argparse.Namespace] = None) -> Path:
    """Return data directory where DB, review files, and run logs live."""
    return get_config(args).data_dir


def next_run_id(db: Database) -> str:
    """Generate sequential run_id like run001, run002..."""
    with db.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1")
        row = cursor.fetchone()
        if not row:
            return "run001"
        last_id = row["run_id"]
        if last_id.startswith("run") and last_id[3:].isdigit():
            num = int(last_id[3:]) + 1
            return f"run{num:03d}"
        return f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def cmd_scan(args: argparse.Namespace) -> None:
    root = get_pictures_root(args)
    manifest_dir = get_manifest_dir(args)
    db_path = manifest_dir / "photo_sort.db"
    db = Database(db_path)
    run_id = next_run_id(db)

    print(f"==> Starting scan ({run_id}) on: {root}")
    exec_inst = Executor(root, db.get_connection(), manifest_dir=manifest_dir)
    preflight_ok, preflight_msg = exec_inst.preflight_check(ignore_insync=args.force_insync)
    if not preflight_ok:
        print(f"[!] Warning: {preflight_msg}")
        if not args.force_insync:
            print("Stopping. Use --force-insync to override if developing or offline.")
            sys.exit(1)

    db.record_run_start(run_id=run_id, kind="scan", args=str(args), insync_running=not preflight_ok)

    # 1. Discover all top-level folders (including currently empty ones)
    folder_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "lowres": 0})
    for entry in root.iterdir():
        if entry.is_dir():
            rel_dir = entry.name
            if not is_ignored_path(rel_dir):
                _ = folder_stats[rel_dir]
    _ = folder_stats["."]

    # Collect all files to scan
    all_files: list[Path] = []
    for p in root.rglob("*"):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            if not is_ignored_path(rel):
                all_files.append(p)
                if args.limit and len(all_files) >= args.limit:
                    break

    total_to_scan = len(all_files)
    print(f"==> Found {total_to_scan} files to inspect across {len(folder_stats)} folders...")

    scanned_count = 0
    total_bytes = 0

    def worker(p: Path):
        rel = p.relative_to(root).as_posix()
        top_folder = normalize_top_folder(rel)
        parent_folder = p.parent.relative_to(root).as_posix()
        meta = inspect_file(p, calculate_hash=args.calculate_hash)
        return p, rel, top_folder, parent_folder, meta

    with db.transaction() as conn:
        cursor = conn.cursor()

        with ThreadPoolExecutor(max_workers=16) as pool:
            for p, rel, top_folder, parent_folder, meta in pool.map(worker, all_files):
                scanned_count += 1
                total_bytes += meta.size_bytes

                folder_stats[top_folder]["total"] += 1
                if meta.is_lowres_candidate:
                    folder_stats[top_folder]["lowres"] += 1

                cursor.execute(
                    """
                    INSERT INTO resources (
                        original_path, current_path, parent_folder, top_folder, folder_class,
                        kind, ext, size_bytes, sha256, width, height, camera_make, camera_model,
                        exif_dt, json_dt, name_dt, video_dt, mtime, mtime_trusted, resolved_dt,
                        date_source, date_confidence, is_lowres_candidate, lowres_match,
                        live_pair_id, has_gps, latitude, longitude, status, issues, first_seen_run, last_seen_run
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'present', ?, ?, ?)
                    ON CONFLICT(original_path) DO UPDATE SET
                        current_path = excluded.current_path,
                        size_bytes = excluded.size_bytes,
                        sha256 = COALESCE(excluded.sha256, resources.sha256),
                        width = excluded.width,
                        height = excluded.height,
                        exif_dt = excluded.exif_dt,
                        resolved_dt = excluded.resolved_dt,
                        date_source = excluded.date_source,
                        date_confidence = excluded.date_confidence,
                        is_lowres_candidate = excluded.is_lowres_candidate,
                        has_gps = excluded.has_gps,
                        latitude = excluded.latitude,
                        longitude = excluded.longitude,
                        last_seen_run = excluded.last_seen_run
                    """,
                    (
                        rel,
                        rel,
                        parent_folder,
                        top_folder,
                        classify_folder_name(top_folder)[0],
                        meta.kind,
                        meta.ext,
                        meta.size_bytes,
                        meta.sha256,
                        meta.width,
                        meta.height,
                        meta.camera_make,
                        meta.camera_model,
                        meta.exif_dt,
                        meta.json_dt,
                        meta.name_dt,
                        meta.video_dt,
                        meta.mtime,
                        int(meta.mtime_trusted),
                        meta.resolved_dt,
                        meta.date_source,
                        meta.date_confidence,
                        int(meta.is_lowres_candidate),
                        meta.lowres_match,
                        p.stem.upper(),
                        int(meta.has_gps),
                        meta.latitude,
                        meta.longitude,
                        meta.issues,
                        run_id,
                        run_id,
                    ),
                )

                if scanned_count % 1000 == 0 or scanned_count == total_to_scan:
                    print(f"    Indexed {scanned_count:,} / {total_to_scan:,} files...")

        # Save folder statistics and classifications
        for folder, stats in folder_stats.items():
            tot = stats["total"]
            lr = stats["lowres"]
            ratio = lr / tot if tot > 0 else 0.0
            f_class, target_cat = classify_folder_name(folder)
            if ratio >= 0.80 and f_class not in ("CameraDump", "Screenshots", "Wallpapers"):
                f_class = "LowRes"
                target_cat = "Unsorted/LowRes"

            cursor.execute(
                """
                INSERT INTO folders (folder_path, folder_class, target_category, item_count, lowres_count, lowres_ratio)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(folder_path) DO UPDATE SET
                    item_count = excluded.item_count,
                    lowres_count = excluded.lowres_count,
                    lowres_ratio = excluded.lowres_ratio,
                    folder_class = excluded.folder_class,
                    target_category = excluded.target_category
                """,
                (folder, f_class, target_cat, tot, lr, ratio),
            )

        # Detect duplicate candidate groups
        dup_count = find_duplicate_groups(conn, run_id)

    dup_complete = bool(args.calculate_hash and not args.limit)
    db.record_run_finish(
        run_id=run_id,
        status="ok",
        files_before=scanned_count,
        bytes_before=total_bytes,
        dup_complete=dup_complete,
        notes=f"Scanned {scanned_count} files across {len(folder_stats)} folders. {dup_count} duplicate groups found.",
    )
    print(f"==> Scan complete: {scanned_count} files indexed ({total_bytes / (1024*1024):.1f} MB).")
    print(f"==> Duplicate candidate groups found: {dup_count}")


def cmd_plan(args: argparse.Namespace) -> None:
    root = get_pictures_root(args)
    manifest_dir = get_manifest_dir(args)
    db = Database(manifest_dir / "photo_sort.db")
    run_id = next_run_id(db)

    print(f"==> Generating execution plan ({run_id})...")
    db.record_run_start(run_id=run_id, kind="plan", args=str(args))
    with db.transaction() as conn:
        ops = plan_operations(conn, run_id)

    db.record_run_finish(
        run_id=run_id,
        status="ok",
        ops_planned=len(ops),
        notes=f"Generated {len(ops)} operations.",
    )

    by_cat: dict[str, int] = defaultdict(int)
    for op in ops:
        by_cat[f"{op.op_type}:{op.category}"] += 1

    print(f"==> Plan ready with {len(ops)} operations:")
    for cat, count in sorted(by_cat.items()):
        print(f"    - {cat}: {count}")


def cmd_dry_run(args: argparse.Namespace) -> None:
    root = get_pictures_root(args)
    manifest_dir = get_manifest_dir(args)
    db = Database(manifest_dir / "photo_sort.db")
    run_id = next_run_id(db)

    print(f"==> Running dry-run validation (capped at {args.limit} operations)...")
    db.record_run_start(run_id=run_id, kind="test", args=str(args))
    conn = db.get_connection()
    res = run_full_dry_run_test(conn, pictures_root=root, limit=args.limit)
    conn.close()

    status = "ok" if res.passed else "failed"
    notes = f"Checked {res.total_checked} ops. Memory ok: {len(res.memory_errors)==0}, Mock ok: {res.mock_test_passed}"
    db.record_run_finish(run_id=run_id, status=status, ops_planned=res.total_checked, notes=notes)

    if res.passed:
        print(f"==> DRY-RUN PASSED: {res.total_checked} operations validated with 0 errors!")
    else:
        print("==> DRY-RUN FAILED:")
        for err in res.memory_errors:
            print(f"    [Memory Error] {err}")
        for err in res.mock_test_errors:
            print(f"    [Mock Error] {err}")
        sys.exit(1)


def cmd_apply(args: argparse.Namespace, is_batch: bool = False) -> None:
    root = get_pictures_root(args)
    manifest_dir = get_manifest_dir(args)
    db = Database(manifest_dir / "photo_sort.db")
    run_id = next_run_id(db)

    limit_desc = f" (limit: {args.limit})" if args.limit else " (ALL planned)"
    print(f"==> PREPARING TO APPLY OPERATIONS {limit_desc} under {run_id}")

    if not args.run:
        print("\n[SAFETY BLOCK] Execution requires the '--run' flag to confirm moves.")
        print("Run with: make apply CONFIRM=--run   or   python3 -m ospd.cli apply --run")
        sys.exit(1)

    exec_inst = Executor(root, db.get_connection(), manifest_dir=manifest_dir)
    preflight_ok, preflight_msg = exec_inst.preflight_check(ignore_insync=args.force_insync)
    if not preflight_ok:
        print(f"[!] {preflight_msg}")
        if not args.force_insync:
            print("Cannot apply while Insync is active. Stop Insync or use --force-insync.")
            sys.exit(1)

    db.record_run_start(run_id=run_id, kind="apply", args=str(args), insync_running=not preflight_ok)

    ok_c, skip_c, err_c, csv_path = exec_inst.execute_planned(
        run_id=run_id,
        limit=args.limit,
        ignore_insync=args.force_insync,
    )

    status = "ok" if err_c == 0 else "failed"
    db.record_run_finish(
        run_id=run_id,
        status=status,
        ops_planned=ok_c + skip_c + err_c,
        ops_ok=ok_c,
        ops_skipped=skip_c,
        ops_failed=err_c,
        notes=f"Moved: {ok_c}, Skipped: {skip_c}, Errors: {err_c}. Log: {csv_path.name}",
    )

    print(f"==> Execution finished: {ok_c} moved, {skip_c} skipped, {err_c} errors.")
    print(f"==> Full log written to: {csv_path}")


def cmd_undo(args: argparse.Namespace) -> None:
    root = get_pictures_root(args)
    manifest_dir = get_manifest_dir(args)
    db = Database(manifest_dir / "photo_sort.db")
    undo_run_id = next_run_id(db)
    target_run = args.target_run

    print(f"==> Undoing run '{target_run}' into {undo_run_id}...")
    exec_inst = Executor(root, db.get_connection(), manifest_dir=manifest_dir)
    preflight_ok, preflight_msg = exec_inst.preflight_check(ignore_insync=args.force_insync)
    if not preflight_ok and not args.force_insync:
        print(f"[!] {preflight_msg}")
        sys.exit(1)

    db.record_run_start(run_id=undo_run_id, kind="undo", undo_of_run_id=target_run, args=str(args))
    ok_c, err_c, csv_path = exec_inst.rollback_run(
        target_run_id=target_run,
        undo_run_id=undo_run_id,
        ignore_insync=args.force_insync,
    )
    status = "ok" if err_c == 0 else "failed"
    db.record_run_finish(
        run_id=undo_run_id,
        status=status,
        ops_ok=ok_c,
        ops_failed=err_c,
        notes=f"Rollback of {target_run}: {ok_c} reversed, {err_c} errors.",
    )
    print(f"==> Rollback complete: {ok_c} restored, {err_c} errors. Log: {csv_path}")


def cmd_review(args: argparse.Namespace) -> None:
    root = get_pictures_root(args)
    manifest_dir = get_manifest_dir(args)
    db = Database(manifest_dir / "photo_sort.db")

    if args.action == "export":
        f_csv, l_csv, d_csv = export_review_csvs(db.get_connection(), manifest_dir)
        print(f"==> Review CSVs exported to {manifest_dir / 'review'}:")
        print(f"    - {f_csv.name}")
        print(f"    - {l_csv.name}")
        print(f"    - {d_csv.name}")
    elif args.action == "html":
        html_p = generate_html_contact_sheet(db.get_connection(), root, manifest_dir)
        print(f"==> Contact sheet generated: file://{html_p}")
    elif args.action == "approve-exact":
        with db.transaction() as conn:
            ok, msg, count = bulk_approve_exact_duplicates(conn)
        if ok:
            print(f"==> Success: {msg}")
        else:
            print(f"[!] Refused: {msg}")
    elif args.action == "approve-all":
        with db.transaction() as conn:
            ok, msg, count = bulk_approve_all_duplicates(conn)
        if ok:
            print(f"==> Success: {msg}")
        else:
            print(f"[!] Refused: {msg}")


def cmd_check_dates(args: argparse.Namespace) -> None:
    root = get_pictures_root(args)
    manifest_dir = get_manifest_dir(args)
    db_path = manifest_dir / "photo_sort.db"
    if not db_path.exists():
        print(f"No database found at {db_path}. Run 'make scan' first.")
        return

    db = Database(db_path)
    conn = db.get_connection()
    discrepancies = find_date_discrepancies(conn, root, min_confidence=args.min_confidence)

    cursor = conn.cursor()
    cursor.execute("SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1")
    row = cursor.fetchone()
    active_run = row["run_id"] if row else "run000"
    conn.close()

    csv_out = manifest_dir / "review" / "date_discrepancies.csv"
    export_discrepancies_csv(discrepancies, csv_out, run_id=active_run)

    print(f"==> Identified {len(discrepancies)} high-confidence date discrepancies (>= {args.min_confidence*100:.0f}% confidence)")
    print(f"==> Exported full discrepancy report to: file://{csv_out}")

    if discrepancies:
        print("\nSample Discrepancies (first 10):")
        for d in discrepancies[:10]:
            print(f"  • {d.rel_path}")
            print(f"      Filename date: {d.filename_dt} (Confidence: {d.confidence*100:.0f}%)")
            print(f"      Current mtime: {d.current_mtime} | Current EXIF: {d.current_exif or 'None'}")
            print(f"      Issue:         {d.discrepancy_type} (~{d.diff_seconds/3600:.1f} hrs difference)")
        print("\nTo apply these dates to the filesystem, run:")
        print("  make fix-dates CONFIRM=--run")


def cmd_fix_dates(args: argparse.Namespace) -> None:
    root = get_pictures_root(args)
    manifest_dir = get_manifest_dir(args)
    db_path = manifest_dir / "photo_sort.db"
    if not db_path.exists():
        print(f"No database found at {db_path}. Run 'make scan' first.")
        return

    db = Database(db_path)
    conn = db.get_connection()
    discrepancies = find_date_discrepancies(conn, root, min_confidence=args.min_confidence)

    if not discrepancies:
        print("==> No date discrepancies found with confidence >= 90%. Filesystem is consistent!")
        conn.close()
        return

    print(f"==> Found {len(discrepancies)} files with high-confidence dates in filename.")

    if not args.run:
        print("\n[DRY RUN] No changes made to disk.")
        print(f"To apply filesystem mtime updates to these {len(discrepancies)} files, run:")
        print("  make fix-dates CONFIRM=--run   or   python3 -m ospd.cli fix-dates --run")
        conn.close()
        return

    run_id = next_run_id(db)
    db.record_run_start(run_id=run_id, kind="fix-dates", args=str(args))
    ok_c, err_c, csv_path = apply_date_fixes(
        conn=conn,
        pictures_root=root,
        manifest_dir=manifest_dir,
        run_id=run_id,
        min_confidence=args.min_confidence,
        ignore_insync=args.force_insync,
    )
    conn.close()

    status = "ok" if err_c == 0 else "failed"
    db.record_run_finish(
        run_id=run_id,
        status=status,
        ops_ok=ok_c,
        ops_failed=err_c,
        notes=f"Restored filesystem mtime for {ok_c} files from filename. Errors: {err_c}.",
    )

    print(f"==> Completed date fixes: {ok_c} restored, {err_c} errors.")
    print(f"==> Operation log: file://{csv_path}")


def cmd_report(args: argparse.Namespace) -> None:
    root = get_pictures_root(args)
    manifest_dir = get_manifest_dir(args)
    db_path = manifest_dir / "photo_sort.db"
    if not db_path.exists():
        print(f"No database found at {db_path}. Run 'make scan' first.")
        return

    db = Database(db_path)
    md_p, html_p = generate_reports(db.get_connection(), manifest_dir)
    print("==> Reports successfully generated in review folder:")
    print(f"    - HTML Dashboard: file://{html_p}")
    print(f"    - Markdown:       file://{md_p}")


def cmd_status(args: argparse.Namespace) -> None:
    root = get_pictures_root(args)
    manifest_dir = get_manifest_dir(args)
    db_path = manifest_dir / "photo_sort.db"
    if not db_path.exists():
        print(f"No database found at {db_path}. Run 'make scan' first.")
        return

    db = Database(db_path)
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) AS c FROM resources")
        res_count = cursor.fetchone()["c"]

        cursor.execute("SELECT COUNT(*) AS c FROM folders")
        folders_count = cursor.fetchone()["c"]

        cursor.execute("SELECT COUNT(*) AS c FROM dup_groups")
        dup_groups_count = cursor.fetchone()["c"]

        cursor.execute("SELECT status, COUNT(*) as c FROM operations GROUP BY status")
        op_counts = {r["status"]: r["c"] for r in cursor.fetchall()}

        cursor.execute("SELECT run_id, kind, started_at, status, notes FROM runs ORDER BY started_at DESC LIMIT 5")
        recent_runs = cursor.fetchall()

    print("========================================")
    print("        OSPD SYSTEM STATUS             ")
    print("========================================")
    print(f"Pictures Root: {root}")
    print(f"Database:      {db_path}")
    print(f"Indexed files: {res_count}")
    print(f"Folders:       {folders_count}")
    print(f"Dup groups:    {dup_groups_count}")
    print(f"Operations:    {op_counts if op_counts else 'None planned'}")
    print("\nRecent Runs:")
    for r in recent_runs:
        print(f"  [{r['run_id']}] {r['kind']} - {r['status']} ({r['started_at']})")
        if r["notes"]:
            print(f"      Notes: {r['notes']}")
    print("========================================")


def main() -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-r", "--root", type=str, help="Pictures root directory to process")
    common.add_argument("-d", "--data-dir", type=str, help="Data directory for database and reports (default: ./data-run)")
    common.add_argument("-c", "--config", type=str, help="Path to custom config.json file")
    common.add_argument("--ignore-sync", "--force-insync", action="store_true", dest="force_insync", help="Bypass cloud sync client active check")

    parser = argparse.ArgumentParser(prog="ospd", description="Organize and Sorting Photo Dump")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # scan
    p_scan = subparsers.add_parser("scan", parents=[common], help="Scan and index pictures folder")
    p_scan.add_argument("--limit", type=int, help="Limit number of files to scan")
    p_scan.add_argument("--calculate-hash", action="store_true", help="Calculate SHA256 checksums")

    # plan
    subparsers.add_parser("plan", parents=[common], help="Generate move plan based on classifications")

    # dry-run
    p_dry = subparsers.add_parser("dry-run", parents=[common], help="Simulate moves and test rollback (up to 20k ops)")
    p_dry.add_argument("--limit", type=int, default=20000, help="Max operations to simulate")

    # batch-run / apply
    p_apply = subparsers.add_parser("apply", parents=[common], help="Execute planned moves")
    p_apply.add_argument("--limit", type=int, help="Limit operations to execute (pilot batch)")
    p_apply.add_argument("--run", action="store_true", help="Confirmation flag to perform real moves")

    p_batch = subparsers.add_parser("batch-run", parents=[common], help="Execute pilot batch (default 200 items)")
    p_batch.add_argument("--limit", type=int, default=200, help="Pilot batch size")
    p_batch.add_argument("--run", action="store_true", help="Confirmation flag to perform real moves")

    # undo
    p_undo = subparsers.add_parser("undo", parents=[common], help="Rollback an executed run")
    p_undo.add_argument("target_run", help="Run ID to revert (e.g. run003)")

    # review
    p_rev = subparsers.add_parser("review", parents=[common], help="Review actions")
    p_rev.add_argument("action", choices=["export", "html", "approve-exact", "approve-all"], help="Review sub-action")

    # check-dates
    p_chk = subparsers.add_parser("check-dates", parents=[common], help="List date discrepancies where filename has >= 90%% confidence")
    p_chk.add_argument("--min-confidence", type=float, default=0.90, help="Minimum confidence threshold (default 0.90)")

    # fix-dates
    p_fix = subparsers.add_parser("fix-dates", parents=[common], help="Restore filesystem mtime from high-confidence filename")
    p_fix.add_argument("--min-confidence", type=float, default=0.90, help="Minimum confidence threshold (default 0.90)")
    p_fix.add_argument("--run", action="store_true", help="Confirmation flag to apply changes to disk")

    # report
    subparsers.add_parser("report", parents=[common], help="Generate Markdown and HTML execution reports")

    # status
    subparsers.add_parser("status", parents=[common], help="Show system status and recent runs")

    args = parser.parse_args()

    if args.command == "scan":
        cmd_scan(args)
    elif args.command == "plan":
        cmd_plan(args)
    elif args.command == "dry-run":
        cmd_dry_run(args)
    elif args.command == "apply":
        cmd_apply(args)
    elif args.command == "batch-run":
        cmd_apply(args, is_batch=True)
    elif args.command == "undo":
        cmd_undo(args)
    elif args.command == "review":
        cmd_review(args)
    elif args.command == "check-dates":
        cmd_check_dates(args)
    elif args.command == "fix-dates":
        cmd_fix_dates(args)
    elif args.command == "report":
        cmd_report(args)
    elif args.command == "status":
        cmd_status(args)


if __name__ == "__main__":
    main()
