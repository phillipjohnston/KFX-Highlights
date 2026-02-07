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

- [ ] **Highlight color support** — All highlights rendered as yellow. Kindle supports multiple colors for categorization. Preserve the original color. *(krds.py annotation data may include color info; extract_highlights_kfxlib.py — use actual color)*

- [x] **`requirements.txt`** — Standard pip dependency file with minimum version constraints.

## Optimization Opportunities

- [ ] **Parallel bulk processing** — Books processed sequentially. Use `concurrent.futures.ProcessPoolExecutor` for multi-book runs. *(extract_highlights.py — wrap `process_pair` calls in executor)*

- [ ] **Externalize CSS/templates** — CSS and HTML template embedded in Python strings. Move to external files or use a templating approach. *(extract_highlights_kfxlib.py — separate CSS file or Jinja2)*

- [ ] **Config file support** — Persistent preferences via `config.yaml` (output format, citation style, theme, etc.) to reduce repeated CLI flags. *(new config loading in extract_highlights.py)*

## Lower Priority but Valuable

- [ ] **JSON/CSV export** — For programmatic analysis or spreadsheet import. *(extract_highlights_kfxlib.py — add `generate_json()` and `generate_csv()` output functions)*

- [ ] **Chapter grouping in output** — Group highlights by TOC section/chapter instead of flat list. *(extract_highlights_kfxlib.py — restructure `items` list by section before rendering)*

- [x] **Dark mode CSS** — Automatic dark mode via `prefers-color-scheme` media query.

- [ ] **Deduplication** — Detect and merge overlapping highlights. *(extract_highlights_kfxlib.py — compare position ranges before appending)*

- [x] **Stats summary in output** — Highlight/note counts, section count, and date range in output header.

- [ ] **Auto-detect connected Kindle** — Scan for mounted Kindle device and copy files automatically. *(extract_highlights.py — platform-specific mount point detection)*
