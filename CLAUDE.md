# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Extracts highlights and notes from Kindle KFX books using synced annotation data. Designed for documents sent via "Send to Kindle" where highlight export isn't natively available.

## Workflow

1. Copy `.kfx` and `.yjr` files from Kindle's `documents/downloads` folder to `input/`
2. Run the pipeline:
   - **Bulk mode** (all books): `python extract_highlights.py`
   - **Single book**: `python extract_highlights.py input/<book>.kfx input/<annotations>.yjr`
3. Output HTML goes to `output/`

Bulk mode automatically pairs `.kfx` and `.yjr` files by matching filenames (the `.yjr` filename starts with the `.kfx` stem).

### Useful flags

- `-f {html,md,json,csv}` — Output format (default: html). Markdown for note-taking apps, JSON/CSV for programmatic use.
- `-q` / `--quiet` — Suppress per-highlight output, show summary only
- `--skip-existing` — Skip books whose output file already exists (bulk mode)
- `--title "My Title"` — Override the book title in output (single-pair mode)
- `--keep-json` — Keep intermediate JSON files (deleted by default after success)
- `-j N` / `--jobs N` — Process N books in parallel (0 = CPU count, default: 1)
- `-o DIR` — Write output to a custom directory

### Config file

Copy `config.yaml.example` to `config.yaml` to set persistent defaults (output format, quiet mode, jobs, etc.). CLI flags always override config values. See the example file for all supported keys.

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
- The `KFX Input.zip` is tracked via Git LFS (see `.gitattributes`).
- `highlights.css` — External CSS for HTML output. Edit to customize styling (colors, fonts, dark mode).
