# OSPD — Organize and Sorting Photo Dump

**OSPD** is an open-source, highly deterministic photo organization and duplicate quarantine engine designed to untangle chaotic dumps of photos and videos (from Google Photos Takeout, Microsoft OneDrive, Samsung Gallery, Apple iPhones, and legacy camera SD cards) into a clean, chronologically organized gallery.

---

## 🌟 Key Capabilities

* **Unified Chronological Timeline:** Moves loose camera dumps into `Gallery/YYYY/YYYY-MM/YYYYMMDD_HHMMSS.ext`.
* **Zero Data Loss (Moves Only):** Never deletes files. Suspected duplicates are quarantined into `_Duplicates/` only after explicit user review and approval.
* **Near-Duplicate Detection:** Solves the cloud re-encoding problem (where cloud providers alter 3–26 bytes in JPEG headers, breaking SHA-256) by pairing files via camera model + sub-second EXIF capture timestamps.
* **Filesystem Date Restoration (`fix-dates`):** Detects when mass cloud sync overwrites filesystem timestamps (`mtime`), restoring accurate capture dates from high-confidence embedded filename timestamps.
* **Apple Live Photo Pairing:** Preserves `.heic` and `.mov` pairs with matching generated filenames so Live Photo playback remains intact.
* **Folder Context Preservation:** Keeps curated albums (`Albums/`), wallpaper packs (`Wallpapers/`), and art folders (`Art/`) intact as whole folders.
* **Full Simulation & Rollback:** Includes in-memory graph validation, `/tmp` mock execution, and instant 1-command rollback (`make undo RUN=runXXX`).
* **Interactive HTML Reports:** Generates side-by-side duplicate contact sheets and comprehensive visual dashboards.

---

## 🚀 Quickstart

### 1. Prerequisites
* Python 3.9+
* Optional: `imagemagick` (for fast EXIF parsing) and `ffmpeg` / `ffprobe` (for video metadata tags).
  ```bash
  # Debian/Ubuntu
  sudo apt-get install imagemagick ffmpeg
  ```

#### Reproducible dev shell (`devenv` / Nix) — optional
`devenv.nix` pins Python 3.13, `pytest` (via `requirements-dev.txt`), ImageMagick, ffmpeg, and procps:
```bash
devenv shell        # enter the shell (Python venv is created automatically)
make test           # 27 tests, fully inside the pinned environment
devenv test         # same suite through devenv's own test hook
```
With direnv installed, `direnv allow` activates it automatically on `cd`.

### 2. Setup Configuration
Copy the configuration template:
```bash
cp config.json.example config.json
```
Edit `config.json` with your library path:
```json
{
  "pictures_root": "/path/to/your/disorganized/photos",
  "data_dir": "./data-run",
  "sync_client": "none"
}
```

Alternatively, you can pass `--root /path/to/photos` directly on the command line or set `export PICTURES_ROOT=/path/to/photos`.

### 3. Run the Test Suite
Ensure the engine is verified:
```bash
make test
```

---

## 📋 The Recommended Workflow

```
1. SCAN          make scan HASH=1       (Indexes files, extracts EXIF/GPS, hashes for dups)
2. REVIEW        make review-html       (Generates side-by-side duplicate comparison sheet)
3. FIX DATES     make fix-dates CONFIRM=--run  (Repairs corrupted sync timestamps on disk)
4. APPROVE DUPS  make review-approve    (Schedules secondary duplicate copies for quarantine)
5. PLAN & TEST   make plan && make dry-run   (Simulates 100% collision-free moves)
6. EXECUTE       make apply CONFIRM=--run    (Performs moves; auto-prunes empty source folders)
7. ROLLBACK      make undo RUN=run001   (Instant 1-command reversibility if ever needed)
```

---

## ⚙️ Configuration Reference (`config.json`)

| Field | Type | Description |
| :--- | :--- | :--- |
| `pictures_root` | `string` | Absolute or relative path to the photos directory to organize. |
| `data_dir` | `string` | Location for SQLite database (`photo_sort.db`), review sheets, and logs. Default: `./data-run`. |
| `sync_client` | `string` | Cloud sync daemon to check before applying moves (`"none"`, `"insync"`, `"onedrive"`, `"dropbox"`, or `"auto"`). |
| `untrusted_mtime_windows` | `array` | Optional list of date ranges `[{"start": "...", "end": "..."}]` where filesystem `mtime` was corrupted and should not be trusted. |
| `camera_dump_patterns` | `array` | Regex patterns for folders to treat as loose camera dumps (e.g. `^camera roll$`, `^dcim.*`, `^samsung gallery.*`). |
| `wallpaper_patterns` | `array` | Regex patterns for wallpaper folders to preserve intact under `Wallpapers/<Name>/`. |
| `art_patterns` | `array` | Regex patterns for art folders to preserve intact under `Art/<Name>/`. |
| `custom_albums` | `object` | Mapping of filename regex to custom album destination (e.g. `{"^tezza_": "Albums/Tezza"}`). |
| `ignore_dirs` | `array` | Folder names to ignore during scanning. |

---

## 📂 Output Folder Structure

```
Pictures/
├── Gallery/YYYY/YYYY-MM/YYYYMMDD_HHMMSS.ext  # Chronological unified timeline
├── Albums/<Album Name>/                      # Preserved albums + custom app albums
├── Wallpapers/<Pack Name>/                   # Wallpapers & release assets
├── Art/<Project Name>/                       # Sketches, sprites, creative dirs
├── Screenshots/YYYY/                         # Screenshots detected by name
├── Unsorted/
│   ├── LowRes/                               # Shortest side < 800px / < 100KB / Tilesets
│   ├── Animated/                             # .gif files
│   ├── WeirdNames/                           # Hashes, UUIDs, .webp
│   ├── NoDate/                               # Missing reliable date
│   └── Other/                                # Non-media files
└── _Duplicates/                              # Quarantined redundant copies (safe to delete/archive)
```

---

## 🛠️ Makefile Cheatsheet

| Target | Description | Example |
| :--- | :--- | :--- |
| `make status` | Show database and folder status | `make status` |
| `make scan` | Scan and index library | `make scan HASH=1` |
| `make review-export` | Export CSVs to `data-run/review/` | `make review-export` |
| `make review-html` | Generate HTML contact sheet | `make review-html` |
| `make review-approve`| Approve candidate duplicate groups | `make review-approve` |
| `make check-dates` | Inspect date discrepancies | `make check-dates` |
| `make fix-dates` | Restore filesystem mtime | `make fix-dates CONFIRM=--run` |
| `make plan` | Generate execution plan | `make plan` |
| `make dry-run` | Simulate moves & test rollback | `make dry-run` |
| `make batch-run` | Run pilot batch (default 200 items) | `make batch-run CONFIRM=--run` |
| `make apply` | Execute ALL planned operations | `make apply CONFIRM=--run` |
| `make undo` | Reversibly undo an executed run | `make undo RUN=run024` |
| `make report` | Refresh HTML and Markdown reports | `make report` |

---

## 📖 Developer & Agent Guide
For architectural details, database schemas, and AI agent instructions, see [AGENTS.md](AGENTS.md).
