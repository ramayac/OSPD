"""Operation planning engine for OSPD.

Generates deterministic, collision-free moves for folders and files.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from ospd.classification import classify_camera_dump_file


@dataclass
class PlannedOp:
    seq: int
    op_type: str            # 'folder' or 'file'
    resource_id: Optional[int]
    src: str
    dst: str
    category: str
    reason: str
    date_used: Optional[str]
    date_source: Optional[str]
    files_inside: Optional[int]
    bytes: Optional[int]


def format_gallery_filename(resolved_dt: str, ext: str, index: int = 0) -> str:
    """Format gallery filename from resolved datetime."""
    # resolved_dt is YYYY-MM-DD HH:MM:SS
    clean = re.sub(r"[- :]", "", resolved_dt)
    if len(clean) >= 14:
        base = f"{clean[:8]}_{clean[8:14]}"
    else:
        base = clean
    suffix = f"_{index}" if index > 0 else ""
    return f"{base}{suffix}{ext.lower()}"


def plan_operations(conn: sqlite3.Connection, run_id: str) -> List[PlannedOp]:
    """Build complete list of planned operations."""
    cursor = conn.cursor()
    planned_ops: List[PlannedOp] = []
    used_destinations: Set[str] = set()
    used_destinations_ci: Set[str] = set()  # Case-insensitive for OneDrive/Windows
    seq = 0

    def add_op(
        op_type: str,
        resource_id: Optional[int],
        src: str,
        dst: str,
        category: str,
        reason: str,
        date_used: Optional[str] = None,
        date_source: Optional[str] = None,
        files_inside: Optional[int] = None,
        num_bytes: Optional[int] = None,
    ) -> bool:
        nonlocal seq
        # Do not move if src == dst
        if src.rstrip("/") == dst.rstrip("/"):
            return False

        dst_ci = dst.lower()
        if dst_ci in used_destinations_ci:
            # Suffix to resolve collision
            p = Path(dst)
            counter = 1
            while True:
                alt_dst = str(p.parent / f"{p.stem}_{counter}{p.suffix}")
                if alt_dst.lower() not in used_destinations_ci:
                    dst = alt_dst
                    dst_ci = alt_dst.lower()
                    break
                counter += 1

        seq += 1
        used_destinations.add(dst)
        used_destinations_ci.add(dst_ci)

        planned_ops.append(
            PlannedOp(
                seq=seq,
                op_type=op_type,
                resource_id=resource_id,
                src=src,
                dst=dst,
                category=category,
                reason=reason,
                date_used=date_used,
                date_source=date_source,
                files_inside=files_inside,
                bytes=num_bytes,
            )
        )
        return True

    # 1. Whole-folder moves (Albums, Wallpapers, Art, LowRes sets)
    cursor.execute(
        """
        SELECT folder_path, folder_class, target_category, item_count, notes
        FROM folders
        WHERE folder_class NOT IN ('CameraDump', 'Screenshots', 'Ignore')
        """
    )
    folder_rows = cursor.fetchall()
    for f in folder_rows:
        src_folder = f["folder_path"]
        target_cat = f["target_category"]
        dst_folder = f"{target_cat}/{Path(src_folder).name}"
        add_op(
            op_type="folder",
            resource_id=None,
            src=src_folder,
            dst=dst_folder,
            category=f["folder_class"],
            reason=f"Whole folder classification: {f['folder_class']} -> {target_cat}",
            files_inside=f["item_count"],
        )

    # 2. Check duplicate statuses
    cursor.execute(
        """
        SELECT dm.resource_id, dm.proposed_role, dg.decision
        FROM dup_members dm
        JOIN dup_groups dg ON dm.group_id = dg.group_id
        """
    )
    dup_status = {r["resource_id"]: (r["proposed_role"], r["decision"]) for r in cursor.fetchall()}

    # 3. Process individual resources in CameraDump
    cursor.execute(
        """
        SELECT resource_id, current_path, folder_class, kind, ext, size_bytes,
               resolved_dt, date_source, is_lowres_candidate, lowres_match, lowres_decision, live_pair_id
        FROM resources
        WHERE status = 'present' AND folder_class = 'CameraDump'
        ORDER BY current_path
        """
    )
    resources = cursor.fetchall()

    # Track base names for Live Photo pairs
    live_pair_names: Dict[str, str] = {}

    for r in resources:
        res_id = r["resource_id"]
        src_path = r["current_path"]
        fname = Path(src_path).name

        # Duplicate handling
        if res_id in dup_status:
            role, decision = dup_status[res_id]
            if decision == "pending":
                # Leave untouched until user reviews
                continue
            elif decision == "approved" and role == "duplicate":
                # Move to quarantine
                dst_path = f"_Duplicates/{src_path}"
                add_op(
                    op_type="file",
                    resource_id=res_id,
                    src=src_path,
                    dst=dst_path,
                    category="Duplicate",
                    reason="Approved duplicate quarantined to _Duplicates/",
                    num_bytes=r["size_bytes"],
                )
                continue

        # Classify camera dump file
        cat, subfolder = classify_camera_dump_file(
            filename=fname,
            ext=r["ext"],
            kind=r["kind"],
            resolved_dt=r["resolved_dt"],
            is_lowres_candidate=bool(r["is_lowres_candidate"]),
            lowres_decision=r["lowres_decision"],
            lowres_match=r["lowres_match"],
        )

        if cat == "Gallery":
            # Live photo check
            live_id = r["live_pair_id"]
            if live_id and live_id in live_pair_names:
                base_stem = live_pair_names[live_id]
                new_name = f"{base_stem}{r['ext'].lower()}"
            else:
                base_stem = format_gallery_filename(r["resolved_dt"], "")
                if live_id:
                    live_pair_names[live_id] = base_stem
                new_name = f"{base_stem}{r['ext'].lower()}"

            dst_path = f"{subfolder}/{new_name}"
            reason = f"Gallery timeline ({r['date_source']}: {r['resolved_dt']})"
        elif cat == "Screenshots":
            dst_path = f"{subfolder}/{fname}"
            reason = "Screenshot identified by filename"
        else:
            dst_path = f"{subfolder}/{fname}"
            reason = f"Placed in {cat}"

        add_op(
            op_type="file",
            resource_id=res_id,
            src=src_path,
            dst=dst_path,
            category=cat,
            reason=reason,
            date_used=r["resolved_dt"],
            date_source=r["date_source"],
            num_bytes=r["size_bytes"],
        )

    # Clear previous unexecuted planned operations
    cursor.execute("DELETE FROM operations WHERE status IN ('planned', 'dryrun-ok')")
    for op in planned_ops:
        cursor.execute(
            """
            INSERT INTO operations (
                run_id, seq, op_type, resource_id, src, dst, category, reason,
                date_used, date_source, files_inside, bytes, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned')
            """,
            (
                run_id,
                op.seq,
                op.op_type,
                op.resource_id,
                op.src,
                op.dst,
                op.category,
                op.reason,
                op.date_used,
                op.date_source,
                op.files_inside,
                op.bytes,
            ),
        )

    return planned_ops
