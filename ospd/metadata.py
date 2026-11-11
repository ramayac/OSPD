"""Metadata extraction and date resolution for OSPD.

Extracts dimensions, EXIF dates, video creation times, Google Takeout sidecars,
filename dates, and mtime (excluding the untrusted Oct 30 - Nov 1 2026 window).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


IMAGE_EXTS = {
    ".heic", ".heif", ".jpg", ".jpeg", ".png", ".webp",
    ".gif", ".bmp", ".tif", ".tiff", ".avif", ".raw", ".cr2", ".nef",
    ".dng", ".jfif",
}
VIDEO_EXTS = {
    ".mov", ".mp4", ".m4v", ".avi", ".mkv", ".3gp", ".webm",
}
SIDECAR_EXTS = {".json"}

# Untrusted mtime range(s): can be configured via set_untrusted_mtime_windows or config.json
DEFAULT_UNTRUSTED_MTIME_WINDOWS = [
    (datetime(2026, 10, 30, 0, 0, 0), datetime(2026, 11, 1, 23, 59, 59))
]
UNTRUSTED_MTIME_WINDOWS: List[Tuple[datetime, datetime]] = list(DEFAULT_UNTRUSTED_MTIME_WINDOWS)


def set_untrusted_mtime_windows(windows: Optional[List[Tuple[datetime, datetime]]] = None) -> None:
    """Configure active untrusted mtime window(s)."""
    global UNTRUSTED_MTIME_WINDOWS
    if windows is not None:
        UNTRUSTED_MTIME_WINDOWS = list(windows)
    else:
        UNTRUSTED_MTIME_WINDOWS = list(DEFAULT_UNTRUSTED_MTIME_WINDOWS)


@dataclass
class FileMetadata:
    ext: str
    kind: str
    size_bytes: int
    sha256: Optional[str]
    width: Optional[int]
    height: Optional[int]
    camera_make: Optional[str]
    camera_model: Optional[str]
    exif_dt: Optional[str]
    json_dt: Optional[str]
    name_dt: Optional[str]
    video_dt: Optional[str]
    mtime: str
    mtime_trusted: bool
    resolved_dt: Optional[str]
    date_source: str
    date_confidence: str
    is_lowres_candidate: bool
    lowres_match: str
    has_gps: bool = False
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    issues: str = ""


def compute_sha256(file_path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Compute sha256 checksum streaming chunks."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def parse_datetime_str(s: str) -> Optional[datetime]:
    """Parse various ISO or EXIF datetime formats into a datetime object."""
    if not s:
        return None
    s = s.strip()
    # EXIF standard: YYYY:MM:DD HH:MM:SS
    m = re.match(r"^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})", s)
    if m:
        try:
            return datetime(
                int(m.group(1)), int(m.group(2)), int(m.group(3)),
                int(m.group(4)), int(m.group(5)), int(m.group(6)),
            )
        except ValueError:
            return None

    # ISO standard: YYYY-MM-DDTHH:MM:SS
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})", s)
    if m:
        try:
            return datetime(
                int(m.group(1)), int(m.group(2)), int(m.group(3)),
                int(m.group(4)), int(m.group(5)), int(m.group(6)),
            )
        except ValueError:
            return None

    # Compact: YYYYMMDD_HHMMSS
    m = re.match(r"^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", s)
    if m:
        try:
            return datetime(
                int(m.group(1)), int(m.group(2)), int(m.group(3)),
                int(m.group(4)), int(m.group(5)), int(m.group(6)),
            )
        except ValueError:
            return None

    return None


def format_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def reset_untrusted_mtime_windows() -> None:
    """Reset active untrusted windows back to defaults."""
    global UNTRUSTED_MTIME_WINDOWS
    UNTRUSTED_MTIME_WINDOWS = list(DEFAULT_UNTRUSTED_MTIME_WINDOWS)


def is_mtime_trusted(dt: datetime) -> bool:
    """Returns False if dt falls within any active untrusted mtime window."""
    for start, end in UNTRUSTED_MTIME_WINDOWS:
        if start <= dt <= end:
            return False
    return True


def extract_filename_date(name: str) -> Optional[datetime]:
    """Extract embedded timestamp from filename patterns."""
    stem = Path(name).stem

    # 1. Security Camera format: [12-hex-ID]_YYYYMMDDHHMMSS[mmm]_camX
    m = re.search(r"[0-9a-fA-F]{12}_(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", stem)
    if m:
        try:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            hour, minute, second = int(m.group(4)), int(m.group(5)), int(m.group(6))
            if 1990 <= year <= 2040 and 1 <= month <= 12 and 1 <= day <= 31 and hour < 24 and minute < 60 and second < 60:
                return datetime(year, month, day, hour, minute, second)
        except ValueError:
            pass

    # 2. Hyphen/underscore-separated full timestamp:
    # Handles: [PREFIX[-_]]YYYY[-_]MM[-_]DD[-_ ]HH[-_:]MM[-_:]SS[optional mmm]
    # e.g. VIDEO-2026-02-14-11-37-45, 2020-12-11-14-55-20-003, fraksl_2021-11-13_23-09-56_video
    m = re.search(r"(\d{4})[-_](\d{2})[-_](\d{2})[-_ ](\d{2})[-_:](\d{2})[-_:](\d{2})(?:[-_.]\d{3})?", stem)
    if m:
        try:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            hour, minute, second = int(m.group(4)), int(m.group(5)), int(m.group(6))
            if 1990 <= year <= 2040 and 1 <= month <= 12 and 1 <= day <= 31 and hour < 24 and minute < 60 and second < 60:
                return datetime(year, month, day, hour, minute, second)
        except ValueError:
            pass

    # 2b. Mixed separated date and compact time:
    # Handles: [PREFIX[-_]]YYYY[-_]MM[-_]DD[-_]HHMMSS[optional mmm]
    # e.g. Tezza_2021_04_02_160615101.jpg
    m = re.search(r"(\d{4})[-_](\d{2})[-_](\d{2})[-_](\d{2})(\d{2})(\d{2})(?:\d{3})?", stem)
    if m:
        try:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            hour, minute, second = int(m.group(4)), int(m.group(5)), int(m.group(6))
            if 1990 <= year <= 2040 and 1 <= month <= 12 and 1 <= day <= 31 and hour < 24 and minute < 60 and second < 60:
                return datetime(year, month, day, hour, minute, second)
        except ValueError:
            pass

    # 2c. Instasize 2-digit year pattern: instasize_YYMMDDHHMMSS
    m = re.search(r"instasize_(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", stem, re.I)
    if m:
        try:
            y = 2000 + int(m.group(1))
            mo, d = int(m.group(2)), int(m.group(3))
            h, mi, s = int(m.group(4)), int(m.group(5)), int(m.group(6))
            if 2000 <= y <= 2040 and 1 <= mo <= 12 and 1 <= d <= 31 and h < 24 and mi < 60 and s < 60:
                return datetime(y, mo, d, h, mi, s)
        except ValueError:
            pass

    # 3. Compact timestamp with optional prefix and optional milliseconds:
    # Handles: [PREFIX_]YYYYMMDD[-_]HHMMSS[mmm][SUFFIX]
    # e.g. VideoCapture_20210122-160351, IMG_20200815_123456, 20210122_160351
    m = re.search(r"(?:^|[a-zA-Z0-9]{2,20}[-_])(\d{4})(\d{2})(\d{2})[-_](\d{2})(\d{2})(\d{2})(?:\d{3})?", stem)
    if m:
        try:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            hour, minute, second = int(m.group(4)), int(m.group(5)), int(m.group(6))
            if 1990 <= year <= 2040 and 1 <= month <= 12 and 1 <= day <= 31 and hour < 24 and minute < 60 and second < 60:
                return datetime(year, month, day, hour, minute, second)
        except ValueError:
            pass

    # 3. Unix timestamp in milliseconds (embedded anywhere, e.g. WA_178..., FB_IMG_158..., 17909... or IMG_xxx_exported_xxx_178...)
    m = re.search(r"(?:^|[^0-9])(1[5-9]\d{11})(?:[^0-9]|$)", stem)
    if m:
        try:
            ms = int(m.group(1))
            sec = ms / 1000.0
            dt = datetime.fromtimestamp(sec)
            if 2000 <= dt.year <= 2040:
                return dt
        except (ValueError, OSError, OverflowError):
            pass

    # 4. Unix timestamp in seconds (10 digits starting with 16-19, e.g. EMJS-1750829312)
    m = re.search(r"(?:^|[^0-9])(1[6-9]\d{8})(?:[^0-9]|$)", stem)
    if m:
        try:
            sec = int(m.group(1))
            dt = datetime.fromtimestamp(sec)
            if 2000 <= dt.year <= 2040:
                return dt
        except (ValueError, OSError, OverflowError):
            pass

    # 5. WhatsApp date-only: IMG-YYYYMMDD-WAxxxx or VID-YYYYMMDD-WAxxxx
    m = re.search(r"(?:IMG|VID)[-_](\d{4})(\d{2})(\d{2})[-_]WA", stem, re.I)
    if m:
        try:
            year = int(m.group(1))
            if 1990 <= year <= 2040:
                return datetime(year, int(m.group(2)), int(m.group(3)), 12, 0, 0)
        except ValueError:
            pass

    # 6. DD-MM-YY or DD-MM-YYYY (e.g. 27-08-24.jpeg)
    m = re.match(r"^(\d{2})[-_](\d{2})[-_](\d{2,4})$", stem)
    if m:
        try:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if y < 100:
                y += 2000 if y <= 40 else 1900
            if 1990 <= y <= 2040 and 1 <= mo <= 12 and 1 <= d <= 31:
                return datetime(y, mo, d, 12, 0, 0)
        except ValueError:
            pass

    # 7. Standard date-only: YYYY-MM-DD or YYYY.MM.DD
    m = re.search(r"(\d{4})[-._](\d{2})[-._](\d{2})", stem)
    if m:
        try:
            year = int(m.group(1))
            if 1990 <= year <= 2040:
                return datetime(year, int(m.group(2)), int(m.group(3)), 12, 0, 0)
        except ValueError:
            pass

    return None


def parse_dms(dms_str: str, ref: str = "N") -> Optional[float]:
    """Parse EXIF DMS degrees/minutes/seconds string into signed decimal float."""
    if not dms_str or not dms_str.strip():
        return None
    try:
        parts = [p.strip() for p in dms_str.split(",")]
        def eval_frac(f: str) -> float:
            if "/" in f:
                n, d = f.split("/")
                return float(n) / float(d) if float(d) != 0 else 0.0
            return float(f)
        deg = eval_frac(parts[0]) if len(parts) > 0 else 0.0
        mn = eval_frac(parts[1]) if len(parts) > 1 else 0.0
        sec = eval_frac(parts[2]) if len(parts) > 2 else 0.0
        val = deg + mn / 60.0 + sec / 3600.0
        if ref and ref.upper() in ("S", "W"):
            val = -val
        return round(val, 6)
    except Exception:
        return None


def extract_image_metadata(file_path: Path) -> Tuple[Optional[int], Optional[int], Optional[str], Optional[str], Optional[str], bool, Optional[float], Optional[float]]:
    """Extract width, height, EXIF date, camera make, model, and GPS via magick identify."""
    cmd = [
        "magick", "identify", "-ping", "-format",
        "%w %h\n%[EXIF:DateTimeOriginal]\n%[EXIF:DateTimeDigitized]\n%[EXIF:DateTime]\n%[EXIF:Make]\n%[EXIF:Model]\n%[EXIF:GPSLatitude]\n%[EXIF:GPSLatitudeRef]\n%[EXIF:GPSLongitude]\n%[EXIF:GPSLongitudeRef]",
        f"{str(file_path)}[0]",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if res.returncode != 0:
            return None, None, None, None, None, False, None, None
        lines = res.stdout.strip().split("\n")
        w, h = None, None
        if len(lines) >= 1 and " " in lines[0]:
            parts = lines[0].split()
            if parts[0].isdigit() and parts[1].isdigit():
                w, h = int(parts[0]), int(parts[1])

        exif_dt = None
        for i in (1, 2, 3):
            if i < len(lines) and lines[i].strip():
                parsed = parse_datetime_str(lines[i].strip())
                if parsed:
                    exif_dt = format_dt(parsed)
                    break

        make = lines[4].strip() if len(lines) >= 5 and lines[4].strip() else None
        model = lines[5].strip() if len(lines) >= 6 and lines[5].strip() else None

        lat_str = lines[6].strip() if len(lines) >= 7 else ""
        lat_ref = lines[7].strip() if len(lines) >= 8 else "N"
        lon_str = lines[8].strip() if len(lines) >= 9 else ""
        lon_ref = lines[9].strip() if len(lines) >= 10 else "W"
        lat = parse_dms(lat_str, lat_ref)
        lon = parse_dms(lon_str, lon_ref)
        has_gps = (lat is not None and lon is not None)

        return w, h, exif_dt, make, model, has_gps, lat, lon
    except Exception:
        return None, None, None, None, None, False, None, None


def extract_video_metadata(file_path: Path) -> Tuple[Optional[int], Optional[int], Optional[str], bool, Optional[float], Optional[float]]:
    """Extract width, height, creation_time, and location via ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", "-show_format",
        str(file_path),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if res.returncode != 0:
            return None, None, None, False, None, None
        data = json.loads(res.stdout)
        w, h, dt_str = None, None, None
        has_gps, lat, lon = False, None, None

        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                if "width" in stream and "height" in stream:
                    w, h = stream["width"], stream["height"]
                tags = stream.get("tags", {})
                if "creation_time" in tags:
                    dt_str = tags["creation_time"]
                    break

        fmt_tags = data.get("format", {}).get("tags", {})
        if not dt_str and fmt_tags:
            dt_str = fmt_tags.get("creation_time")

        loc_tag = fmt_tags.get("location") or fmt_tags.get("location-eng") or fmt_tags.get("com.apple.quicktime.location.ISO6709")
        if loc_tag:
            m = re.match(r"([+-]\d+\.?\d*)([+-]\d+\.?\d*)", loc_tag)
            if m:
                try:
                    lat = float(m.group(1))
                    lon = float(m.group(2))
                    has_gps = True
                except ValueError:
                    pass

        video_dt = None
        if dt_str:
            parsed = parse_datetime_str(dt_str)
            if parsed:
                video_dt = format_dt(parsed)
        return w, h, video_dt, has_gps, lat, lon
    except Exception:
        return None, None, None, False, None, None


def find_google_takeout_json(file_path: Path) -> Optional[str]:
    """Look for sidecar .json created by Google Takeout."""
    candidates = [
        file_path.with_name(f"{file_path.name}.json"),
        file_path.with_name(f"{file_path.stem}.json"),
    ]
    # Google Takeout sometimes truncates or appends (1) before .json
    m = re.match(r"^(.*?)\(\d+\)$", file_path.stem)
    if m:
        candidates.append(file_path.with_name(f"{m.group(1)}.json"))

    for c in candidates:
        if c.is_file():
            try:
                with open(c, "r", encoding="utf-8", errors="ignore") as f:
                    data = json.load(f)
                    ts = data.get("photoTakenTime", {}).get("timestamp")
                    if ts and str(ts).isdigit():
                        dt = datetime.fromtimestamp(int(ts))
                        return format_dt(dt)
            except Exception:
                pass
    return None


def inspect_file(file_path: Path, calculate_hash: bool = False) -> FileMetadata:
    """Fully inspect a single file, extracting dates, resolution, low-res candidacy."""
    ext = file_path.suffix.lower()
    if ext in IMAGE_EXTS:
        kind = "image"
    elif ext in VIDEO_EXTS:
        kind = "video"
    elif ext in SIDECAR_EXTS:
        kind = "sidecar"
    else:
        kind = "other"

    stat = file_path.stat()
    size_bytes = stat.st_size
    mtime_dt = datetime.fromtimestamp(stat.st_mtime)
    mtime_str = format_dt(mtime_dt)
    mtime_trusted = is_mtime_trusted(mtime_dt)

    sha256 = compute_sha256(file_path) if calculate_hash else None

    width, height = None, None
    camera_make, camera_model = None, None
    exif_dt = None
    video_dt = None
    json_dt = None
    has_gps = False
    latitude = None
    longitude = None
    issues = []

    if kind == "image":
        width, height, exif_dt, camera_make, camera_model, has_gps, latitude, longitude = extract_image_metadata(file_path)
    elif kind == "video":
        width, height, video_dt, has_gps, latitude, longitude = extract_video_metadata(file_path)

    json_dt = find_google_takeout_json(file_path)

    name_dt_obj = extract_filename_date(file_path.name)
    name_dt = format_dt(name_dt_obj) if name_dt_obj else None

    # Date resolution hierarchy
    resolved_dt = None
    date_source = "none"
    date_confidence = "none"

    if exif_dt:
        resolved_dt = exif_dt
        date_source = "exif"
        date_confidence = "high"
    elif video_dt and kind == "video":
        resolved_dt = video_dt
        date_source = "video"
        date_confidence = "high"
    elif json_dt:
        resolved_dt = json_dt
        date_source = "json"
        date_confidence = "high"
    elif name_dt:
        resolved_dt = name_dt
        date_source = "name"
        date_confidence = "medium"
    elif mtime_trusted:
        resolved_dt = mtime_str
        date_source = "mtime"
        date_confidence = "low"
    else:
        resolved_dt = None
        date_source = "none"
        date_confidence = "none"
        issues.append("untrusted-mtime-no-other-date")

    # Low-res candidacy
    # Rule: size < 100 KB OR shortest side < 800 px
    size_match = size_bytes < 100_000
    res_match = False
    if width is not None and height is not None and width > 0 and height > 0:
        res_match = min(width, height) < 800

    if size_match and res_match:
        lowres_match = "both"
        is_lowres = True
    elif size_match:
        lowres_match = "size"
        is_lowres = True
    elif res_match:
        lowres_match = "res"
        is_lowres = True
    else:
        lowres_match = "none"
        is_lowres = False

    return FileMetadata(
        ext=ext,
        kind=kind,
        size_bytes=size_bytes,
        sha256=sha256,
        width=width,
        height=height,
        camera_make=camera_make,
        camera_model=camera_model,
        exif_dt=exif_dt,
        json_dt=json_dt,
        name_dt=name_dt,
        video_dt=video_dt,
        mtime=mtime_str,
        mtime_trusted=mtime_trusted,
        resolved_dt=resolved_dt,
        date_source=date_source,
        date_confidence=date_confidence,
        is_lowres_candidate=is_lowres,
        lowres_match=lowres_match,
        has_gps=has_gps,
        latitude=latitude,
        longitude=longitude,
        issues=";".join(issues),
    )
