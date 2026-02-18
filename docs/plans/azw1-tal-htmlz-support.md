# Plan: AZW1 (Topaz) / TAL / HTMLZ Support (The Anti-Christ)

## Problem

*The Anti-Christ* is on the Kindle as an `.azw1` file (Amazon Topaz/TPZ format — a scanned-page
format, not MOBI or KF8). The annotation sidecar is a `.tal` file (KRDS format, 308 highlights,
90 notes). `krds.py` already decodes `.tal` correctly, and the positions are **plain integers**,
identical in structure to AZW3 byte offsets:

```
"startPosition": "14297",
"endPosition":   "14354"
```

The Calibre library has the book as `.htmlz` (a zip containing `book.html`, CSS, and images).
Calibre converted it from the Topaz source. The HTMLZ `book.html` is a single HTML file with
all the text — and the byte offsets in `.tal` almost certainly index into the raw bytes of
that `book.html` content (or into the assembled text of the Topaz internal structure that
Calibre mirrored). This needs a brief verification step before implementation.

**This is simpler than the MOBI6 plan.** The annotation format is already handled by `krds.py`
and the position format is the same as AZW3. The main new work is:

1. Recognizing `.azw1` + `.tal`/`.tas` as a valid pair on Kindle.
2. Writing an extractor that reads text from HTMLZ.
3. Wiring the Calibre index to return `.htmlz` paths.

## Verification Step (Before Coding)

Before implementing, confirm that `.tal` byte offsets correspond to `book.html` byte positions:

```python
import zipfile
with zipfile.ZipFile("The Anti-Christ - Friedrich Nietzsche.htmlz") as z:
    html = z.read("book.html")
# First highlight: startPosition=14297, endPosition=14354
print(repr(html[14297:14354]))
```

If the output is recognizable text (possibly with HTML tags around it), the HTMLZ approach
works directly. If it's garbled, the offsets may index into the Topaz internal representation —
in which case we'd need to investigate the TPZ format or use a different Calibre conversion
target (EPUB or TXT).

## Required Changes

### 1. New extractor: `extract_highlights_htmlz.py`

Runs as a standalone script invoked via `sys.executable` (no `calibre-debug` needed).

**Steps:**

1. Open the `.htmlz` zip and read `book.html` as bytes.
2. Parse annotation positions: they are plain integers (same as AZW3, just use `int(pos_str)`).
3. For each highlight: slice `html_bytes[start:end]`, call `snap_to_tag_boundaries()` +
   `strip_html_tags()` (copy the helpers from `extract_highlights_azw3.py`, or factor them
   into a shared `highlight_utils.py`).
4. Extract metadata (title, author, year) from the HTMLZ `book.opf` file or from the HTML
   `<meta>` tags in `book.html` (the HTMLZ for Anti-Christ has `<meta name="Author">`,
   `<meta name="Title">`, and `<meta name="ASIN">` in the HTML head).
5. Build a page map from `<div id="pageNNNN">` markers that Calibre embeds in HTMLZ output
   (confirmed present in the Anti-Christ HTMLZ: `id="page0110"`, etc.).
6. Output `{title, authors, year, items}` JSON to stdout — same schema as the AZW3 extractor.

Script signature:

```
python extract_highlights_htmlz.py <json_file> <htmlz_file> [--title TITLE]
```

### 2. Format detection in `extract_highlights.py`

**`_BOOK_FORMATS`** — add `.azw1` with `.tal` and `.tas` annotation extensions (prefer `.tal`):

```python
_BOOK_FORMATS = {
    ".kfx":  [".yjr"],
    ".azw3": [".azw3r", ".azw3f"],
    ".azw1": [".tal", ".tas"],
}
```

**`process_pair()`** — add a detection function and branch:

```python
def _is_htmlz_source(book_file):
    return Path(book_file).suffix.lower() in (".azw1", ".htmlz")
```

Invoke `extract_highlights_htmlz.py` via `sys.executable` (no Calibre wrapper), capture stdout
JSON, pass through `_format_azw3_output()`.

**`find_annotation_for_stem()`** — extend `all_ann_exts` to include `.tal` and `.tas`.

**`build_calibre_index()`** — extend to include `'HTMLZ'` in the format query. Add `HTMLZ`
to the format priority map (lower priority than KFX/AZW3 since it's a conversion artifact):
`KFX (0) > KFX-ZIP (1) > AZW3 (2) > HTMLZ (3)`. Construct the book path with `.htmlz`
extension.

Note: The Calibre entry for Anti-Christ does **not** have a `mobi-asin` identifier in
`metadata.db` (confirmed: the ASIN `B001C329DU` is in the HTML meta tag but not in the DB).
Add it via Calibre's UI or directly to the DB, or rely on fuzzy title matching. The title
"The Anti-Christ" should fuzzy-match well.

**`find_kindle_pairs()`** and **`find_pairs()`** — already generic over `_BOOK_FORMATS`, no
additional changes needed.

### 3. Calibre library matching

The Anti-Christ lives in two Calibre entries (IDs 7255 and 14689), both with only HTMLZ format.
After adding HTMLZ to the format list, the Calibre index will pick up whichever entry matches.
If neither has a `mobi-asin` identifier, fuzzy matching on "The Anti-Christ" will find it.

The book in Calibre at ID 7255 has path:
`Friedrich Nietzsche/The Anti-Christ (7255)/The Anti-Christ - Friedrich Nietzsche.htmlz`

### 4. Import metadata flow

The `.tal` file is inside the `.sdr/` folder alongside the `.azw1` book — same structure as
every other Kindle format. `import_metadata_only()` copies the file generically by path; no
changes needed beyond adding `.tal`/`.tas` to the extension lists.

## Testing Plan

1. **Verify offsets** (see Verification Step above):
   ```python
   import zipfile
   with zipfile.ZipFile("The Anti-Christ - Friedrich Nietzsche.htmlz") as z:
       html = z.read("book.html")
   print(repr(html[14297:14354]))
   ```

2. Run `krds.py` on the `.tal` file and confirm 308 highlights / 90 notes.

3. Write `extract_highlights_htmlz.py`; test standalone:
   ```
   python extract_highlights_htmlz.py /tmp/ann.tal.json \
       "/path/to/The Anti-Christ.htmlz" | python -m json.tool | head -60
   ```

4. Run end-to-end via `--calibre-library` (with `--accept-fuzzy` if ASIN is not in the DB).

## What Does Not Need to Change

- `krds.py` — already handles `.tal`.
- `_format_azw3_output()` — output schema is identical.
- All output formatters and sync state structure.
- `--import-metadata` flow (generic by file path).

## Shared Code Opportunity

Both this plan and the MOBI6 plan duplicate `snap_to_tag_boundaries()` and `strip_html_tags()`
from `extract_highlights_azw3.py`. If implementing both, consider factoring those into a small
shared `highlight_utils.py` that all three extractors import. Not required for either plan to
work independently.
