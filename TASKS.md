# KFX Highlights Extraction - Tasks & Issues

## Known Issues

### `--skip-existing` flag doesn't work correctly with title-based naming

**Severity**: Medium
**Introduced in**: commit ddbfa33 (Use full book title for output filenames)

**Description**:

The `--skip-existing` flag in bulk mode (extract_highlights.py, line 1211) uses the KFX filename to determine the expected output file:

```python
output_file = output_dir / kfx.with_suffix(ext).name
```

However, since commit ddbfa33, the actual output filename is generated from the book's metadata title in `extract_highlights_kfxlib.py` (lines 477-480):

```python
safe_title = re.sub(r'[<>:"/\\|?*]', '_', title)
safe_title = re.sub(r'\s+', ' ', safe_title).strip()
output_name = safe_title + ext
```

This mismatch means `--skip-existing` checks for a different filename than what actually gets created, causing it to:
- Skip books that haven't been processed yet (if the KFX filename matches an existing output)
- Re-process books that have already been processed (if the title differs from the KFX filename)

**Impact**:

- Users relying on `--skip-existing` in bulk mode may get unexpected behavior
- The flag is primarily useful for incremental processing, which is less common in bulk mode
- Kindle mode uses sync state tracking instead, which isn't affected by this issue

**Potential Solutions**:

1. **Read metadata upfront** (expensive): Parse book metadata before deciding whether to skip, matching the actual output filename logic. This would slow down bulk mode significantly.

2. **Remove `--skip-existing` flag**: Since Kindle mode (the primary use case) uses sync state tracking, and bulk mode is typically used for one-time processing, this flag may not be essential.

3. **Document the limitation**: Add a note that `--skip-existing` works best when KFX filenames match book titles, and may not work correctly for books with truncated Kindle filenames.

4. **Add a warning**: Detect when title-based naming would differ from KFX-based naming and warn the user that `--skip-existing` may not work as expected.

**Workaround**:

For reliable incremental processing, use `--kindle` mode which maintains a sync state file (`.sync_state.json`) that tracks processed books by their actual metadata rather than filenames.

**Proposed Solutions for Discussion**:

**Option A: Add sync state tracking to bulk mode**
- When collision detection triggers (e.g., creating `Meditations-2.highlights.html`), record the mapping in `.sync_state.json`
- Store: `kfx_stem → actual_output_filename`
- Use this mapping to make `--skip-existing` work correctly in bulk mode

*Pros:*
- Fixes the `--skip-existing` issue
- Creates historical record of which KFX file produced which output
- Helps users understand collision-numbered filenames

*Challenges:*
- Bulk mode is intentionally stateless; adding sync state changes its design philosophy
- Sync state is currently keyed by KFX stem, but bulk mode could process the same KFX from different locations
- Output filename depends on metadata - still need to read book to determine output path
- Collision numbers aren't stable if files are deleted (deleting `Meditations-2.html` means next run might assign that number to a different book)

**Option B: Smart collision detection with metadata comparison**
- Store identifying metadata in output files (KFX stem, ASIN, or hash)
  - HTML: meta tags in `<head>`
  - JSON: top-level field
  - Markdown: YAML frontmatter
  - CSV: header comment
- On collision, check if existing file was created from the *same* source KFX
- If same source: overwrite/update the existing file
- If different source: use collision numbering

*Pros:*
- Re-running the same book updates its file instead of creating duplicates
- Different books with same title get unique numbered files
- Works without sync state - self-contained in output files
- Smaller change, doesn't affect bulk mode's stateless design

*Challenges:*
- Requires reading and parsing existing output files during collision detection
- Need to handle all four output formats differently
- Older output files without metadata would need migration or special handling

**Option C: Hybrid approach**
- Implement Option B (metadata in output files) for collision resolution
- Optionally use sync state in bulk mode for performance (cache metadata lookups)
- Make sync state usage opt-in for bulk mode via flag

---

## Future Enhancements

*This section reserved for planned features and improvements.*
