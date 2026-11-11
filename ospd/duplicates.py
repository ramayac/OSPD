"""Duplicate detection and group management for OSPD."""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class CandidateDuplicate:
    resource_id: int
    current_path: str
    folder_class: str
    size_bytes: int
    sha256: Optional[str]
    exif_dt: Optional[str]
    camera_model: Optional[str]
    ext: str
    resolved_dt: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None


def find_duplicate_groups(conn: sqlite3.Connection, run_id: str) -> int:
    """Detect duplicate candidate groups and populate dup_groups & dup_members."""
    cursor = conn.cursor()

    # Clear previous pending groups
    cursor.execute("DELETE FROM dup_groups WHERE decision = 'pending'")

    # Fetch all present resources
    cursor.execute(
        """
        SELECT resource_id, current_path, folder_class, size_bytes, sha256, exif_dt, camera_model, ext, resolved_dt, width, height
        FROM resources
        WHERE status = 'present'
        """
    )
    rows = cursor.fetchall()
    candidates = [
        CandidateDuplicate(
            resource_id=r["resource_id"],
            current_path=r["current_path"],
            folder_class=r["folder_class"],
            size_bytes=r["size_bytes"],
            sha256=r["sha256"],
            exif_dt=r["exif_dt"],
            camera_model=r["camera_model"],
            ext=r["ext"],
            resolved_dt=r["resolved_dt"],
            width=r["width"],
            height=r["height"],
        )
        for r in rows
    ]

    # 1. Exact SHA-256 matching
    sha_map: Dict[str, List[CandidateDuplicate]] = defaultdict(list)
    for c in candidates:
        if c.sha256:
            sha_map[c.sha256].append(c)

    groups_created = 0
    assigned_ids: Set[int] = set()

    for sha, group in sha_map.items():
        if len(group) > 1:
            groups_created += 1
            cursor.execute(
                """
                INSERT INTO dup_groups (match_type, confidence, detected_run, decision)
                VALUES ('exact_sha256', '100%', ?, 'pending')
                """,
                (run_id,),
            )
            group_id = cursor.lastrowid

            # Determine proposed roles:
            # Rule: Album copy wins over CameraDump
            # If all are in CameraDump: pick the one without (0) or earlier name
            sorted_group = sorted(
                group,
                key=lambda x: (
                    0 if x.folder_class == "Album" else 1,
                    1 if "(0)" in x.current_path or "(1)" in x.current_path else 0,
                    x.current_path,
                ),
            )
            # The first one is 'keep', rest are 'duplicate'
            for idx, member in enumerate(sorted_group):
                role = "keep" if idx == 0 else "duplicate"
                cursor.execute(
                    """
                    INSERT INTO dup_members (group_id, resource_id, proposed_role)
                    VALUES (?, ?, ?)
                    """,
                    (group_id, member.resource_id, role),
                )
                assigned_ids.add(member.resource_id)

    # 2. HEIC + JPG pairs (same stem in same or nearby folder)
    stem_map: Dict[str, List[CandidateDuplicate]] = defaultdict(list)
    for c in candidates:
        if c.resource_id not in assigned_ids:
            stem = Path(c.current_path).stem.upper()
            stem_norm = re.sub(r"\(\d+\)$", "", stem)
            stem_map[stem_norm].append(c)

    for stem, group in stem_map.items():
        exts = {x.ext.lower() for x in group}
        if len(group) > 1 and (".heic" in exts or ".heif" in exts) and (".jpg" in exts or ".jpeg" in exts):
            groups_created += 1
            cursor.execute(
                """
                INSERT INTO dup_groups (match_type, confidence, detected_run, decision)
                VALUES ('heic_jpg_pair', 'medium', ?, 'pending')
                """,
                (run_id,),
            )
            group_id = cursor.lastrowid
            # HEIC preferred over JPG
            sorted_group = sorted(
                group,
                key=lambda x: (
                    0 if x.folder_class == "Album" else 1,
                    0 if x.ext.lower() in (".heic", ".heif") else 1,
                    x.current_path,
                ),
            )
            for idx, member in enumerate(sorted_group):
                role = "keep" if idx == 0 else "duplicate"
                cursor.execute(
                    """
                    INSERT INTO dup_members (group_id, resource_id, proposed_role)
                    VALUES (?, ?, ?)
                    """,
                    (group_id, member.resource_id, role),
                )
                assigned_ids.add(member.resource_id)

    # 3. Same EXIF datetime + camera model
    exif_map: Dict[Tuple[str, str], List[CandidateDuplicate]] = defaultdict(list)
    for c in candidates:
        if c.resource_id not in assigned_ids and c.exif_dt and c.camera_model:
            exif_map[(c.exif_dt, c.camera_model)].append(c)

    for (dt, model), group in exif_map.items():
        if len(group) > 1:
            groups_created += 1
            cursor.execute(
                """
                INSERT INTO dup_groups (match_type, confidence, detected_run, decision)
                VALUES ('same_exif_time_camera', 'high', ?, 'pending')
                """,
                (run_id,),
            )
            group_id = cursor.lastrowid
            sorted_group = sorted(
                group,
                key=lambda x: (
                    0 if x.folder_class == "Album" else 1,
                    -x.size_bytes,  # higher resolution/size preferred
                ),
            )
            for idx, member in enumerate(sorted_group):
                role = "keep" if idx == 0 else "duplicate"
                cursor.execute(
                    """
                    INSERT INTO dup_members (group_id, resource_id, proposed_role)
                    VALUES (?, ?, ?)
                    """,
                    (group_id, member.resource_id, role),
                )
                assigned_ids.add(member.resource_id)

    # 4. Same filename across different folders (e.g. Root vs Galería de Samsung)
    name_cross_map: Dict[str, List[CandidateDuplicate]] = defaultdict(list)
    for c in candidates:
        if c.resource_id not in assigned_ids:
            fn = Path(c.current_path).name.lower()
            name_cross_map[fn].append(c)

    for fn, group in name_cross_map.items():
        if len(group) > 1:
            parents = {str(Path(x.current_path).parent).lower() for x in group}
            if len(parents) > 1:
                base_sz = group[0].size_bytes
                size_similar = all(abs(x.size_bytes - base_sz) <= max(100000, 0.20 * max(x.size_bytes, base_sz)) for x in group)
                same_dt = len({x.resolved_dt for x in group if x.resolved_dt}) == 1
                same_dims = len({(x.width, x.height) for x in group if x.width and x.height}) == 1

                if size_similar or same_dt or same_dims:
                    groups_created += 1
                    conf = "high" if (same_dt or same_dims) else "medium"
                    cursor.execute(
                        """
                        INSERT INTO dup_groups (match_type, confidence, detected_run, decision)
                        VALUES ('same_name_cross_folder', ?, ?, 'pending')
                        """,
                        (conf, run_id),
                    )
                    group_id = cursor.lastrowid
                    sorted_group = sorted(
                        group,
                        key=lambda x: (
                            0 if x.folder_class == "Album" else 1,
                            -x.size_bytes,  # higher resolution/size preferred
                            x.current_path,
                        ),
                    )
                    for idx, member in enumerate(sorted_group):
                        role = "keep" if idx == 0 else "duplicate"
                        cursor.execute(
                            """
                            INSERT INTO dup_members (group_id, resource_id, proposed_role)
                            VALUES (?, ?, ?)
                            """,
                            (group_id, member.resource_id, role),
                        )
                        assigned_ids.add(member.resource_id)

    return groups_created


def bulk_approve_exact_duplicates(conn: sqlite3.Connection) -> Tuple[bool, str, int]:
    """Approve all pending exact_sha256 duplicate groups.

    Requirements:
    - Latest scan must have dup_detection_complete = 1
    - No unapproved conflict
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT dup_detection_complete
        FROM runs
        WHERE kind = 'scan' AND status = 'ok'
        ORDER BY started_at DESC LIMIT 1
        """
    )
    row = cursor.fetchone()
    if not row or not row["dup_detection_complete"]:
        return False, "Cannot bulk-approve: No complete scan with dup_detection_complete=1 found", 0

    cursor.execute(
        """
        UPDATE dup_groups
        SET decision = 'approved',
            reviewed_at = datetime('now'),
            reviewer_note = 'bulk-approved exact sha256'
        WHERE match_type = 'exact_sha256' AND decision = 'pending'
        """
    )
    count = cursor.rowcount
    return True, f"Bulk-approved {count} exact duplicate groups", count


def bulk_approve_all_duplicates(conn: sqlite3.Connection) -> Tuple[bool, str, int]:
    """Approve all pending duplicate groups (exact sha256, exif time camera, same name cross folder, heic/jpg)."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT dup_detection_complete
        FROM runs
        WHERE kind = 'scan' AND status = 'ok'
        ORDER BY started_at DESC LIMIT 1
        """
    )
    row = cursor.fetchone()
    if not row or not row["dup_detection_complete"]:
        return False, "Cannot bulk-approve: No complete scan with dup_detection_complete=1 found", 0

    cursor.execute(
        """
        UPDATE dup_groups
        SET decision = 'approved',
            reviewed_at = datetime('now'),
            reviewer_note = 'bulk-approved all duplicate groups'
        WHERE decision = 'pending'
        """
    )
    count = cursor.rowcount
    return True, f"Bulk-approved {count} duplicate groups", count

