# Plan: MOBI6 / MBP1 Support (A Book of Five Rings)

## Problem

*A Book of Five Rings* is on the Kindle as an `.azw` file (MOBI6, DRM-encrypted). The Calibre
library has an unlocked `.mobi` copy (MOBI6, unencrypted) and a DRM-encrypted `.azw`. The
annotation sidecar in the `.sdr/` folder is a `.mbp1` file (58 KB, 124 highlights, 11 notes).

`krds.py` can already deserialize `.mbp1` — it uses the same KRDS binary format. The problem is
that the annotation positions look nothing like KFX or AZW3 positions:

```
"startPosition": "53373:53373:57005:REFUQQAAAHBFQkFSAAAA..."
```

The four colon-separated fields are: `start_offset:spine_offset:total_length:base64_blob`.
The **first field** (`53373`) is the byte offset into the decompressed MOBI6 raw markup (the
"rawML"). This is PalmDoc- or Huffman-compressed HTML stored in the PalmDB record list.

The base64 blob is a Kindle CDEKey structure used for sync/DRM purposes — it is not needed
for text extraction.

## Required Changes

### 1. New extractor: `extract_highlights_mobi.py`

Runs as a standalone script (like `extract_highlights_azw3.py`) invoked from `process_pair()`.
Does **not** need `calibre-debug`; standard Python can decompress PalmDoc via Calibre's
libraries or a pure-Python implementation.

**Options for decompression:**
- Use `calibre-debug -e` like the AZW3 path, importing `calibre.ebooks.mobi.reader.mobi6.MobiReader`
  which exposes `getRawML()`. This reuses the existing Calibre integration and avoids adding a
  new dependency.
- Alternatively, use KindleUnpack's `MobiHeader` + `Sectionizer` (already vendored) to get
  `getRawML()` directly. This avoids needing `calibre-debug` at all.

**Recommendation:** Use the vendored KindleUnpack path (`MobiHeader` + `Sectionizer`) so the
script can run in the standard `.venv` Python environment without `calibre-debug`. The AZW3
extractor needed Calibre for KF8 skeleton processing; MOBI6 rawML is simpler and KindleUnpack
handles it.

**Steps inside the extractor:**

1. Parse the `.mobi` file with `Sectionizer` + `MobiHeader` from KindleUnpack.
2. Call `mh.getRawML()` to get the decompressed raw markup bytes (the full book HTML).
3. Parse annotation positions: split each position string on `:`, take field `[0]` as the
   byte offset integer.
4. For each highlight: slice `raw_html[start:end]`, call `snap_to_tag_boundaries()` +
   `strip_html_tags()` (same helpers as the AZW3 extractor — can be copied or factored out).
5. Extract metadata (title, authors, year) from the MOBI EXTH header via `MobiHeader`.
6. Build a page map from `<mbp:pagebreak>` tags (same as AZW3 extractor).
7. Output `{title, authors, year, items}` JSON to stdout.

The script signature should match the AZW3 extractor:

```
python extract_highlights_mobi.py <json_file> <mobi_file> [--title TITLE]
```

### 2. Format detection in `extract_highlights.py`

**`_BOOK_FORMATS`** — add `.mobi` and `.azw` as recognized book extensions with `.mbp1` and
`.mbs` as their annotation extensions (prefer `.mbp1`):

```python
_BOOK_FORMATS = {
    ".kfx":  [".yjr"],
    ".azw3": [".azw3r", ".azw3f"],
    ".mobi": [".mbp1", ".mbs"],
    ".azw":  [".mbp1", ".mbs"],
}
```

**`process_pair()`** — add a branch for MOBI/AZW books:

```python
def _is_mobi(book_file):
    return Path(book_file).suffix.lower() in (".mobi", ".azw")
```

Invoke the new extractor the same way as the AZW3 extractor (via `subprocess.run`), capture
stdout JSON, and pass it through `_format_azw3_output()` (the output schema is identical).
No `calibre-debug` wrapper needed — use `sys.executable` directly.

**`find_annotation_for_stem()`** — extend `all_ann_exts` to include `.mbp1` and `.mbs`.

**`build_calibre_index()`** — extend the SQL `WHERE d.format IN (...)` to include `'MOBI'`
and the format priority map. The Calibre book path construction is identical.

**`find_kindle_pairs()`** and **`find_pairs()`** — already iterate over `_BOOK_FORMATS`, so
adding the new extensions to that dict is sufficient.

### 3. Calibre library matching for Five Rings

Five Rings is stored in Calibre as both `.azw` (DRM-encrypted) and `.mobi` (unencrypted).
The `build_calibre_index()` query needs to return the `.mobi` path when it's available.

Updated format priority: `KFX (0) > KFX-ZIP (1) > AZW3 (2) > MOBI (3) > AZW (4)`.

The ASIN `B004L9L6B8` is embedded in the Kindle filename. Add it to Calibre's `metadata.db`
as a `mobi-asin` identifier for the correct book entry if it isn't already present — or rely
on fuzzy title matching as a fallback.

### 4. Sync state / `--import-metadata` flow

The `.mbp1` file is inside the `.sdr/` folder (same pattern as `.yjr` and `.azw3r`). The
`import_metadata_only()` and `filter_new_or_changed()` functions work on the annotation file
path generically — no changes needed there beyond the extension additions above.

## Testing Plan

1. Run `krds.py` on the `.mbp1` directly and confirm 124 highlights / 11 notes decode.
2. Write `extract_highlights_mobi.py`; test it standalone:
   ```
   python extract_highlights_mobi.py /tmp/ann.mbp1.json \
       "/path/to/A Book of Five Rings.mobi" | python -m json.tool | head -50
   ```
3. Verify the first few offsets produce sensible text by cross-referencing with the book
   contents.
4. Run end-to-end via `--calibre-library` after adding MOBI to the format list.

## What Does Not Need to Change

- `krds.py` — already handles `.mbp1`.
- `_format_azw3_output()` — the output schema is the same.
- Output formatters (HTML, Markdown, JSON, CSV).
- Sync state structure (field names are already format-agnostic).
