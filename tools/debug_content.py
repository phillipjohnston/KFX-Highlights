#!/usr/bin/env python3
"""Debug script to examine KFX content structure."""
import json
import sys
from pathlib import Path

# Allow importing kfxlib
base_dir = Path(__file__).parent
extracted = base_dir / "kfxlib_extracted"
if extracted.exists():
    sys.path.insert(0, str(extracted))
else:
    sys.path.insert(0, str(base_dir / "KFX Input.zip"))

from extract_highlights_kfxlib import load_content_sections

# Find any KFX file in output directory to test
output_dir = base_dir / "output"
kfx_files = list(output_dir.glob("*.kfx"))

if not kfx_files:
    print("No KFX files found in output/")
    sys.exit(1)

kfx_path = kfx_files[0]
print(f"Examining: {kfx_path.name}")

sections = load_content_sections(str(kfx_path))

# Look at the first few sections to see their structure
for i, sec in enumerate(sections[:5]):
    print(f"\n=== Section {i} ===")
    print(f"Position: {sec['position']}")
    print(f"Length: {sec['length']}")
    print(f"Content preview (first 200 chars): {repr(sec['content'][:200])}")
