"""Unit tests for classification rules."""

from ospd.classification import (
    classify_camera_dump_file,
    classify_folder_name,
    is_screenshot_filename,
    is_weird_filename,
    normalize_top_folder,
)


def test_normalize_top_folder():
    assert normalize_top_folder("photo.jpg") == "."
    assert normalize_top_folder("sub/photo.jpg") == "sub"
    assert normalize_top_folder("deep/nested/path/photo.jpg") == "deep"
    assert normalize_top_folder(".") == "."


def test_classify_folder_name():
    # Wallpapers
    assert classify_folder_name("Wallpapers") == ("Wallpapers", "Wallpapers")
    assert classify_folder_name("Wallpapers 1") == ("Wallpapers", "Wallpapers")
    assert classify_folder_name("Ubuntu Release Assets") == ("Wallpapers", "Wallpapers")
    assert classify_folder_name("unsplash") == ("Wallpapers", "Wallpapers")

    # Art
    assert classify_folder_name("Processing Sketches") == ("Art", "Art")
    assert classify_folder_name("PowerLamb") == ("Art", "Art")
    assert classify_folder_name("Poorly drawn shit") == ("Art", "Art")

    # LowRes
    assert classify_folder_name("Tilesets") == ("LowRes", "Unsorted/LowRes")

    # Camera dumps (per-file)
    assert classify_folder_name(".") == ("CameraDump", "Gallery")
    assert classify_folder_name("Camera Roll") == ("CameraDump", "Gallery")
    assert classify_folder_name("Galería de Samsung") == ("CameraDump", "Gallery")

    # Albums (all other event folders)
    assert classify_folder_name("Boda Civil - Fotos") == ("Album", "Albums")
    assert classify_folder_name("Madrid - 2018") == ("Album", "Albums")
    assert classify_folder_name("Cumple Japi 2023") == ("Album", "Albums")


def test_is_weird_filename():
    assert is_weird_filename("9ca0a508-4e41-4d45-ac08-7ba8102f5301.png") is True
    assert is_weird_filename("490276942_10161330189311485_8518749171500419028_n.jpg") is True
    assert is_weird_filename("1hrznsli520f1.jpeg") is True
    assert is_weird_filename("IMG_4512.HEIC") is False
    assert is_weird_filename("20260926_164029.heic") is False


def test_classify_camera_dump_file():
    # Screenshot
    cat, sub = classify_camera_dump_file(
        "Screenshot_20261010_215825.jpg", ".jpg", "image", "2026-10-10 21:58:25", False, "pending", "none"
    )
    assert cat == "Screenshots"
    assert sub == "Screenshots/2026"

    # Animated gif
    cat, sub = classify_camera_dump_file(
        "animation.gif", ".gif", "image", "2024-01-01 12:00:00", False, "pending", "none"
    )
    assert cat == "Unsorted/Animated"

    # LowRes (both rules matched)
    cat, sub = classify_camera_dump_file(
        "icon.png", ".png", "image", "2024-01-01 12:00:00", True, "pending", "both"
    )
    assert cat == "Unsorted/LowRes"

    # Normal photo with date -> Gallery
    cat, sub = classify_camera_dump_file(
        "IMG_4512.HEIC", ".heic", "image", "2024-08-25 12:28:30", False, "pending", "none"
    )
    assert cat == "Gallery"
    assert sub == "Gallery/2024/2024-08"

    # No date -> Unsorted/NoDate
    cat, sub = classify_camera_dump_file(
        "IMG_9999.HEIC", ".heic", "image", None, False, "pending", "none"
    )
    assert cat == "Unsorted/NoDate"

    # Tezza photo editing app -> Dedicated Album
    cat, sub = classify_camera_dump_file(
        "Tezza_2021_04_02_160615101.jpg", ".jpg", "image", "2021-04-02 16:06:15", False, "pending", "none"
    )
    assert cat == "Album"
    assert sub == "Albums/Tezza"

    # SmartSelect and AISelect -> Screenshots
    cat, sub = classify_camera_dump_file(
        "SmartSelect_20250507_113712_BancaMovil.jpg", ".jpg", "image", "2025-05-07 11:37:12", False, "pending", "none"
    )
    assert cat == "Screenshots"
    assert sub == "Screenshots/2025"

    cat, sub = classify_camera_dump_file(
        "AISelect_20260612_201333_Chrome.jpg", ".jpg", "image", "2026-06-12 20:13:33", False, "pending", "none"
    )
    assert cat == "Screenshots"
    assert sub == "Screenshots/2026"
