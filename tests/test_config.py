"""Unit tests for OSPD configuration loading, resolution precedence, and custom rules."""

import json
from datetime import datetime
from pathlib import Path
import pytest

from ospd.config import Config, load_config, parse_date_window
from ospd.classification import classify_folder_name, classify_camera_dump_file, CUSTOM_ALBUMS, reset_classification_rules
from ospd.metadata import reset_untrusted_mtime_windows


def test_parse_date_window():
    # String format
    w1 = parse_date_window("2026-10-30:2026-11-01")
    assert w1 is not None
    assert w1[0] == datetime(2026, 10, 30)
    assert w1[1] == datetime(2026, 11, 1)

    # Dict format
    w2 = parse_date_window({"start": "2024-06-01T00:00:00", "end": "2024-06-02T23:59:59"})
    assert w2 is not None
    assert w2[0].year == 2024
    assert w2[1].day == 2

    # Invalid
    assert parse_date_window("invalid") is None


def test_config_file_and_cli_overrides(tmp_path: Path):
    cfg_file = tmp_path / "custom_config.json"
    pic_dir = tmp_path / "MyPhotos"
    pic_dir.mkdir()
    data_dir = tmp_path / "MyData"
    data_dir.mkdir()

    cfg_data = {
        "pictures_root": str(pic_dir),
        "data_dir": str(data_dir),
        "sync_client": "none",
        "untrusted_mtime_windows": [
            {"start": "2024-01-01", "end": "2024-01-05"}
        ],
        "camera_dump_patterns": ["^custom_camera$"],
        "wallpaper_patterns": ["^custom_wallpapers$"],
        "custom_albums": {
            "^vsco_": "Albums/VSCO"
        }
    }
    cfg_file.write_text(json.dumps(cfg_data))

    # Test load from config file
    class DummyArgs:
        config = str(cfg_file)
        root = None
        data_dir = None

    cfg = load_config(DummyArgs())
    assert cfg.pictures_root == pic_dir
    assert cfg.data_dir == data_dir
    assert cfg.sync_client == "none"
    assert len(cfg.untrusted_mtime_windows) == 1

    # Verify custom classification rules took effect
    f_class, target = classify_folder_name("custom_camera")
    assert f_class == "CameraDump"
    assert target == "Gallery"

    f_class, target = classify_folder_name("custom_wallpapers")
    assert f_class == "Wallpapers"

    cat, subfolder = classify_camera_dump_file(
        filename="vsco_051421.jpg",
        ext=".jpg",
        kind="image",
        resolved_dt="2021-05-14 12:00:00",
        is_lowres_candidate=False,
        lowres_decision="pending",
        lowres_match="none",
    )
    assert cat == "Album"
    assert subfolder == "Albums/VSCO"

    # Test CLI flag overrides config file
    override_dir = tmp_path / "CLIOverride"
    override_dir.mkdir()
    DummyArgs.root = str(override_dir)

    cfg2 = load_config(DummyArgs())
    assert cfg2.pictures_root == override_dir

    # Reset rules back to defaults
    reset_classification_rules()
    reset_untrusted_mtime_windows()
