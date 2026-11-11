"""Execution and rollback engine for OSPD with strict safety guarantees."""

from __future__ import annotations

import csv
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


def is_sync_client_running(client_name: str = "auto") -> bool:
    """Check if any cloud sync client process is running on the system."""
    if client_name.lower() in ("none", "disabled", "false"):
        return False

    targets = ["insync"] if client_name in ("insync", "auto") else [client_name]
    if client_name == "auto":
        targets = ["insync", "onedrive", "dropbox"]

    for target in targets:
        try:
            res = subprocess.run(["pgrep", "-i", target], capture_output=True, text=True)
            if res.returncode == 0 and len(res.stdout.strip()) > 0:
                return True
        except Exception:
            pass

        # Fallback inspection of /proc
        try:
            for p in Path("/proc").glob("[0-9]*/cmdline"):
                try:
                    cmd = p.read_bytes().decode("utf-8", "ignore").lower()
                    if target in cmd:
                        return True
                except (OSError, PermissionError):
                    continue
        except Exception:
            pass
    return False


def is_insync_running() -> bool:
    """Legacy wrapper for is_sync_client_running('insync')."""
    return is_sync_client_running("insync")


def find_insyncdl_files(root: Path) -> List[Path]:
    """Find any incomplete Insync downloads."""
    return list(root.glob("*.insyncdl")) + list(root.glob(".*.insyncdl"))


def generate_run_csv_path(manifest_dir: Path, run_id: str, is_undo: bool = False) -> Path:
    """Generate run log path: <runid>-<date>-<timestamp>[ -undo].csv"""
    now = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    suffix = "-undo" if is_undo else ""
    filename = f"{run_id}-{now}{suffix}.csv"
    runs_dir = manifest_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    return runs_dir / filename


CSV_HEADER = [
    "run_id", "seq", "logged_at", "op_type", "src", "dst",
    "category", "reason", "date_used", "date_source",
    "files_inside", "bytes", "status", "error_msg",
]


class Executor:
    """Safely executes planned moves and generates atomic rollback records."""

    def __init__(
        self,
        pictures_root: Path,
        conn: sqlite3.Connection,
        manifest_dir: Optional[Path] = None,
    ):
        self.root = Path(pictures_root).resolve()
        self.conn = conn
        self.manifest_dir = (
            Path(manifest_dir).resolve()
            if manifest_dir
            else (self.root / "OSPD")
        )

    def preflight_check(self, ignore_insync: bool = False) -> Tuple[bool, str]:
        """Verify Insync state before touching files."""
        if not ignore_insync:
            if is_insync_running():
                return False, "SAFETY BLOCK: Insync process is running. Stop Insync before applying moves."
            dl_files = find_insyncdl_files(self.root)
            if dl_files:
                return (
                    False,
                    f"SAFETY BLOCK: Found {len(dl_files)} .insyncdl temporary files. Ensure sync is 100% complete.",
                )
        return True, "Preflight checks passed"

    def execute_planned(
        self,
        run_id: str,
        limit: Optional[int] = None,
        ignore_insync: bool = False,
    ) -> Tuple[int, int, int, Path]:
        """Execute planned operations. Returns (ok_count, skipped_count, error_count, csv_path)."""
        csv_path = generate_run_csv_path(self.manifest_dir, run_id)
        cursor = self.conn.cursor()

        query = """
            SELECT op_id, seq, op_type, resource_id, src, dst, category, reason,
                   date_used, date_source, files_inside, bytes
            FROM operations
            WHERE status IN ('planned', 'dryrun-ok')
            ORDER BY seq ASC
        """
        if limit:
            query += f" LIMIT {int(limit)}"

        cursor.execute(query)
        ops = cursor.fetchall()

        ok_count, skipped_count, error_count = 0, 0, 0

        with open(csv_path, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(CSV_HEADER)

            for op in ops:
                op_id = op["op_id"]
                seq = op["seq"]
                op_type = op["op_type"]
                src = op["src"].strip("/")
                dst = op["dst"].strip("/")
                src_path = self.root / src
                dst_path = self.root / dst

                start_iso = datetime.now().isoformat()

                # Check pre-conditions
                if not src_path.exists():
                    status = "skipped-missing"
                    err = f"Source '{src}' does not exist on disk"
                    skipped_count += 1
                elif dst_path.exists():
                    status = "skipped-conflict"
                    err = f"Destination '{dst}' already exists on disk"
                    skipped_count += 1
                else:
                    try:
                        dst_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(src_path), str(dst_path))
                        status = "ok"
                        err = ""
                        ok_count += 1
                        # Update resource current_path if file
                        if op["resource_id"]:
                            cursor.execute(
                                "UPDATE resources SET current_path = ?, status = 'moved' WHERE resource_id = ?",
                                (dst, op["resource_id"]),
                            )
                    except Exception as e:
                        status = "error"
                        err = str(e)
                        error_count += 1

                fin_iso = datetime.now().isoformat()
                cursor.execute(
                    """
                    UPDATE operations
                    SET status = ?, error_msg = ?, started_at = ?, finished_at = ?
                    WHERE op_id = ?
                    """,
                    (status, err, start_iso, fin_iso, op_id),
                )

                writer.writerow([
                    run_id, seq, fin_iso, op_type, src, dst,
                    op["category"], op["reason"], op["date_used"],
                    op["date_source"], op["files_inside"], op["bytes"],
                    status, err,
                ])

                total_processed = ok_count + skipped_count + error_count
                if total_processed % 1000 == 0:
                    self.conn.commit()
                    csvfile.flush()
                    print(f"    Executed {total_processed:,} / {len(ops):,} operations (moved: {ok_count:,}, errors: {error_count})...")

            self.conn.commit()
            csvfile.flush()

        # Clean up empty source directories left behind (e.g. Galería de Samsung/DCIM/Camera)
        for p in sorted(self.root.rglob("*"), key=lambda x: len(x.parts), reverse=True):
            if p.is_dir() and not any(p.iterdir()):
                rel = p.relative_to(self.root).as_posix()
                if not any(rel.lower().startswith(x) for x in ("ospd", "_manifest", ".git")):
                    try:
                        p.rmdir()
                    except OSError:
                        pass

        return ok_count, skipped_count, error_count, csv_path

    def rollback_run(
        self,
        target_run_id: str,
        undo_run_id: str,
        ignore_insync: bool = False,
    ) -> Tuple[int, int, Path]:
        """Rollback all successful operations from target_run_id in reverse order."""
        csv_path = generate_run_csv_path(self.manifest_dir, undo_run_id, is_undo=True)
        cursor = self.conn.cursor()

        cursor.execute(
            """
            SELECT op_id, seq, op_type, resource_id, src, dst, category, reason, bytes
            FROM operations
            WHERE run_id = ? AND status = 'ok'
            ORDER BY seq DESC
            """,
            (target_run_id,),
        )
        ops = cursor.fetchall()

        ok_count, err_count = 0, 0

        with open(csv_path, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(CSV_HEADER)

            for op in ops:
                op_id = op["op_id"]
                seq = op["seq"]
                op_type = op["op_type"]
                orig_src = op["src"].strip("/")
                orig_dst = op["dst"].strip("/")
                src_path = self.root / orig_dst   # Reversing: dst becomes source
                dst_path = self.root / orig_src   # Reversing: original src is target

                if not src_path.exists():
                    status = "skipped-missing"
                    err = f"Item to undo '{orig_dst}' does not exist"
                    err_count += 1
                else:
                    try:
                        dst_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(src_path), str(dst_path))
                        status = "ok"
                        err = ""
                        ok_count += 1
                        if op["resource_id"]:
                            cursor.execute(
                                "UPDATE resources SET current_path = ?, status = 'present' WHERE resource_id = ?",
                                (orig_src, op["resource_id"]),
                            )
                        cursor.execute(
                            "UPDATE operations SET status = 'undone', undone_by_run = ? WHERE op_id = ?",
                            (undo_run_id, op_id),
                        )
                        self.conn.commit()
                    except Exception as e:
                        status = "error"
                        err = str(e)
                        err_count += 1

                now_iso = datetime.now().isoformat()
                writer.writerow([
                    undo_run_id, seq, now_iso, op_type, orig_dst, orig_src,
                    "Undo", f"Rollback op #{op_id}", None, None,
                    None, op["bytes"], status, err,
                ])
                csvfile.flush()

        # Clean up empty directories left behind
        for p in sorted(self.root.rglob("*"), key=lambda x: len(x.parts), reverse=True):
            if p.is_dir() and not any(p.iterdir()):
                rel = p.relative_to(self.root).as_posix()
                if not any(rel.lower().startswith(x) for x in ("ospd", "_manifest", ".git")):
                    try:
                        p.rmdir()
                    except OSError:
                        pass

        return ok_count, err_count, csv_path
