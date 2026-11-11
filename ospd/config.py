"""Configuration management for OSPD.

Provides flexible, developer-friendly configuration via:
1. CLI flags (--root, --data-dir, --config)
2. Environment variables (PICTURES_ROOT, OSPD_ROOT, OSPD_DATA_DIR)
3. Config file (config.json, ospd.json)
4. Sensible defaults
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


def get_default_data_dir() -> Path:
    """Return default data directory where DB and reports live."""
    # Default to a local data directory inside OSPD
    ospd_root = Path(__file__).resolve().parent.parent
    return ospd_root / "data-run"


@dataclass
class Config:
    pictures_root: Path
    data_dir: Path = field(default_factory=get_default_data_dir)
    sync_client: str = "none"  # "auto", "insync", "onedrive", "dropbox", "none"
    untrusted_mtime_windows: List[Tuple[datetime, datetime]] = field(default_factory=list)
    camera_dump_patterns: List[str] = field(default_factory=list)
    wallpaper_patterns: List[str] = field(default_factory=list)
    art_patterns: List[str] = field(default_factory=list)
    lowres_folders: List[str] = field(default_factory=list)
    custom_albums: Dict[str, str] = field(default_factory=dict)
    ignore_dirs: Set[str] = field(default_factory=set)


def parse_date_window(window_str_or_dict: Any) -> Optional[Tuple[datetime, datetime]]:
    """Parse a date window from dict {"start": "...", "end": "..."} or string "START:END"."""
    if isinstance(window_str_or_dict, dict):
        s_raw = window_str_or_dict.get("start")
        e_raw = window_str_or_dict.get("end")
        if s_raw and e_raw:
            try:
                s = datetime.fromisoformat(str(s_raw))
                e = datetime.fromisoformat(str(e_raw))
                return s, e
            except ValueError:
                pass
    elif isinstance(window_str_or_dict, str) and ":" in window_str_or_dict:
        parts = window_str_or_dict.split(":", 1)
        try:
            s = datetime.fromisoformat(parts[0].strip())
            e = datetime.fromisoformat(parts[1].strip())
            return s, e
        except ValueError:
            pass
    return None


def find_config_file(custom_path: Optional[str] = None) -> Optional[Path]:
    """Find configuration file in standard search locations."""
    if custom_path:
        p = Path(custom_path).expanduser().resolve()
        if p.exists():
            return p
        raise FileNotFoundError(f"Specified config file not found: {custom_path}")

    ospd_root = Path(__file__).resolve().parent.parent
    search_locations = [
        Path.cwd() / "config.json",
        Path.cwd() / "ospd.json",
        ospd_root / "config.json",
        ospd_root / "ospd.json",
        Path.home() / ".ospd" / "config.json",
    ]

    for loc in search_locations:
        if loc.exists():
            return loc
    return None


def load_config(args: Optional[Any] = None) -> Config:
    """Load configuration from config file, environment, and CLI flags."""
    cfg_file = find_config_file(getattr(args, "config", None))
    raw: Dict[str, Any] = {}

    if cfg_file and cfg_file.exists():
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            print(f"[!] Warning: Failed to parse config file {cfg_file}: {e}")

    # 1. Resolve pictures_root
    # Priority: CLI flag > Environment > Config file > Default fallback
    cli_root = getattr(args, "root", None)
    env_root = os.getenv("PICTURES_ROOT") or os.getenv("OSPD_ROOT") or os.getenv("PICTURES_DIR")
    cfg_root = raw.get("pictures_root")

    if cli_root:
        pic_root_path = Path(cli_root).expanduser().resolve()
    elif env_root:
        pic_root_path = Path(env_root).expanduser().resolve()
    elif cfg_root:
        pic_root_path = Path(cfg_root).expanduser().resolve()
    else:
        # Fallback check: If current working directory or its parent looks like a photo library
        pic_root_path = Path.cwd().resolve()

    # 2. Resolve data_dir
    # Priority: CLI flag > Environment > Config file > Default (data/)
    cli_data = getattr(args, "data_dir", None)
    env_data = os.getenv("OSPD_DATA_DIR")
    cfg_data = raw.get("data_dir")

    ospd_root = Path(__file__).resolve().parent.parent
    if cli_data:
        data_dir_path = Path(cli_data).expanduser().resolve()
    elif env_data:
        data_dir_path = Path(env_data).expanduser().resolve()
    elif cfg_data:
        data_dir_path = Path(cfg_data).expanduser().resolve()
    else:
        data_dir_path = ospd_root / "data-run"

    data_dir_path.mkdir(parents=True, exist_ok=True)

    # 3. Resolve sync client
    sync_client = raw.get("sync_client", "none")

    # 4. Resolve untrusted date windows
    windows: List[Tuple[datetime, datetime]] = []
    for w in raw.get("untrusted_mtime_windows", []):
        parsed = parse_date_window(w)
        if parsed:
            windows.append(parsed)

    cfg = Config(
        pictures_root=pic_root_path,
        data_dir=data_dir_path,
        sync_client=sync_client,
        untrusted_mtime_windows=windows,
        camera_dump_patterns=raw.get("camera_dump_patterns", []),
        wallpaper_patterns=raw.get("wallpaper_patterns", []),
        art_patterns=raw.get("art_patterns", []),
        lowres_folders=raw.get("lowres_folders", []),
        custom_albums=raw.get("custom_albums", {}),
        ignore_dirs=set(raw.get("ignore_dirs", [])),
    )

    # Apply configuration to submodules
    from ospd.classification import set_classification_rules
    from ospd.metadata import set_untrusted_mtime_windows

    set_classification_rules(
        camera_dumps=cfg.camera_dump_patterns,
        wallpapers=cfg.wallpaper_patterns,
        art=cfg.art_patterns,
        lowres=cfg.lowres_folders,
        custom_albums=cfg.custom_albums,
        extra_ignore=cfg.ignore_dirs,
    )
    set_untrusted_mtime_windows(cfg.untrusted_mtime_windows)

    return cfg
