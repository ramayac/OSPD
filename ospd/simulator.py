"""Simulation and validation engine for OSPD.

Performs:
1. In-memory graph validation of operations (capped at 5,000 ops)
2. Shadow-tree execution & rollback test in /tmp with dummy files
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class SimulationResult:
    passed: bool
    total_checked: int
    memory_errors: List[str]
    mock_test_passed: bool
    mock_test_errors: List[str]


def run_in_memory_simulation(
    ops: List[sqlite3.Row],
    existing_files: Set[str],
    existing_dirs: Set[str],
    limit: int = 20000,
) -> Tuple[bool, List[str]]:
    """Validate operations in-memory for collisions, cycles, and dependencies."""
    errors: List[str] = []
    current_files = {p.lower(): p for p in existing_files}
    current_dirs = {p.lower(): p for p in existing_dirs}

    dest_seen_ci: Dict[str, str] = {}
    moved_dirs: Set[str] = set()

    for op in ops[:limit]:
        src = op["src"].strip("/")
        dst = op["dst"].strip("/")
        op_type = op["op_type"]
        src_ci = src.lower()
        dst_ci = dst.lower()

        # Check destination uniqueness
        if dst_ci in dest_seen_ci:
            errors.append(
                f"Collision: dst '{dst}' already targeted by op moving '{dest_seen_ci[dst_ci]}'"
            )
        dest_seen_ci[dst_ci] = src

        # Check folder cycles
        if op_type == "folder":
            if dst_ci == src_ci or dst_ci.startswith(src_ci + "/"):
                errors.append(f"Cycle: cannot move folder '{src}' into its own subpath '{dst}'")
            moved_dirs.add(src_ci)
        elif op_type == "file":
            # Check if file's parent was already moved
            parent = str(Path(src).parent).lower()
            if parent in moved_dirs and parent != ".":
                errors.append(
                    f"Ordering conflict: file '{src}' moved after its parent folder '{parent}' was already moved"
                )

    return len(errors) == 0, errors


def run_mock_execution_test(
    ops: List[sqlite3.Row],
    limit: int = 20000,
) -> Tuple[bool, List[str]]:
    """Create a temporary sandbox mirror, execute moves, execute rollback, verify integrity."""
    errors: List[str] = []
    with tempfile.TemporaryDirectory(prefix="ospd_mock_") as tmpdir:
        root = Path(tmpdir)
        tested_ops = ops[:limit]

        # 1. Create dummy source structure
        for op in tested_ops:
            src = op["src"].strip("/")
            src_path = root / src
            if op["op_type"] == "folder":
                src_path.mkdir(parents=True, exist_ok=True)
                (src_path / ".dummy").touch()
            else:
                src_path.parent.mkdir(parents=True, exist_ok=True)
                src_path.touch()

        # Snapshot initial state
        initial_paths = sorted(
            [p.relative_to(root).as_posix() for p in root.rglob("*")]
        )

        # 2. Execute moves (Forward)
        for op in tested_ops:
            src = op["src"].strip("/")
            dst = op["dst"].strip("/")
            src_path = root / src
            dst_path = root / dst

            if not src_path.exists():
                errors.append(f"Mock execution failed: src '{src}' does not exist")
                return False, errors

            dst_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(src_path), str(dst_path))
            except Exception as e:
                errors.append(f"Mock move failed for '{src}' -> '{dst}': {e}")
                return False, errors

        # 3. Rollback (Reverse)
        for op in reversed(tested_ops):
            src = op["src"].strip("/")
            dst = op["dst"].strip("/")
            src_path = root / src
            dst_path = root / dst

            if not dst_path.exists():
                errors.append(f"Mock rollback failed: dst '{dst}' missing during undo")
                return False, errors

            src_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(dst_path), str(src_path))
            except Exception as e:
                errors.append(f"Mock rollback error for '{dst}' -> '{src}': {e}")
                return False, errors

        # Clean empty directories created during test
        for p in sorted(root.rglob("*"), key=lambda x: len(x.parts), reverse=True):
            if p.is_dir() and not any(p.iterdir()):
                try:
                    p.rmdir()
                except OSError:
                    pass

        # 4. Compare state
        final_paths = sorted(
            [p.relative_to(root).as_posix() for p in root.rglob("*")]
        )
        if initial_paths != final_paths:
            errors.append(
                f"State mismatch after undo: initial items {len(initial_paths)}, final items {len(final_paths)}"
            )
            return False, errors

    return True, []


def run_full_dry_run_test(
    conn: sqlite3.Connection,
    pictures_root: Path,
    limit: int = 20000,
) -> SimulationResult:
    """Run in-memory + mock tests for planned operations."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT op_id, run_id, seq, op_type, resource_id, src, dst, category, reason
        FROM operations
        WHERE status = 'planned'
        ORDER BY seq ASC
        """
    )
    ops = cursor.fetchall()

    if not ops:
        return SimulationResult(
            passed=True,
            total_checked=0,
            memory_errors=[],
            mock_test_passed=True,
            mock_test_errors=[],
        )

    # Collect existing files and dirs
    existing_files: Set[str] = set()
    existing_dirs: Set[str] = set()

    for p in pictures_root.rglob("*"):
        rel = p.relative_to(pictures_root).as_posix()
        if p.is_dir():
            existing_dirs.add(rel)
        elif p.is_file():
            existing_files.add(rel)

    mem_ok, mem_errors = run_in_memory_simulation(ops, existing_files, existing_dirs, limit=limit)
    mock_ok, mock_errors = run_mock_execution_test(ops, limit=limit)

    passed = mem_ok and mock_ok

    # Update operation statuses in DB
    status_val = "dryrun-ok" if passed else "dryrun-fail"
    for op in ops[:limit]:
        cursor.execute(
            "UPDATE operations SET status = ? WHERE op_id = ?",
            (status_val, op["op_id"]),
        )
    conn.commit()

    return SimulationResult(
        passed=passed,
        total_checked=min(len(ops), limit),
        memory_errors=mem_errors,
        mock_test_passed=mock_ok,
        mock_test_errors=mock_errors,
    )
