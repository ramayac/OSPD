"""Unit tests for metadata extraction and date rules."""

from datetime import datetime
from pathlib import Path
import pytest

from ospd.metadata import (
    extract_filename_date,
    format_dt,
    is_mtime_trusted,
    parse_datetime_str,
    parse_dms,
)


def test_parse_datetime_str():
    assert parse_datetime_str("2024:08:25 12:28:30") == datetime(2024, 8, 25, 12, 28, 30)
    assert parse_datetime_str("2026-09-28T22:05:26") == datetime(2026, 9, 28, 22, 5, 26)
    assert parse_datetime_str("20250419_090530") == datetime(2025, 4, 19, 9, 5, 30)
    assert parse_datetime_str("invalid") is None


def test_filename_date_extraction():
    # Samsung pattern
    dt = extract_filename_date("20260926_164029.heic")
    assert dt == datetime(2026, 9, 26, 16, 40, 29)

    # Screenshot pattern
    dt = extract_filename_date("Screenshot_20261010_215825_UGREEN NAS.jpg")
    assert dt == datetime(2026, 10, 10, 21, 58, 25)

    # Millisecond timestamp pattern (1790912862896_1.jpg)
    dt = extract_filename_date("1790912862896_1.jpg")
    assert dt is not None
    assert dt.year >= 2026

    # Standard date pattern
    dt = extract_filename_date("photo-2018-06-15.jpg")
    assert dt == datetime(2018, 6, 15, 12, 0, 0)

    # WhatsApp pattern
    dt = extract_filename_date("IMG-20231215-WA0004.jpg")
    assert dt == datetime(2023, 12, 15, 12, 0, 0)

    # Google Pixel pattern
    dt = extract_filename_date("PXL_20220804_153022.jpg")
    assert dt == datetime(2022, 8, 4, 15, 30, 22)

    # Hyphen-separated VIDEO pattern
    dt = extract_filename_date("VIDEO-2026-02-14-11-37-45.mp4")
    assert dt == datetime(2026, 2, 14, 11, 37, 45)

    # VideoCapture pattern
    dt = extract_filename_date("VideoCapture_20210122-160351.jpg")
    assert dt == datetime(2021, 1, 22, 16, 3, 51)

    # Hyphen-separated with milliseconds
    dt = extract_filename_date("2020-12-11-14-55-20-003.jpg")
    assert dt == datetime(2020, 12, 11, 14, 55, 20)

    # Tool prefix with hyphen-separated timestamp
    dt = extract_filename_date("fraksl_2021-11-13_23-09-56_video.mp4")
    assert dt == datetime(2021, 11, 13, 23, 9, 56)

    # Tezza photo editing app format (Tezza_YYYY_MM_DD_HHMMSSmmm)
    dt = extract_filename_date("Tezza_2021_04_02_160615101.jpg")
    assert dt == datetime(2021, 4, 2, 16, 6, 15)

    # Instasize app format (instasize_YYMMDDHHMMSS)
    dt = extract_filename_date("instasize_180619121212.png")
    assert dt == datetime(2018, 6, 19, 12, 12, 12)


def test_untrusted_mtime_window():
    # Oct 30, Oct 31, Nov 01 of 2026 must be UNTRUSTED
    bad1 = datetime(2026, 10, 30, 9, 0, 0)
    bad2 = datetime(2026, 10, 31, 23, 59, 59)
    bad3 = datetime(2026, 11, 1, 14, 20, 0)
    assert is_mtime_trusted(bad1) is False
    assert is_mtime_trusted(bad2) is False
    assert is_mtime_trusted(bad3) is False

    # Dates outside the window are TRUSTED
    good1 = datetime(2026, 10, 29, 23, 59, 59)
    good2 = datetime(2026, 11, 2, 0, 0, 0)
    good3 = datetime(2024, 8, 25, 12, 0, 0)
    assert is_mtime_trusted(good1) is True
    assert is_mtime_trusted(good2) is True
    assert is_mtime_trusted(good3) is True


def test_parse_dms_and_gps_metadata():
    # Valid North latitude
    lat = parse_dms("49/1,22/1,38174160/1000000", "N")
    assert lat is not None
    assert round(lat, 4) == 49.3773

    # Valid West longitude (should be negative)
    lon = parse_dms("123/1,16/1,19883280/1000000", "W")
    assert lon is not None
    assert round(lon, 4) == -123.2722

    # South latitude (should be negative)
    lat_s = parse_dms("33/1,51/1,29/1", "S")
    assert lat_s is not None
    assert lat_s < 0

    # East longitude (should be positive)
    lon_e = parse_dms("151/1,12/1,40/1", "E")
    assert lon_e is not None
    assert lon_e > 0

    # Edge cases
    assert parse_dms("", "N") is None
    assert parse_dms(None, "N") is None
    assert parse_dms("invalid,junk,data", "N") is None
