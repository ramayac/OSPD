"""Unit tests for in-memory and mock dry-run simulations."""

from pathlib import Path
import sqlite3
import pytest

from ospd.simulator import run_in_memory_simulation, run_mock_execution_test


def test_in_memory_simulation_collision():
    # Two operations targeting the same destination (even case-insensitively)
    ops = [
        {"src": "a.jpg", "dst": "Gallery/2024/photo.jpg", "op_type": "file"},
        {"src": "b.jpg", "dst": "Gallery/2024/PHOTO.JPG", "op_type": "file"},
    ]
    ok, errors = run_in_memory_simulation(ops, set(), set())
    assert ok is False
    assert any("Collision" in e for e in errors)


def test_in_memory_simulation_cycle():
    # Moving folder into its own subpath
    ops = [
        {"src": "Albums/Trip", "dst": "Albums/Trip/Sub", "op_type": "folder"},
    ]
    ok, errors = run_in_memory_simulation(ops, set(), set())
    assert ok is False
    assert any("Cycle" in e for e in errors)


def test_mock_execution_and_rollback():
    # Valid operations simulated on temporary files
    ops = [
        {"src": "old_folder", "dst": "Albums/old_folder", "op_type": "folder"},
        {"src": "photo.jpg", "dst": "Gallery/2024/20240101.jpg", "op_type": "file"},
    ]
    ok, errors = run_mock_execution_test(ops)
    assert ok is True
    assert len(errors) == 0
