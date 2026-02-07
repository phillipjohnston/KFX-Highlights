# KFX-Highlights Improvement Checklist

Improvement opportunities identified for the KFX highlight extraction pipeline.

## High-Impact Workflow Improvements

- [ ] **`--skip-existing` flag** — Bulk mode reprocesses everything. Add an option to skip books whose output HTML already exists. *(extract_highlights.py — check for output file before calling `process_pair`)*

- [ ] **Markdown export (`--format md`)** — HTML-only limits usefulness. Markdown integrates with Obsidian, Notion, Bear, etc. *(extract_highlights_kfxlib.py — add `generate_markdown()` alongside `generate_html()`, wire format flag through from orchestrator)*

- [ ] **Better title handling** — When KFX metadata lacks a clean title, the output uses raw filenames with ISBNs, order IDs, and email fragments. Add `--title` override and smarter title cleanup. *(extract_highlights_kfxlib.py:337 — regex to strip ISBN, order ID, email patterns from `kfx_path.stem`)*

- [ ] **Intermediate file cleanup** — JSON files from `krds.py` accumulate in `output/` with no cleanup. Add `--keep-json` flag (default: delete after successful HTML generation). *(extract_highlights.py — delete JSON after `extract_highlights_kfxlib.py` succeeds)*

- [ ] **Progress feedback** — Processing is silent for several seconds per book. Add `[1/N] Processing "Book Title"...` style output and suppress per-highlight console spam. *(extract_highlights.py already has bulk progress; extract_highlights_kfxlib.py:298-307 — add `--quiet` flag)*

## Medium-Impact Enhancements

- [ ] **`--quiet` / `--verbose` flags** — With 200+ highlights, console output is overwhelming. Quiet mode shows summary only; verbose mode shows debug info. *(extract_highlights_kfxlib.py — gate print statements on verbosity level; pass through from orchestrator)*

- [ ] **Better citation handling** — When publication year is missing, citation shows `"(). "`. Gracefully omit empty fields. *(extract_highlights_kfxlib.py:201-205 — conditional formatting)*

- [ ] **Preserve paragraph structure** — All newlines replaced with spaces, collapsing multi-paragraph highlights into walls of text. Add option to preserve paragraph breaks. *(extract_highlights_kfxlib.py:62 — replace `\n` with `<br/>` in HTML or double-newline in Markdown)*

- [ ] **Highlight color support** — All highlights rendered as yellow. Kindle supports multiple colors for categorization. Preserve the original color. *(krds.py annotation data may include color info; extract_highlights_kfxlib.py:229 — use actual color)*

- [ ] **`requirements.txt`** — Dependencies only listed in docs. Add a proper requirements file. *(project root)*

## Optimization Opportunities

- [ ] **Parallel bulk processing** — Books processed sequentially. Use `concurrent.futures.ProcessPoolExecutor` for multi-book runs. *(extract_highlights.py — wrap `process_pair` calls in executor)*

- [ ] **Externalize CSS/templates** — 65 lines of CSS and HTML template embedded in Python strings. Move to external files or use a templating approach. *(extract_highlights_kfxlib.py:118-207 — separate CSS file or Jinja2)*

- [ ] **Config file support** — Persistent preferences via `config.yaml` (output format, citation style, theme, etc.) to reduce repeated CLI flags. *(new config loading in extract_highlights.py)*

## Lower Priority but Valuable

- [ ] **JSON/CSV export** — For programmatic analysis or spreadsheet import. *(extract_highlights_kfxlib.py — add `generate_json()` and `generate_csv()` output functions)*

- [ ] **Chapter grouping in output** — Group highlights by TOC section/chapter instead of flat list. *(extract_highlights_kfxlib.py — restructure `items` list by section before rendering)*

- [ ] **Dark mode CSS** — A dark theme option for the HTML output. *(extract_highlights_kfxlib.py — add alternate CSS block, toggle via flag or `prefers-color-scheme` media query)*

- [ ] **Deduplication** — Detect and merge overlapping highlights. *(extract_highlights_kfxlib.py — compare position ranges before appending)*

- [ ] **Stats summary in output** — Total highlight count, date range, most-highlighted chapters. *(extract_highlights_kfxlib.py — compute stats, add summary section to HTML/Markdown header)*

- [ ] **Auto-detect connected Kindle** — Scan for mounted Kindle device and copy files automatically. *(extract_highlights.py — platform-specific mount point detection)*
