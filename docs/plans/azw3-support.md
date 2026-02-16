# Plan: Add AZW3 Book Support

## Context

Some Kindle books are only available in AZW3 format (not KFX). These books have `.azw3` book files and `.azw3r`/`.azw3f` annotation sidecars. The current pipeline only handles `.kfx` + `.yjr` pairs. KRDS already parses `.azw3r`/`.azw3f` annotations into the same JSON format, but we need a new content extractor for AZW3 books and updated file discovery logic throughout.

**Key finding from prototyping**: AZW3 annotation positions are **byte offsets into the decompressed KF8 HTML**. We verified this by decompressing the raw MOBI text records using Calibre's `HuffReader` and checking that annotation positions map correctly to book content. The decompressed content is raw HTML — text extraction requires stripping tags while respecting byte positions.

## Approach

### Dependency: Calibre's Python libraries

AZW3 uses HUFF/CDIC or PalmDoc compression. Calibre's bundled `HuffReader` and `MobiReader` handle decompression. Rather than bundling a standalone MOBI library, we'll use Calibre's Python (via `calibre-debug -e`) as a subprocess — similar to how we already use `krds.py` as a subprocess. This avoids adding heavy dependencies while leveraging battle-tested code.

**Alternative considered**: Using Calibre as a Python import in our venv. Rejected because Calibre's Python environment is self-contained and can't be imported from a standard venv.

### Architecture: New `extract_highlights_azw3.py` script

Parallel to `extract_highlights_kfxlib.py`, create `extract_highlights_azw3.py` that:
1. Takes an AZW3 path + annotation JSON path as arguments
2. Decompresses the AZW3 book using Calibre's `MobiReader` + `HuffReader`
3. Extracts plain text from HTML byte ranges using annotation positions
4. Produces the same output format (HTML/Markdown/JSON/CSV) using the same formatting functions

This script runs under Calibre's Python (`calibre-debug -e`), so it has access to Calibre's libraries natively.

### Files to modify

1. **`extract_highlights_azw3.py`** (NEW) — AZW3 content extraction + highlight mapping
   - Decompresses KF8 HTML from AZW3 using `calibre.ebooks.mobi.reader.mobi6.MobiReader` + `calibre.ebooks.mobi.huffcdic.HuffReader`
   - Maps byte-offset positions from annotations to HTML content
   - Strips HTML tags to extract plain text for each highlight range
   - Extracts metadata (title, authors) from MOBI EXTH header
   - Reuses output formatting from `extract_highlights_kfxlib.py` (or shares via common module)
   - Handles both HUFF/CDIC (compression type `DH`) and PalmDoc LZ77 (compression type 2)

2. **`extract_highlights.py`** — Orchestrator updates
   - `find_pairs()`: Also scan for `.azw3` + `.azw3r`/`.azw3f` pairs in `input/`
   - `find_kindle_pairs()`: Also discover `.azw3` books and look for `.azw3r`/`.azw3f` in `.sdr/` directories
   - `process_pair()`: Detect book format by extension, dispatch to `extract_highlights_azw3.py` (via `calibre-debug -e`) for AZW3, or existing `extract_highlights_kfxlib.py` for KFX
   - `build_calibre_index()`: Also look for AZW3 format entries in Calibre library (currently only checks KFX/KFX-ZIP)
   - Sync state: Use book stem as key (already does), but handle `.azw3` alongside `.kfx`
   - `--import-only`/`--import-book`/`--import-metadata`: Handle `.azw3` + `.azw3r`/`.azw3f` file copying

3. **`CLAUDE.md`** — Update documentation to mention AZW3 support

### Implementation details

#### AZW3 decompression (in `extract_highlights_azw3.py`)

```python
# Runs under calibre-debug -e, so calibre imports are available
from calibre.ebooks.mobi.reader.mobi6 import MobiReader
from calibre.ebooks.mobi.huffcdic import HuffReader

reader = MobiReader(azw3_path)
bh = reader.book_header

# Build decompressor based on compression type
if bh.compression_type == b'DH':  # HUFF/CDIC
    huff_sections = [reader.sections[bh.huff_offset + i][0] for i in range(bh.huff_number)]
    huffr = HuffReader(huff_sections)
    decompress = huffr.unpack
elif bh.compression_type == 2:  # PalmDoc LZ77
    from calibre.ebooks.compression.palmdoc import decompress as palmdoc_decompress
    decompress = palmdoc_decompress
else:  # No compression
    decompress = lambda x: x

# Concatenate decompressed text records
parts = []
for i in range(1, bh.records + 1):
    rec = reader.sections[i][0]
    trail = reader.sizeof_trailing_entries(rec)
    if trail:
        rec = rec[:-trail]
    parts.append(decompress(rec))
all_html = b''.join(parts)
```

#### Annotation pairing for AZW3

- `.azw3` books can have BOTH `.azw3f` and `.azw3r` sidecars
- `.azw3f` stores reading position/bookmarks, `.azw3r` stores highlights/notes
- Prefer `.azw3r` for highlight extraction (verified: that's where highlights live)
- Fallback to `.azw3f` if `.azw3r` not present
- Annotation filenames follow same Kindle convention: `BookName_ASINhash.azw3r`

#### Position mapping

- AZW3 positions are raw byte offsets into the decompressed HTML
- KFX positions use `"prefix:offset"` format (split on `:` to get integer)
- AZW3 positions are plain integers (no `:` separator)
- Text extraction: slice `all_html[start:end]`, decode to string, strip HTML tags, unescape entities

#### Subprocess invocation

`extract_highlights.py` currently calls `extract_highlights_kfxlib.py` via `subprocess.run([sys.executable, script, ...])`. For AZW3, it will call:

```python
subprocess.run([calibre_debug_path, '-e', azw3_script, ...])
```

Need to discover the `calibre-debug` path (check PATH, then common locations like `/Applications/calibre.app/Contents/MacOS/`).

#### Shared output formatting

The output formatting code (HTML generation, Markdown, JSON, CSV) currently lives in `extract_highlights_kfxlib.py`. To avoid duplication, have `extract_highlights_azw3.py` output JSON to stdout (same structure as the intermediate JSON the pipeline already uses), then let the existing formatting pipeline handle it. This minimizes code duplication and keeps the AZW3 script focused on extraction.

### Step-by-step implementation order

1. Create `extract_highlights_azw3.py` — standalone script that runs under `calibre-debug -e`, takes AZW3 + annotation JSON args, outputs intermediate JSON to stdout
2. Update `extract_highlights.py` — `process_pair()` to dispatch based on file extension
3. Update `find_pairs()` — scan for `.azw3` + `.azw3r`/`.azw3f` pairs
4. Update `find_kindle_pairs()` — discover AZW3 books with annotation sidecars
5. Update `build_calibre_index()` — include AZW3 format in Calibre library matching
6. Update import modes — handle AZW3 file copying
7. Update `CLAUDE.md` — document AZW3 support

## Verification

1. **Single book test**: Process the 4-Hour Chef AZW3 from Calibre library with its `.azw3r` annotations
   ```
   python extract_highlights.py /path/to/book.azw3 /path/to/annotations.azw3r.json
   ```
2. **Kindle mode test**: Connect Kindle, run `--kindle` and verify AZW3 books are discovered alongside KFX
3. **Calibre mode test**: Run `--calibre-library` with `--dry-run` and verify AZW3 books appear in matching report
4. **Bulk mode test**: Copy an AZW3 + `.azw3r` pair to `input/` and run bulk extraction
5. **Output format test**: Verify HTML, Markdown, JSON, CSV all work for AZW3 books

## Research notes

### Prototyping results (2026-02-16)

Tested with "The 4-Hour Chef" (ASIN: B005NJU8PA):
- **Kindle device**: Has `.azw3` book + `.azw3f` and `.azw3r` in `.sdr/` directory
- **Calibre library**: Has DRM-free `.azw3` copy (book ID 3412)
- **KRDS parsing**: `.azw3r` contains 309 highlights and 28 notes; `.azw3f` only has reading position data
- **Position format**: Plain integers (e.g., `"329975"`) — byte offsets into decompressed HTML
- **Decompression**: HUFF/CDIC (`compression_type == b'DH'`), 629 text records, 2,563,129 bytes decompressed
- **Text extraction**: Slicing `all_html[start:end]` + HTML tag stripping produces correct highlight text
- **Calibre's MobiReader**: `text_section()` method does NOT decompress correctly; manual `HuffReader` approach required
