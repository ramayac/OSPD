# OSPD (Organize and Sorting Photo Dump) — Agent & Developer Guide

OSPD organizes tens of thousands of disorganized photos, videos, and cloud album dumps (Google Photos, OneDrive, iCloud, Apple Live Photos, Samsung Gallery, Kodak/Lumix camera dumps) into a clean, unified, chronologically structured gallery.

It was designed to be **safe, deterministic, fully auditable, and easily adaptable to any developer's local environment or directory layout**.

---

## 1. Quickstart & Configuration (`config.json.example`)

OSPD is fully decoupled from any specific hardcoded filepaths. A new developer can set up and run OSPD in seconds.

### Step 1: Create `config.json`
Copy the included template:
```bash
cp config.json.example config.json
```
Customize the configuration:
```json
{
  "pictures_root": "/home/username/Pictures",
  "data_dir": "./data-run",
  "sync_client": "none",
  "camera_dump_patterns": [
    "^camera(\\s+roll)?$",
    "^dcim(/.*)?$",
    "^samsung(\\s+gallery)?(/.*)?$",
    "^google(\\s+photos)?(/.*)?$"
  ],
  "custom_albums": {
    "^tezza_": "Albums/Tezza",
    "^vsco_": "Albums/VSCO"
  }
}
```

### Configuration Resolution Precedence:
1. **CLI flags:** `--root /path/to/photos`, `--data-dir /path/to/data`, `--config custom.json`
2. **Environment variables:** `PICTURES_ROOT`, `OSPD_ROOT`, `OSPD_DATA_DIR`
3. **Config file:** `config.json` in current directory or repo root
4. **Sensible defaults:** Automatically assumes camera rolls, wallpapers, and albums based on standard naming conventions.

---

## 2. Directory Structure & `data-run/`

OSPD keeps code cleanly separated from execution state. All runtime databases, audit manifests, HTML contact sheets, and logs live inside `data-run/` (which is excluded from Git):

```
OSPD/                                     # Git Repository
├── ospd/                                 # Core Python Package
│   ├── config.py                         # Configuration engine (CLI, ENV, JSON)
│   ├── metadata.py                       # EXIF, QuickTime/video, filename date extraction
│   ├── classification.py                 # Extensible folder & file classifier
│   ├── duplicates.py                     # SHA-256 & heuristic EXIF near-duplicate engine
│   ├── planner.py                        # Collision-free move planner & Live Photo pairing
│   ├── simulator.py                      # Shadow dry-run validator (in-memory + mock sandbox)
│   ├── executor.py                       # Atomic filesystem move engine & empty folder pruner
│   ├── review.py                         # HTML contact sheet & CSV export engine
│   ├── reporter.py                       # Markdown & HTML dashboard generator
│   └── db.py                             # SQLite audit database layer
├── tests/                                # 27 unit tests (100% passing)
├── config.json.example                   # Configuration template
├── Makefile                              # Make console
├── AGENTS.md                             # AI Agent guide
└── data-run/                             # Runtime data (ignored in Git)
    ├── photo_sort.db                     # SQLite audit database
    ├── review/                           # Generated reports and HTML contact sheets
    │   ├── report.html                   # Interactive visual summary report
    │   ├── report.md                     # Markdown summary
    │   ├── duplicates.html               # Side-by-side duplicate comparison sheet
    │   └── *.csv                         # Discrepancy, duplicate, and folder CSVs
    ├── runs/                             # Streaming CSV logs (<run_id>-<date>.csv)
    └── snapshots/                        # Auto SQLite snapshot backups
```

### Target Organized Library Output:
```
Pictures/
├── Gallery/YYYY/YYYY-MM/YYYYMMDD_HHMMSS.ext  # Chronological unified camera timeline
├── Albums/<Album Name>/                      # Preserved whole albums & sets (e.g. Albums/Tezza/)
├── Wallpapers/<Pack Name>/                   # Wallpapers & release assets
├── Art/<Project Name>/                       # Sketches, sprites, creative dirs
├── Screenshots/YYYY/                         # Screenshots detected by name
├── Unsorted/
│   ├── LowRes/                               # Shortest side < 800px / < 100KB / Tilesets
│   ├── Animated/                             # .gif
│   ├── WeirdNames/                           # Hashes, UUIDs, .webp
│   ├── NoDate/                               # Missing reliable date
│   └── Other/                                # Non-media
└── _Duplicates/                              # Quarantined redundant copies (only after user approval)
```

---

## 3. Workflow & Makefile Commands

All commands can be run via `make` or directly via `python3 -m ospd.cli`:

```bash
# 1. Run unit test suite
make test

# 2. Check system status & file counts
make status [ROOT=/path/to/photos]

# 3. Scan & Index (use HASH=1 to compute SHA-256 for duplicate matching)
make scan HASH=1 [ROOT=/path/to/photos]

# 4. Review Duplicates & Dates
make review-export       # Exports CSVs to data-run/review/
make review-html         # Builds HTML contact sheet with side-by-side images
make check-dates         # Scans for date discrepancies where filename has >=90% confidence
make fix-dates CONFIRM=--run # Restores file mtime from filename on disk

# 5. Approve Duplicates (Safe Precedence: higher res/album copy wins)
make review-approve      # Approves candidate duplicate groups to move secondary copies to _Duplicates/

# 6. Generate Move Plan & Simulate
make plan                # Generates deterministic, collision-free moves
make dry-run             # Full shadow simulation (tests rollback & validates 0 collisions)

# 7. Execute (Pilot or Full)
make batch-run CONFIRM=--run # Executes a 200-file pilot batch
make apply CONFIRM=--run     # Executes ALL planned moves

# 8. Reversibility (1-command rollback)
make undo RUN=run024     # Reverses all moves from that specific run
```

---

## 4. Key Architectural Learnings & Takeaways

### 1. The Cloud Re-Encoding Trap (Why Pure Cryptographic Hashing Fails)
- **Problem:** When analyzing 12,951 duplicate photos across Root and Samsung Gallery, only 1,320 shared an exact SHA-256 hash.
- **Root Cause:** Cloud sync engines (OneDrive / Google Photos) modify JPEG DHT (Define Huffman Table) markers and reorder EXIF metadata tags, causing a 3–26 byte difference while pixel data remains identical.
- **Solution:** Multi-tier heuristic matching. Pairing on identical camera make/model + exact sub-second EXIF capture timestamp (`same_exif_time_camera`) safely resolved 11,011 near-duplicates that SHA-256 missed, without false positives.

### 2. The Mass Sync Timestamp Corruption Window
- **Problem:** Mass file downloads/extractions overwrite filesystem `mtime` with the transfer time (e.g. Oct 30 – Nov 1, 2026 window), corrupting chronological timelines for 38,420 files.
- **Solution:** Strict untrusted mtime policy. Hierarchical date resolution (EXIF DateTimeOriginal -> QuickTime/MP4 creation tags -> Filename regex with confidence >= 90% -> Takeout JSON -> Untrusted fallback) allowed restoring true filesystem mtimes via `os.utime()` with 0 errors.

### 3. Virtual Albums vs. Physical Folders in Cloud Exports
- **Problem:** 76 album folders arrived from Google Photos Takeout with 0 files (e.g. `Ancestros (Solo Bisabuelos y Tatarabuelos)`).
- **Root Cause:** Google Photos stores albums as virtual tags in a database; exports create directory placeholders while placing actual files into the main camera roll or root.
- **Solution:** Knowing files were safe in the unified `Gallery/` timeline allowed automatic pruning of empty directory stubs without user concern.

### 4. Apple Live Photo Integrity
- **Problem:** iPhone Live Photos consist of a paired `.heic` still image and a short `.mov` clip.
- **Solution:** Linking them via a shared `live_pair_id` guaranteed matching generated stems in `Gallery/`, preserving native playback in Apple Photos and compatible viewers.

### 5. Local Inode Speed vs. Cloud API Rate Limits
- **Measurement:** 49,438 operations completed in 9.2 seconds locally via atomic Linux filesystem renames.
- **Cloud Reality:** Propagating moves and deletions to OneDrive took ~5 hours because Microsoft Graph strictly throttles write requests to ~2–3 operations/second.
- **Takeaway:** Insync's internal SQLite database (`od-*.db`) proved that 0 re-uploads were needed; the system was simply processing cloud node re-parenting and deletions at the provider's speed limit.

### 6. Non-Destructive Workflow Psychology
- **Impact:** Quarantining duplicates into `_Duplicates/` rather than deleting them gave full confidence to execute the full run, verify the library, and transfer duplicates to external cold storage cleanly.
