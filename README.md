# KFX Highlights
Uses Synced Kindle Annotations to Make Highlights File

Most of the documents I read on my Kindle are sent via "Send to Kindle" so that I can read them on other devices. However, one of the issues I've noticed is that there's no way to extract synced highlights. This tool extracts them by reading the book files and annotation files directly.

Supports both **KFX** (`.kfx` + `.yjr`) and **AZW3** (`.azw3` + `.azw3r`/`.azw3f`) formats.

You can either connect your Kindle via USB and process books directly from the device, or manually copy the files to the `input/` directory.

jhowell released a [KRDS Parser](https://www.mobileread.com/forums/showthread.php?t=322172). It's located [here](https://github.com/K-R-D-S/KRDS).


## Setup

Create a virtual environment and install the required dependencies:

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Activate the virtual environment before each use:

```
source .venv/bin/activate
```

**AZW3 support** additionally requires [Calibre](https://calibre-ebook.com/) to be installed. The tool uses `calibre-debug -e` to run the AZW3 decompression under Calibre's Python environment. It checks your PATH first, then looks in common installation locations (e.g. `/Applications/calibre.app/Contents/MacOS/` on macOS).

## Usage

### Kindle device mode (recommended)

Connect your Kindle via USB, then process directly from the device:

```
python extract_highlights.py --kindle /Volumes/Kindle
```

This scans the Kindle's `documents/` and `documents/Downloads/` directories, finds all book/annotation pairs (both KFX and AZW3), and extracts highlights to `output/`. It uses incremental sync — unchanged books that were previously processed are skipped automatically.

You can set a persistent default in `config.yaml` so you don't need to pass the path every time:

```yaml
kindle_path: /Volumes/Kindle
```

#### Import modes

Instead of processing in-place, you can copy files from the Kindle to `input/`:

```bash
# Copy book + annotation files to input/ without extracting
python extract_highlights.py --kindle /Volumes/Kindle --import-only

# Copy book + annotation files to input/ AND run extraction
python extract_highlights.py --kindle /Volumes/Kindle --import-book

# Copy only annotation files to input/pending/ (for DRM-protected books)
python extract_highlights.py --kindle /Volumes/Kindle --import-metadata
```

#### DRM-protected books

Books with DRM will fail extraction and are flagged separately in the output. Use `--import-metadata` to copy just the annotation files, then use `--calibre-library` to automatically match them with unlocked files in your Calibre library (see below). Alternatively, manually place an unlocked book file in `input/` and run in bulk mode.

#### Testing and preview

```bash
# Preview what would be done without touching any files
python extract_highlights.py --kindle /Volumes/Kindle --dry-run

# Process only the first N books
python extract_highlights.py --kindle /Volumes/Kindle --limit 5
```

### Calibre library mode

If you have a Calibre library with unlocked copies of your DRM-protected Kindle books, this mode automatically matches them by ASIN and extracts highlights using the Calibre files directly (no file copying needed). Supports KFX, KFX-ZIP, and AZW3 formats in Calibre (prefers KFX > KFX-ZIP > AZW3).

First, import annotations from your Kindle:

```bash
python extract_highlights.py --kindle /Volumes/Kindle --import-metadata
```

Then match and extract:

```bash
# Preview the matching report
python extract_highlights.py --calibre-library "/path/to/Calibre Library" --dry-run

# Process all ASIN-matched books
python extract_highlights.py --calibre-library "/path/to/Calibre Library"

# Include fuzzy title matches (shown in report but skipped by default)
python extract_highlights.py --calibre-library "/path/to/Calibre Library" --accept-fuzzy

# Match ALL synced books, not just DRM-flagged ones
python extract_highlights.py --calibre-library "/path/to/Calibre Library" --all-books

# Re-process all previously successful books (regenerate output files)
python extract_highlights.py --calibre-library "/path/to/Calibre Library" --reprocess

# Re-process only books whose output file is missing
python extract_highlights.py --calibre-library "/path/to/Calibre Library" --reprocess-missing

# Update Calibre book paths in sync state (after library reorganization)
python extract_highlights.py --calibre-library "/path/to/Calibre Library" --rematch

# Rematch only books whose Calibre files are missing
python extract_highlights.py --calibre-library "/path/to/Calibre Library" --rematch --missing-only

# Preview what paths would be updated without changing sync state
python extract_highlights.py --calibre-library "/path/to/Calibre Library" --rematch --dry-run
```

By default, only DRM-flagged books are matched. Use `--all-books` to also include books that were already successfully processed from the Kindle — useful if you want to re-extract using the Calibre version instead.

#### Recovering missing output files

If the sync state shows books as successfully processed but their output files are missing (e.g. after moving the `output/` directory), use `--reprocess-missing` to regenerate only those files without reprocessing everything:

```bash
# First, make sure annotation files are imported for all books on the Kindle
python extract_highlights.py --kindle --import-metadata --reprocess

# Then regenerate only the missing output files
python extract_highlights.py --calibre-library --reprocess-missing
```

The `--reprocess` flag on `--import-metadata` is needed to include books already marked successful in the sync state — without it, those books would be skipped as unchanged.

If you've reorganized your Calibre library (moved or renamed books), use `--rematch` to update the stored Calibre paths in `.sync_state.json` without reprocessing the books. Use `--missing-only` with `--rematch` to only update books whose stored Calibre file path no longer exists (marked with `"file_missing": true` in the sync state). Use `--dry-run` with `--rematch` to preview which paths would be updated before making changes. To prevent specific books from being rematched (e.g., if you've manually set a custom path), add `"rematch_disabled": true` to the book's record in `.sync_state.json`.

The matching report shows four categories: ASIN-matched with a supported format (processable), matched but no supported format in Calibre, matched but no annotation file imported yet, and unmatched. You can set a persistent default in `config.yaml`:

```yaml
calibre_library: /path/to/Calibre Library
```

### Bulk mode (all books at once)

Place book and annotation files in the `input/` directory, then run:

```
python extract_highlights.py
```

This scans `input/` and automatically pairs book files with their corresponding annotation files by matching filenames. Supports both KFX (`.kfx` + `.yjr`) and AZW3 (`.azw3` + `.azw3r`/`.azw3f`) pairs. Each pair is processed sequentially, and failures are reported at the end without aborting the whole run.

`--dry-run` and `--limit N` work in bulk mode too.

### Single book

```bash
# KFX book
python extract_highlights.py <book.kfx> <annotations.yjr>

# AZW3 book (requires Calibre)
python extract_highlights.py <book.azw3> <annotations.azw3r>
```

All modes will:
1. Convert the annotation file to JSON using `krds.py`.
2. For KFX: call `extract_highlights_kfxlib.py` with the generated JSON and book file.
3. For AZW3: call `extract_highlights_azw3.py` via `calibre-debug -e` to decompress and extract highlights.

Output goes to `output/`.

### Options

| Flag | Description |
|------|-------------|
| `-f {html,md,json,csv}` | Output format (default: html) |
| `-q` / `--quiet` | Suppress per-highlight output, show summary only |
| `--skip-existing` | Skip books whose output file already exists (bulk mode) |
| `--title "My Title"` | Override the book title in output (single-pair mode) |
| `--keep-json` | Keep intermediate JSON files |
| `-j N` / `--jobs N` | Parallel workers (0 = CPU count, default: 1) |
| `-o DIR` | Write output to a custom directory |
| `--kindle PATH` | Path to mounted Kindle device |
| `--import-only` | Copy book + annotation files from Kindle to `input/` without extracting |
| `--import-book` | Copy book + annotation files to `input/` and extract |
| `--import-metadata` | Copy only annotations to `input/pending/` (for DRM books) |
| `--calibre-library PATH` | Match DRM books to unlocked Calibre library files |
| `--accept-fuzzy` | Include fuzzy title matches in Calibre mode |
| `--all-books` | Match all synced books to Calibre, not just DRM-flagged |
| `--rematch` | Update Calibre book paths in sync state (requires `--calibre-library`) |
| `--missing-only` | Rematch only books whose Calibre files are missing (requires `--rematch`) |
| `--reprocess` | Reprocess previously successful books (bypass sync state skip logic) |
| `--reprocess-missing` | Reprocess only previously successful books whose output file is missing (requires `--calibre-library`) |
| `--dry-run` | Preview what would be done without making changes |
| `--limit N` | Process at most N books |

### Config file

To avoid repeating the same flags on every run, copy the example config and edit it:

```
cp config.yaml.example config.yaml
```

All supported keys are listed with comments in the example file. CLI flags always override config values. The config file is gitignored since it contains user-specific preferences.
