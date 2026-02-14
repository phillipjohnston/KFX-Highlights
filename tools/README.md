# Debug Tools

This directory contains debugging and diagnostic scripts for troubleshooting KFX highlight extraction issues.

## Scripts

### debug_content.py

Examines the internal structure of KFX content sections.

**Usage:**
```bash
python tools/debug_content.py
```

Displays the first few content sections from a KFX file, showing position, length, and content preview. Useful for understanding how kfxlib organizes book content and debugging position-based extraction issues.

### debug_newlines.py

Analyzes how newlines are handled during text extraction from KFX content sections.

**Usage:**
```bash
python tools/debug_newlines.py
```

Examines a specific highlight from the 365 Tao book (positions 241469-241553) to show:
- Which content sections contain the highlight
- How each section's content is stored
- The final extracted text with newline encoding

Useful for debugging line break preservation issues and understanding the relationship between KFX content sections and rendered text.

## Requirements

Both scripts require the same environment as the main extraction pipeline:
- Python virtual environment activated (`.venv`)
- kfxlib loaded from `KFX Input.zip` or `kfxlib_extracted/`
- Access to KFX files (either in the repository or via paths in `.sync_state.json`)
