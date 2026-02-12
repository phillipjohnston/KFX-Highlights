# KFX Highlights
Uses Synced Kindle Annotations to Make Highlights File

Most of the documents I read on my Kindle are sent via "Send to Kindle" so that I can read them on other devices. However, one of the issues I've noticed is that there's no way to extract synced highlights. This tool extracts them by reading the KFX book files and YJR annotation files directly.

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

## Usage

### Kindle device mode (recommended)

Connect your Kindle via USB, then process directly from the device:

```
python extract_highlights.py --kindle /Volumes/Kindle
```

This scans the Kindle's `documents/` and `documents/Downloads/` directories, finds all `.kfx`/`.yjr` pairs, and extracts highlights to `output/`. It uses incremental sync — unchanged books that were previously processed are skipped automatically.

You can set a persistent default in `config.yaml` so you don't need to pass the path every time:

```yaml
kindle_path: /Volumes/Kindle
```

#### Import modes

Instead of processing in-place, you can copy files from the Kindle to `input/`:

```bash
# Copy .kfx + .yjr to input/ without extracting
python extract_highlights.py --kindle /Volumes/Kindle --import-only

# Copy .kfx + .yjr to input/ AND run extraction
python extract_highlights.py --kindle /Volumes/Kindle --import-book

# Copy only .yjr annotations to input/pending/ (for DRM-protected books)
python extract_highlights.py --kindle /Volumes/Kindle --import-metadata
```

#### DRM-protected books

Books with DRM will fail extraction and are flagged separately in the output. Use `--import-metadata` to copy just the annotation files, then use `--calibre-library` to automatically match them with unlocked KFX files in your Calibre library (see below). Alternatively, manually place an unlocked `.kfx` in `input/` and run in bulk mode.

#### Testing and preview

```bash
# Preview what would be done without touching any files
python extract_highlights.py --kindle /Volumes/Kindle --dry-run

# Process only the first N books
python extract_highlights.py --kindle /Volumes/Kindle --limit 5
```

### Calibre library mode

If you have a Calibre library with unlocked KFX copies of your DRM-protected Kindle books, this mode automatically matches them by ASIN and extracts highlights using the Calibre KFX files directly (no file copying needed).

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
```

By default, only DRM-flagged books are matched. Use `--all-books` to also include books that were already successfully processed from the Kindle — useful if you want to re-extract using the Calibre KFX version instead.

The matching report shows four categories: ASIN-matched with KFX (processable), matched but no KFX format in Calibre, matched with KFX but no `.yjr` imported yet, and unmatched. You can set a persistent default in `config.yaml`:

```yaml
calibre_library: /path/to/Calibre Library
```

### Bulk mode (all books at once)

Place all `.kfx` and `.yjr` files in the `input/` directory, then run:

```
python extract_highlights.py
```

This scans `input/` and automatically pairs `.kfx` files with their corresponding `.yjr` annotation files by matching filenames. Each pair is processed sequentially, and failures are reported at the end without aborting the whole run.

`--dry-run` and `--limit N` work in bulk mode too.

### Single book

```
python extract_highlights.py <book.kfx> <annotations.yjr>
```

All modes will:
1. Convert the YJR file to JSON using `krds.py`.
2. Call `extract_highlights_kfxlib.py` with the generated JSON and KFX file to create the HTML highlights file.

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
| `--import-only` | Copy files from Kindle to `input/` without extracting |
| `--import-book` | Copy files to `input/` and extract |
| `--import-metadata` | Copy only `.yjr` to `input/pending/` (for DRM books) |
| `--calibre-library PATH` | Match DRM books to Calibre library KFX files |
| `--accept-fuzzy` | Include fuzzy title matches in Calibre mode |
| `--all-books` | Match all synced books to Calibre, not just DRM-flagged |
| `--reprocess` | Reprocess previously successful books (bypass sync state skip logic) |
| `--dry-run` | Preview what would be done without making changes |
| `--limit N` | Process at most N books |

### Config file

To avoid repeating the same flags on every run, copy the example config and edit it:

```
cp config.yaml.example config.yaml
```

All supported keys are listed with comments in the example file. CLI flags always override config values. The config file is gitignored since it contains user-specific preferences.
