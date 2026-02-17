# AZW3 Position Mapping Fix

## Date
2026-02-16

## Problem
AZW3 annotation positions were not mapping correctly to highlight text. Positions would land in the correct neighborhood but cut into section headings or include extra content.

**Example:**
- Expected: "Most cookbooks ignore how unreliable recipes can be..."
- Buggy output: "T DESIGNED MENUS\nMost cookbooks ignore how unreliable recipes can be..."

## Root Cause
AZW3 (KF8 format) annotation positions reference the text **after skeleton/fragment/FDST processing**, not the raw decompressed MOBI HTML. Our original implementation only did basic MOBI record decompression without the KF8 reconstruction step.

## Solution
Integrated KindleUnpack's `K8Processor` to properly reconstruct KF8 "Flow 0" content (the main text body after skeleton/fragment/FDST processing). This is the text representation that annotation byte offsets actually reference.

### Changes Made

1. **Updated `extract_highlights_azw3.py`**:
   - Replaced `decompress_azw3()` with `extract_flow0_content()`
   - Added KindleUnpack library imports (`K8Processor`, `MobiHeader`, `Sectionizer`)
   - Process raw MOBI markup through `K8Processor.buildParts()` to get assembled Flow 0 text
   - Suppress KindleUnpack's print statements to avoid breaking JSON output

2. **Dependencies**:
   - Requires KindleUnpack cloned at `/Users/phillip/src/KindleUnpack`
   - Uses KindleUnpack's lib package for K8 processing

3. **Documentation**:
   - Updated CLAUDE.md to document KindleUnpack dependency
   - Updated format descriptions to clarify "Flow 0" vs "raw HTML"

## Verification
Tested with "The 4-Hour Chef" (ASIN B005NJU8PA, 309 highlights). Previously problematic highlights now extract cleanly without section heading fragments.

## References
- [docs/lessons/azw3-position-mapping.md](./azw3-position-mapping.md) - Detailed investigation notes
- [MobileRead Forums: KF8 annotation positions](https://www.mobileread.com/forums/showthread.php?t=321811)
- [KindleUnpack mobi_k8proc.py](https://github.com/kevinhendricks/KindleUnpack/blob/master/lib/mobi_k8proc.py)
