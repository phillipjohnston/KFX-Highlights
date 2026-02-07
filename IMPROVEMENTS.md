# KFX-Highlights Improvement Checklist

Improvement opportunities identified for the KFX highlight extraction pipeline.

## High-Impact Workflow Improvements

- [x] **`--skip-existing` flag** — Bulk mode skips books whose output already exists.

- [x] **Markdown export (`--format md`)** — Output to Markdown for Obsidian, Notion, Bear, etc.

- [x] **Better title handling** — `clean_title()` strips ISBNs, order IDs, and email fragments from fallback filenames. `--title` flag allows explicit override.

- [x] **Intermediate file cleanup** — JSON files auto-deleted after successful extraction. `--keep-json` preserves them.

- [x] **`--quiet` flag** — Suppresses per-highlight console output, shows summary only.

## Medium-Impact Enhancements

- [x] **Better citation handling** — Gracefully omits missing author/year fields instead of showing broken formatting.

- [x] **Preserve paragraph structure** — Newlines preserved in highlight text. HTML uses `<br/>`, Markdown uses multi-line blockquotes.

- [ ] **Highlight color support** — All highlights rendered as yellow. Kindle supports multiple colors for categorization. *(Deferred: only one template value found in sample data. The `template` field is `"0\ufffc0"` format where the first digit likely maps to color index. Needs multi-color test data.)*

- [x] **`requirements.txt`** — Standard pip dependency file with minimum version constraints.

## Optimization Opportunities

- [x] **Parallel bulk processing** — `-j/--jobs N` flag for concurrent processing (0 = CPU count). Default remains sequential.

- [x] **Externalize CSS** — CSS moved to `highlights.css`, loaded and inlined at generation time. Easy to customize without editing Python.

- [ ] **Config file support** — Persistent preferences via `config.yaml` (output format, citation style, theme, etc.) to reduce repeated CLI flags. *(new config loading in extract_highlights.py)*

## Lower Priority but Valuable

- [x] **JSON/CSV export** — `--format json` and `--format csv` for programmatic analysis or spreadsheet import.

- [ ] **Chapter grouping in output** — Group highlights by TOC section/chapter instead of flat list. *(extract_highlights_kfxlib.py — restructure `items` list by section before rendering)*

- [x] **Dark mode CSS** — Automatic dark mode via `prefers-color-scheme` media query.

- [x] **Deduplication** — Overlapping highlights detected and merged, keeping the longer range.

- [x] **Stats summary in output** — Highlight/note counts, section count, and date range in output header.

- [ ] **Auto-detect connected Kindle** — Scan for mounted Kindle device and copy files automatically. *(extract_highlights.py — platform-specific mount point detection, scan /Volumes for Kindle)*
