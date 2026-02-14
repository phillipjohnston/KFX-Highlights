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

---

## Future Enhancements

*This section reserved for planned features and improvements.*
