# Extract Shared Formatter Module

## Problem

`_format_azw3_output()` in `extract_highlights.py` duplicates ~150 lines of HTML/Markdown/JSON/CSV formatting logic from `extract_highlights_kfxlib.py`. The duplication exists because `extract_highlights_kfxlib.py` has kfxlib imports at module level, so it can't be imported from the standard venv.

## Proposed Solution

Extract the formatting functions (`generate_html`, `generate_markdown`, `generate_json`, `generate_csv`, and helpers like `_format_citation_html`, `_compute_stats`, `_format_stats_line`, `_load_css`, `clean_title`) into a shared `highlight_formatter.py` module with no kfxlib dependencies. Both `extract_highlights_kfxlib.py` and `_format_azw3_output()` would import from it.

This would also let the AZW3 output include citation lines and stats summaries, which the current inline formatting omits.
