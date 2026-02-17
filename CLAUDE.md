# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Extracts highlights and notes from Kindle books (KFX and AZW3 formats) using synced annotation data. Designed for documents sent via "Send to Kindle" where highlight export isn't natively available.

## Supported Formats

- **KFX**: `.kfx` book + `.yjr` annotation sidecar. Uses `kfxlib` for content decoding.
- **AZW3**: `.azw3` book + `.azw3r`/`.azw3f` annotation sidecar. Uses Calibre's Python libraries (via `calibre-debug -e`) for MOBI decompression and KindleUnpack's K8Processor for skeleton/fragment/FDST processing. AZW3 annotation positions are byte offsets into the KF8 "Flow 0" content (assembled text after skeleton processing), not raw HTML.

## Workflow

1. Copy book and annotation files from Kindle's `documents/downloads` folder to `input/`
2. Run the pipeline:
   - **Bulk mode** (all books): `python extract_highlights.py`
   - **Single KFX book**: `python extract_highlights.py input/<book>.kfx input/<annotations>.yjr`
   - **Single AZW3 book**: `python extract_highlights.py input/<book>.azw3 input/<annotations>.azw3r`
   - **Kindle device mode**: `python extract_highlights.py --kindle /Volumes/Kindle`
3. Output goes to `output/`

Bulk mode automatically pairs book and annotation files by matching filenames (the annotation filename starts with the book stem).

### Kindle device mode

Connect a Kindle via USB and use `--kindle` to process directly from the device. The tool scans `documents/`, `documents/Downloads/`, and subdirectories of `Downloads/` (e.g. `Downloads/Items01/`) for book/annotation pairs (both KFX and AZW3).

```
# Default: process in-place from Kindle, output to output/
python extract_highlights.py --kindle /Volumes/Kindle

# Copy book + annotation files to input/, don't extract
python extract_highlights.py --kindle /Volumes/Kindle --import-only

# Copy book + annotation files to input/ AND run extraction
python extract_highlights.py --kindle /Volumes/Kindle --import-book

# Copy only annotation files to input/pending/ (for DRM books)
python extract_highlights.py --kindle /Volumes/Kindle --import-metadata

# Preview what would be done
python extract_highlights.py --kindle /Volumes/Kindle --dry-run

# Process only the first N books
python extract_highlights.py --kindle /Volumes/Kindle --limit 5
```

Kindle mode uses incremental sync via `.sync_state.json` — unchanged, previously successful books are skipped automatically. The sync state also serves as a book registry, recording all known paths for each book.

**DRM handling**: Books that fail with DRM errors are flagged separately. Use `--import-metadata` to copy just the annotations, then use `--calibre-library` to match them with unlocked Calibre files.

### Calibre library mode

Match DRM-flagged books to unlocked files in a Calibre library. Supports KFX, KFX-ZIP, and AZW3 formats in Calibre (prefers KFX > KFX-ZIP > AZW3). Requires annotations to be imported first via `--kindle --import-metadata`.

```
# Preview matching report
python extract_highlights.py --calibre-library "/path/to/Calibre Library" --dry-run

# Process all ASIN-matched books
python extract_highlights.py --calibre-library "/path/to/Calibre Library"

# Include fuzzy title matches (shown in report but skipped by default)
python extract_highlights.py --calibre-library "/path/to/Calibre Library" --accept-fuzzy

# Process first N matched books
python extract_highlights.py --calibre-library "/path/to/Calibre Library" --limit 5

# Match ALL synced books (not just DRM-flagged) to Calibre files
python extract_highlights.py --calibre-library "/path/to/Calibre Library" --all-books
```

Matching uses the ASIN extracted from Kindle filenames, looked up via `mobi-asin` in Calibre's `metadata.db`. Fuzzy title matching is available as a fallback for books without ASIN matches. Calibre files are used in-place (no copying). The `calibre_library` config key provides a persistent default. Use `--all-books` to include successfully processed books (not just DRM-flagged) in the matching.

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
- `--import-metadata` — Copy only annotations to input/pending/ (requires `--kindle`)
- `--dry-run` — Preview what would be done without making changes
- `--limit N` — Process at most N books (works with both Kindle and bulk modes)
- `--calibre-library PATH` — Match DRM books to Calibre library files
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

**Note on operation modes**: You can safely set both `kindle_path` and `calibre_library` in your config. The script determines which mode to use based on **explicit CLI flags**, not config values:

- **No flags** → Bulk mode (processes files in `input/`)
- **`--kindle`** → Kindle device mode (uses `kindle_path` from config if no path specified)
- **`--calibre-library`** → Calibre matching mode (uses `calibre_library` from config if no path specified)

This allows you to set both paths in your config and switch modes by simply adding the appropriate flag.

## Setup

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

AZW3 support additionally requires [Calibre](https://calibre-ebook.com/) installed (uses `calibre-debug -e` for MOBI decompression). KindleUnpack modules for KF8 processing are vendored in the `KindleUnpack/` directory.

## Architecture

Four scripts form a pipeline:

- **`extract_highlights.py`** — Entry point. Orchestrates the pipeline by calling `krds.py` then `extract_highlights_kfxlib.py` (for KFX) or `extract_highlights_azw3.py` (for AZW3) as subprocesses. Supports single-pair mode (explicit arguments) or bulk mode (scans `input/` for paired files).
- **`krds.py`** — Third-party KRDS parser (GPL v3, by John Howell). Deserializes Kindle binary annotation files (`.yjr`, `.azw3f`, `.azw3r`, etc.) into JSON. Contains `KindleReaderDataStore` which handles the binary format, and `Deserializer` for low-level unpacking.
- **`extract_highlights_kfxlib.py`** — KFX extraction logic. Uses the `kfxlib` library (from bundled `KFX Input.zip` or extracted `kfxlib_extracted/` directory) to decode KFX book content. Maps annotation positions from the JSON onto book content sections, resolves page numbers and TOC sections, and generates styled HTML or Markdown output.
- **`extract_highlights_azw3.py`** — AZW3 extraction logic. Runs under Calibre's Python environment (via `calibre-debug -e`). Uses Calibre's `MobiReader` for MOBI decompression and KindleUnpack's `K8Processor` to reconstruct KF8 Flow 0 content (via skeleton/fragment/FDST processing). Maps byte-offset annotation positions to this assembled text and outputs intermediate JSON to stdout. The orchestrator handles formatting.

## Key Dependencies

- **kfxlib**: The KFX Input Calibre plugin library, loaded from `KFX Input.zip` (or `kfxlib_extracted/` if present). Provides `yj_book.YJ_Book` for KFX decoding, `IonSymbol`, and `YJFragment` for navigation parsing.
- **Calibre** (for AZW3): Provides `calibre-debug -e` for running the AZW3 extractor with access to Calibre's MOBI decompression libraries.
- **KindleUnpack** (for AZW3): Vendored in `KindleUnpack/` directory. Provides `K8Processor`, `MobiHeader`, and `Sectionizer` for proper KF8 skeleton/fragment/FDST processing to extract Flow 0 content. Licensed under GPL-3.0.
- Standard pip packages: `pillow`, `pypdf`, `lxml`, `beautifulsoup4` (required by kfxlib)

## File Conventions

- Input files go in `input/`, output artifacts go in `output/` — both are gitignored.
- `.sync_state.json` — Book registry / sync state for Kindle mode (gitignored). Records all known books with paths, mtimes, and processing status.
- The `KFX Input.zip` is tracked via Git LFS (see `.gitattributes`).
- `highlights.css` — External CSS for HTML output. Edit to customize styling (colors, fonts, dark mode).
