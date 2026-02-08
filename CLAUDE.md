# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Extracts highlights and notes from Kindle KFX books using synced annotation data. Designed for documents sent via "Send to Kindle" where highlight export isn't natively available.

## Workflow

1. Copy `.kfx` and `.yjr` files from Kindle's `documents/downloads` folder to `input/`
2. Run the pipeline:
   - **Bulk mode** (all books): `python extract_highlights.py`
   - **Single book**: `python extract_highlights.py input/<book>.kfx input/<annotations>.yjr`
   - **Kindle device mode**: `python extract_highlights.py --kindle /Volumes/Kindle`
3. Output goes to `output/`

Bulk mode automatically pairs `.kfx` and `.yjr` files by matching filenames (the `.yjr` filename starts with the `.kfx` stem).

### Kindle device mode

Connect a Kindle via USB and use `--kindle` to process directly from the device. The tool scans `documents/`, `documents/Downloads/`, and subdirectories of `Downloads/` (e.g. `Downloads/Items01/`) for `.kfx`/`.yjr` pairs.

```
# Default: process in-place from Kindle, output to output/
python extract_highlights.py --kindle /Volumes/Kindle

# Copy .kfx + .yjr to input/, don't extract
python extract_highlights.py --kindle /Volumes/Kindle --import-only

# Copy .kfx + .yjr to input/ AND run extraction
python extract_highlights.py --kindle /Volumes/Kindle --import-book

# Copy only .yjr to input/pending/ (for DRM books)
python extract_highlights.py --kindle /Volumes/Kindle --import-metadata

# Preview what would be done
python extract_highlights.py --kindle /Volumes/Kindle --dry-run

# Process only the first N books
python extract_highlights.py --kindle /Volumes/Kindle --limit 5
```

Kindle mode uses incremental sync via `.sync_state.json` — unchanged, previously successful books are skipped automatically. The sync state also serves as a book registry, recording all known paths for each book.

**DRM handling**: Books that fail with DRM errors are flagged separately. Use `--import-metadata` to copy just the `.yjr` annotations, then use `--calibre-library` to match them with unlocked Calibre KFX files.

### Calibre library mode

Match DRM-flagged books to unlocked KFX files in a Calibre library. Requires `.yjr` annotations to be imported first via `--kindle --import-metadata`.

```
# Preview matching report
python extract_highlights.py --calibre-library "/path/to/Calibre Library" --dry-run

# Process all ASIN-matched books
python extract_highlights.py --calibre-library "/path/to/Calibre Library"

# Include fuzzy title matches (shown in report but skipped by default)
python extract_highlights.py --calibre-library "/path/to/Calibre Library" --accept-fuzzy

# Process first N matched books
python extract_highlights.py --calibre-library "/path/to/Calibre Library" --limit 5

# Match ALL synced books (not just DRM-flagged) to Calibre KFX files
python extract_highlights.py --calibre-library "/path/to/Calibre Library" --all-books
```

Matching uses the ASIN extracted from Kindle filenames, looked up via `mobi-asin` in Calibre's `metadata.db`. Fuzzy title matching is available as a fallback for books without ASIN matches. Calibre KFX files are used in-place (no copying). The `calibre_library` config key provides a persistent default. Use `--all-books` to include successfully processed books (not just DRM-flagged) in the matching.

### Useful flags

- `-f {html,md,json,csv}` — Output format (default: html). Markdown for note-taking apps, JSON/CSV for programmatic use.
- `-q` / `--quiet` — Suppress per-highlight output, show summary only
- `--skip-existing` — Skip books whose output file already exists (bulk mode)
- `--title "My Title"` — Override the book title in output (single-pair mode)
- `--keep-json` — Keep intermediate JSON files (deleted by default after success)
- `-j N` / `--jobs N` — Process N books in parallel (0 = CPU count, default: 1)
- `-o DIR` — Write output to a custom directory
- `--kindle PATH` — Path to mounted Kindle device
- `--import-only` — Copy files from Kindle to input/ without extracting (requires `--kindle`)
- `--import-book` — Copy files to input/ and extract (requires `--kindle`)
- `--import-metadata` — Copy only .yjr to input/pending/ (requires `--kindle`)
- `--dry-run` — Preview what would be done without making changes
- `--limit N` — Process at most N books (works with both Kindle and bulk modes)
- `--calibre-library PATH` — Match DRM books to Calibre library KFX files
- `--accept-fuzzy` — Include fuzzy title matches in Calibre mode (default: ASIN-only)
- `--all-books` — Match all synced books to Calibre, not just DRM-flagged (requires `--calibre-library`)

### Re-exporting in a different format

The sync state (`.sync_state.json`) tracks processing status independently of output format. If you've already processed books via `--kindle` or `--calibre-library` and want to re-export in a different format (e.g. markdown after initially generating HTML), the sync state will skip them as "unchanged, previously successful."

To regenerate in a different format:

- **If files are in `input/`**: Use bulk mode, which doesn't consult sync state:
  ```
  python extract_highlights.py -f md
  ```
- **If using `--kindle` mode**: Either run bulk mode against `input/` (if files were imported), or delete `.sync_state.json` to force reprocessing.
- **If using `--calibre-library` mode**: Same as above — use bulk mode or clear the sync state.

Note: `--skip-existing` (bulk mode only) checks for the output file matching the *current* format, so it won't skip books that only have HTML output when you request markdown.

### Config file

Copy `config.yaml.example` to `config.yaml` to set persistent defaults (output format, quiet mode, jobs, etc.). CLI flags always override config values. See the example file for all supported keys. The `kindle_path` and `calibre_library` keys provide persistent defaults for `--kindle` and `--calibre-library`.

## Setup

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Architecture

Three scripts form a pipeline:

- **`extract_highlights.py`** — Entry point. Orchestrates the two-step pipeline by calling `krds.py` then `extract_highlights_kfxlib.py` as subprocesses. Supports single-pair mode (explicit arguments) or bulk mode (scans `input/` for paired files).
- **`krds.py`** — Third-party KRDS parser (GPL v3, by John Howell). Deserializes Kindle binary annotation files (`.yjr`, `.azw3f`, etc.) into JSON. Contains `KindleReaderDataStore` which handles the binary format, and `Deserializer` for low-level unpacking.
- **`extract_highlights_kfxlib.py`** — Core extraction logic. Uses the `kfxlib` library (from bundled `KFX Input.zip` or extracted `kfxlib_extracted/` directory) to decode KFX book content. Maps annotation positions from the JSON onto book content sections, resolves page numbers and TOC sections, and generates styled HTML or Markdown output. HTML includes automatic dark mode support via `prefers-color-scheme` media query.

## Key Dependencies

- **kfxlib**: The KFX Input Calibre plugin library, loaded from `KFX Input.zip` (or `kfxlib_extracted/` if present). Provides `yj_book.YJ_Book` for KFX decoding, `IonSymbol`, and `YJFragment` for navigation parsing.
- Standard pip packages: `pillow`, `pypdf`, `lxml`, `beautifulsoup4` (required by kfxlib)

## File Conventions

- Input files go in `input/`, output artifacts go in `output/` — both are gitignored.
- `.sync_state.json` — Book registry / sync state for Kindle mode (gitignored). Records all known books with paths, mtimes, and processing status.
- The `KFX Input.zip` is tracked via Git LFS (see `.gitattributes`).
- `highlights.css` — External CSS for HTML output. Edit to customize styling (colors, fonts, dark mode).
