#!/usr/bin/env python3
"""Debug script to examine newline handling in KFX content extraction."""
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

from extract_highlights_kfxlib import load_content_sections, extract_text

kfx_path = "/Users/phillip/Documents/Calibre Library/Ming-Dao Deng/365 Tao (13957)/365 Tao - Ming-Dao Deng.kfx"

# Load sections
sections = load_content_sections(kfx_path)

# The problematic highlight spans 241469 to 241553
start = 241469
end = 241553

# Find which sections contain this range
print(f"Looking for content between positions {start} and {end}")
print("="*70)

for i, sec in enumerate(sections):
    sec_start = sec["position"]
    sec_end = sec_start + sec["length"]

    # Check if this section overlaps with our range
    if sec_start <= end and sec_end > start:
        print(f"\nSection {i}:")
        print(f"  Position: {sec_start} to {sec_end}")
        print(f"  Length: {sec['length']}")

        # Show the part that overlaps
        slice_start = max(start, sec_start)
        slice_end = min(end + 1, sec_end)
        a = slice_start - sec_start
        b = slice_end - sec_start
        content_slice = sec["content"][a:b]

        print(f"  Slice [{a}:{b}]")
        print(f"  Content: {repr(content_slice)}")
        print(f"  Raw bytes: {content_slice.encode('unicode_escape').decode('ascii')}")

# Now use the extract_text function to see what it produces
print("\n" + "="*70)
print("Result from extract_text():")
extracted = extract_text(sections, start, end)
print(f"  Text: {repr(extracted)}")
print(f"  Raw bytes: {extracted.encode('unicode_escape').decode('ascii')}")
