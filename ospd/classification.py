"""Folder and file classification rules for OSPD.

Configurable and extensible for any user or camera setup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


DEFAULT_IGNORE_DIRS = {
    "ospd", "_manifest", "_duplicates", ".dtrash", ".git", ".idea",
    "albums", "gallery", "wallpapers", "screenshots", "art", "unsorted",
    "data", "data-run", "backup",
}

DEFAULT_WALLPAPER_PATTERNS = [
    r"^wallpapers?(\s+\d+)?$",
    r"^backgrounds?$",
    r"^desktop(\s+backgrounds?)?$",
    r"^ubuntu release assets$",
    r"^unsplash$",
    r"^theoriginalfilesofsomewindowswallpapers$",
    r"^gumroad",
    r"^telus assets laptop monitor$",
]

DEFAULT_ART_PATTERNS = [
    r"^art$",
    r"^sketches$",
    r"^processing sketches$",
    r"^powerlamb",
    r"^poorly drawn",
]

DEFAULT_LOWRES_FOLDERS = [
    r"^tilesets$",
    r"^thumbnails?$",
]

DEFAULT_CAMERA_DUMP_PATTERNS = [
    r"^\.$",
    r"^camera(\s+roll)?$",
    r"^dcim(/.*)?$",
    r"^samsung(\s+gallery)?(/.*)?$",
    r"^galer[ií]a(\s+de\s+samsung)?(/.*)?$",
    r"^google(\s+photos)?(/.*)?$",
    r"^cybershot$",
    r"^lumix$",
    r"^photo booth library(/.*)?$",
    r"^camera uploads?$",
]

DEFAULT_CUSTOM_ALBUMS = {
    r"^tezza_": "Albums/Tezza",
}

# Active working rules (can be customized via set_classification_rules or config.json)
IGNORE_DIRS: Set[str] = set(DEFAULT_IGNORE_DIRS)
WALLPAPER_PATTERNS: List[str] = list(DEFAULT_WALLPAPER_PATTERNS)
ART_PATTERNS: List[str] = list(DEFAULT_ART_PATTERNS)
LOWRES_FOLDERS: List[str] = list(DEFAULT_LOWRES_FOLDERS)
CAMERA_DUMP_PATTERNS: List[str] = list(DEFAULT_CAMERA_DUMP_PATTERNS)
CUSTOM_ALBUMS: Dict[str, str] = dict(DEFAULT_CUSTOM_ALBUMS)


def set_classification_rules(
    camera_dumps: Optional[List[str]] = None,
    wallpapers: Optional[List[str]] = None,
    art: Optional[List[str]] = None,
    lowres: Optional[List[str]] = None,
    custom_albums: Optional[Dict[str, str]] = None,
    extra_ignore: Optional[Set[str]] = None,
) -> None:
    """Override or extend active classification rules from configuration."""
    global IGNORE_DIRS, WALLPAPER_PATTERNS, ART_PATTERNS, LOWRES_FOLDERS, CAMERA_DUMP_PATTERNS, CUSTOM_ALBUMS

    if camera_dumps:
        # User defined patterns take precedence, while preserving root '.'
        CAMERA_DUMP_PATTERNS = list(dict.fromkeys([r"^\.$"] + camera_dumps))
    else:
        CAMERA_DUMP_PATTERNS = list(DEFAULT_CAMERA_DUMP_PATTERNS)

    if wallpapers:
        WALLPAPER_PATTERNS = list(wallpapers)
    else:
        WALLPAPER_PATTERNS = list(DEFAULT_WALLPAPER_PATTERNS)

    if art:
        ART_PATTERNS = list(art)
    else:
        ART_PATTERNS = list(DEFAULT_ART_PATTERNS)

    if lowres:
        LOWRES_FOLDERS = list(lowres)
    else:
        LOWRES_FOLDERS = list(DEFAULT_LOWRES_FOLDERS)

    if custom_albums is not None:
        merged_albums = dict(DEFAULT_CUSTOM_ALBUMS)
        merged_albums.update(custom_albums)
        CUSTOM_ALBUMS = merged_albums
    else:
        CUSTOM_ALBUMS = dict(DEFAULT_CUSTOM_ALBUMS)

    if extra_ignore:
        IGNORE_DIRS.update(extra_ignore)


def reset_classification_rules() -> None:
    """Reset active classification rules back to defaults."""
    global IGNORE_DIRS, WALLPAPER_PATTERNS, ART_PATTERNS, LOWRES_FOLDERS, CAMERA_DUMP_PATTERNS, CUSTOM_ALBUMS
    IGNORE_DIRS = set(DEFAULT_IGNORE_DIRS)
    WALLPAPER_PATTERNS = list(DEFAULT_WALLPAPER_PATTERNS)
    ART_PATTERNS = list(DEFAULT_ART_PATTERNS)
    LOWRES_FOLDERS = list(DEFAULT_LOWRES_FOLDERS)
    CAMERA_DUMP_PATTERNS = list(DEFAULT_CAMERA_DUMP_PATTERNS)
    CUSTOM_ALBUMS = dict(DEFAULT_CUSTOM_ALBUMS)


def normalize_top_folder(rel_path: str) -> str:
    """Return the first directory of a relative path or '.' if file is in root."""
    parts = Path(rel_path).parts
    if len(parts) <= 1:
        return "."
    return parts[0]


def is_ignored_path(rel_path: str) -> bool:
    """Check if relative path belongs to system/work directories."""
    parts = [p.lower() for p in Path(rel_path).parts]
    for p in parts:
        if p in IGNORE_DIRS or p.startswith(".~") or p.endswith(".insyncdl"):
            return True
    return False


def classify_folder_name(folder_name: str) -> Tuple[str, str]:
    """Classify folder based on name. Returns (folder_class, target_category).

    Classes: 'Wallpapers', 'Art', 'LowRes', 'CameraDump', 'Screenshots', 'Album'
    """
    fn = folder_name.strip().lower()

    if fn == ".":
        return "CameraDump", "Gallery"

    for pat in CAMERA_DUMP_PATTERNS:
        if re.search(pat, fn):
            return "CameraDump", "Gallery"

    if fn == "screenshots":
        return "Screenshots", "Screenshots"

    for pat in WALLPAPER_PATTERNS:
        if re.search(pat, fn):
            return "Wallpapers", "Wallpapers"

    for pat in ART_PATTERNS:
        if re.search(pat, fn):
            return "Art", "Art"

    for pat in LOWRES_FOLDERS:
        if re.search(pat, fn):
            return "LowRes", "Unsorted/LowRes"

    # Default: Any other folder is treated as an Album
    return "Album", "Albums"


def is_weird_filename(filename: str) -> bool:
    """Detect hash-like, UUID, or social media download names."""
    stem = Path(filename).stem
    # UUID pattern
    if re.match(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", stem):
        return True
    # Hex or MD5/SHA hash pattern (20+ hex chars)
    if re.match(r"^[0-9a-fA-F]{20,}$", stem):
        return True
    # Facebook / WhatsApp / Reddit download patterns with numeric segments
    if re.match(r"^\d+_\d+_\d+_[0-9a-zA-Z_]+$", stem):
        return True
    if re.match(r"^\d{9,}_\d{9,}", stem):
        return True
    if re.match(r"^[a-zA-Z0-9]{13,20}$", stem) and not any(c in stem for c in "_ -"):
        # Do not treat pure 13-digit Unix millisecond timestamps as weird names
        if re.match(r"^1[5-9]\d{11}$", stem):
            return False
        return True
    return False


def is_screenshot_filename(filename: str) -> bool:
    """Check if filename indicates a screenshot."""
    fn = filename.lower()
    return (
        fn.startswith("screenshot")
        or "screenshot_" in fn
        or fn.startswith("screen shot")
        or fn.startswith("smartselect_")
        or fn.startswith("aiselect_")
        or fn.startswith("screen_recording_")
        or "screenshot" in fn
    )


def classify_camera_dump_file(
    filename: str,
    ext: str,
    kind: str,
    resolved_dt: Optional[str],
    is_lowres_candidate: bool,
    lowres_decision: str,
    lowres_match: str,
) -> Tuple[str, str]:
    """Classify a loose file from a camera dump.

    Returns (category, subfolder_path).
    """
    ext_l = ext.lower()
    fn_l = filename.lower()

    # 1. Non-media files
    if kind not in ("image", "video"):
        return "Unsorted/Other", "Unsorted/Other"

    # 2. Custom Album patterns (e.g. Tezza photo editing app exports, etc.)
    for pat, dst in CUSTOM_ALBUMS.items():
        if re.search(pat, fn_l):
            return "Album", dst

    # 3. Screenshots (including SmartSelect, AISelect, Screen_Recording)
    if is_screenshot_filename(filename):
        year = resolved_dt[:4] if resolved_dt else "Unknown"
        return "Screenshots", f"Screenshots/{year}"

    # 4. Animated (.gif)
    if ext_l == ".gif":
        return "Unsorted/Animated", "Unsorted/Animated"

    # 5. Weird names or webp
    if ext_l == ".webp" or is_weird_filename(filename):
        return "Unsorted/WeirdNames", "Unsorted/WeirdNames"

    # 6. Low-res candidate
    if lowres_decision == "LowRes" or (lowres_decision == "pending" and lowres_match == "both"):
        return "Unsorted/LowRes", "Unsorted/LowRes"

    # 7. Missing date
    if not resolved_dt:
        return "Unsorted/NoDate", "Unsorted/NoDate"

    # 8. Valid gallery timeline
    try:
        dt_parts = resolved_dt.split(" ")[0].split("-")
        year, month = dt_parts[0], f"{dt_parts[0]}-{dt_parts[1]}"
        return "Gallery", f"Gallery/{year}/{month}"
    except Exception:
        return "Unsorted/NoDate", "Unsorted/NoDate"
