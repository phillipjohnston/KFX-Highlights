# AZW3 Annotation Position Mapping

## Problem

AZW3 (KF8 format) annotation positions in `.azw3r` files were not mapping correctly to highlight text when using simple HTML decompression. Positions were landing in the correct neighborhood but cutting into section headings or including extra content.

**Example issue:**
- Expected: "Most cookbooks ignore how unreliable recipes can be..."
- Actual extraction: "T DESIGNED MENUS\nMost cookbooks ignore how unreliable recipes can be..."

## Investigation

### What Positions Are NOT

Through testing, we determined that AZW3 annotation positions are **NOT**:

1. **Raw byte offsets into decompressed HTML** - Positions don't align with simple MOBI record decompression
2. **Character positions in plain text** - Stripping HTML tags and using character offsets produces completely different content
3. **UTF-16 code unit positions** - Converting to UTF-16 encoding doesn't resolve the offset issues
4. **Related to trailing entry handling** - Both KindleUnpack and Calibre strip trailing entries the same way

### What Positions Actually Are

According to [MobileRead Forums](https://www.mobileread.com/forums/showthread.php?t=321811):

> "KF8 (azw3) format appears to be the simplest case. The position is a decimal number giving an offset within the raw HTML content of the book, **as can be obtained using the kindleunpack software**."

Key insight: **"as can be obtained using kindleunpack"** - not just any decompression method.

### The KF8 Internal Structure

KF8 books have a complex internal structure beyond simple HTML:

- **Skeleton Index** (`skelidx`): Template structures for XHTML reconstruction
- **Fragment Index** (`fragidx`): Content fragments inserted into skeleton positions
- **FDST (Flow Data Section Table)** (`fdstidx`, `fdstcnt`): Separates content into "flows"
  - Flow 0: Main XHTML text body
  - Other flows: CSS, SVG, embedded resources

From our test file:
```
skelidx: 667
fdstidx: 1688
fdstcnt: 12
```

### How KindleUnpack Extracts "Raw HTML"

From [KindleUnpack's mobi_header.py](https://github.com/kevinhendricks/KindleUnpack/blob/master/lib/mobi_header.py):

```python
def getRawML():
    for i in range(1, self.records+1):
        data = trimTrailingDataEntries(self.sect.loadSection(self.start + i))
        dataList.append(self.unpack(data))
    return b''.join(dataList)
```

However, this raw concatenation is then **processed through skeleton/fragment/FDST** structures in [mobi_k8proc.py](https://github.com/kevinhendricks/KindleUnpack/blob/master/lib/mobi_k8proc.py) to reconstruct the actual XHTML that matches annotation positions.

### The Real Issue

**Annotation positions reference the text representation AFTER skeleton/fragment/FDST processing**, not the raw decompressed MOBI records.

Our current implementation (`extract_highlights_azw3.py`) does:
1. Decompress MOBI records ✓
2. Strip trailing entries ✓
3. Concatenate into "raw HTML" ✓
4. **MISSING: Process through KF8 skeleton/fragment/FDST system** ✗

This missing step is why positions are slightly off - they reference the reconstructed XHTML, not the raw concatenated HTML.

## Solution Path

To correctly extract AZW3 highlights, we need to:

1. Use Calibre's KF8 processing classes (not just `MobiReader`)
2. Process skeleton/fragment/FDST indices to reconstruct proper XHTML
3. Map annotation positions against this reconstructed content
4. Extract highlight text from the correct representation

Alternative approaches:
- Call KindleUnpack as an external tool
- Port KindleUnpack's K8Processor to work with our pipeline
- Use Calibre's higher-level conversion APIs that handle KF8 properly

## Related Concepts

### Kindle Location Numbers

From the same forum post:

> "A location is a reference an approximate location in a book. Each location represents 150 bytes of raw HTML in MOBI format. The other Kindle formats, such as KF8 (azw3) and KFX, contain a mapping table that shows where each location starts within their content."

This explains why location numbers (what Readwise displays) differ from the byte offset positions in annotation files - they use different coordinate systems but both reference the same underlying content representation.

## Files Examined

- Book: The 4-Hour Chef (ASIN: B005NJU8PA)
- Annotation file: `.azw3r` (309 highlights)
- Book file size (Calibre): 2,563,129 bytes (incorrect extraction)
- Book file size (actual Kindle): 2,694,690 bytes (still incorrect without KF8 processing)

## References

- [MobileRead: KF8 annotation positions](https://www.mobileread.com/forums/showthread.php?t=321811)
- [MobileRead Wiki: KF8 format](https://wiki.mobileread.com/wiki/KF8)
- [KindleUnpack GitHub](https://github.com/kevinhendricks/KindleUnpack)
- [KindleUnpack mobi_k8proc.py](https://github.com/kevinhendricks/KindleUnpack/blob/master/lib/mobi_k8proc.py)
- [KindleUnpack mobi_header.py](https://github.com/kevinhendricks/KindleUnpack/blob/master/lib/mobi_header.py)

## Date

2026-02-16
