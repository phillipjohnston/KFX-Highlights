#!/usr/bin/env python3
"""Extract highlight text from an AZW3 file using position data from annotation JSON.

This script runs under Calibre's Python environment (via calibre-debug -e) to access
Calibre's MOBI decompression libraries. It decompresses the KF8 HTML from the AZW3
container, maps byte-offset annotation positions onto the decompressed content, and
outputs intermediate JSON to stdout for the orchestrator to format.

AZW3 annotation positions are raw byte offsets into the decompressed HTML, unlike KFX
positions which use a "prefix:offset" format.
"""

import json
import re
import sys
from html import unescape


def decompress_azw3(azw3_path):
    """Decompress all text records from an AZW3 file.

    Returns the concatenated decompressed HTML as bytes.

    Uses Calibre's MobiReader for header parsing and HuffReader/PalmDoc
    for decompression depending on the compression type.
    """
    from calibre.ebooks.mobi.reader.mobi6 import MobiReader

    reader = MobiReader(azw3_path)
    bh = reader.book_header

    # Build decompressor based on compression type
    if bh.compression_type == b'DH':  # HUFF/CDIC
        from calibre.ebooks.mobi.huffcdic import HuffReader
        huff_sections = [reader.sections[bh.huff_offset + i][0]
                         for i in range(bh.huff_number)]
        huffr = HuffReader(huff_sections)
        decompress = huffr.unpack
    elif bh.compression_type == 2:  # PalmDoc LZ77
        from calibre.ebooks.compression.palmdoc import decompress as palmdoc_decompress
        decompress = palmdoc_decompress
    else:  # No compression (type 1)
        decompress = lambda x: x

    # Concatenate decompressed text records
    parts = []
    for i in range(1, bh.records + 1):
        rec = reader.sections[i][0]
        trail = reader.sizeof_trailing_entries(rec)
        if trail:
            rec = rec[:-trail]
        parts.append(decompress(rec))

    return b''.join(parts)


def extract_metadata(azw3_path):
    """Extract title and authors from the MOBI EXTH header.

    Returns (title, authors_list, year).
    """
    from calibre.ebooks.mobi.reader.mobi6 import MobiReader

    reader = MobiReader(azw3_path)
    bh = reader.book_header

    title = getattr(bh, 'title', None) or ''
    if not title:
        # Fall back to the book_title from EXTH
        exth = getattr(bh, 'exth', None) or {}
        title = exth.get(503, b'').decode('utf-8', errors='replace') if isinstance(exth.get(503), bytes) else str(exth.get(503, ''))

    # Authors from EXTH record 100
    authors = []
    exth = getattr(bh.exth, 'items', None)
    if exth is None:
        # Try the exth_flag / raw approach
        try:
            from calibre.ebooks.mobi.reader.headers import MetaInformation
            mi = reader.create_opf('').to_book_metadata()
            title = mi.title or title
            authors = list(mi.authors) if mi.authors else []
        except Exception:
            pass
    else:
        for rec_type, val in exth:
            if rec_type == 100:  # author
                if isinstance(val, bytes):
                    authors.append(val.decode('utf-8', errors='replace'))
                else:
                    authors.append(str(val))

    # Publication date from EXTH record 106
    year = ''
    try:
        mi = reader.create_opf('').to_book_metadata()
        title = mi.title or title
        if mi.authors:
            authors = list(mi.authors)
        if hasattr(mi, 'pubdate') and mi.pubdate:
            year = str(mi.pubdate.year)
    except Exception:
        pass

    return title, authors, year


def strip_html_tags(html_bytes):
    """Strip HTML tags from bytes, returning plain text.

    Handles tag removal and entity unescaping while preserving meaningful
    whitespace (paragraph/break boundaries become newlines).
    """
    text = html_bytes.decode('utf-8', errors='replace')
    # Replace block-level tags with newlines
    text = re.sub(r'<br\s*/?\s*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</(p|div|h[1-6]|li|tr|blockquote)>', '\n', text, flags=re.IGNORECASE)
    # Remove all remaining tags
    text = re.sub(r'<[^>]+>', '', text)
    # Unescape HTML entities
    text = unescape(text)
    # Normalize whitespace within lines but preserve line breaks
    lines = text.split('\n')
    lines = [' '.join(line.split()) for line in lines]
    text = '\n'.join(line for line in lines if line)
    return text.strip()


def build_page_map(all_html):
    """Build an estimated page map from <mbp:pagebreak> tags in the HTML.

    Returns a list of (byte_offset, page_label) tuples sorted by offset.
    Page numbers are sequential estimates based on pagebreak tag positions,
    not necessarily matching the physical book's pagination.
    """
    pages = []
    # Look for Kindle page break markers
    for m in re.finditer(rb'<mbp:pagebreak\s*/?\s*>', all_html):
        # Page number is typically not in the tag itself; use sequential numbering
        pages.append(m.start())

    if not pages:
        return []

    return [(offset, str(i + 1)) for i, offset in enumerate(pages)]


def page_for_offset(pages, offset):
    """Find the page label for a given byte offset."""
    label = None
    for page_offset, page_label in pages:
        if page_offset <= offset:
            label = page_label
        else:
            break
    return label


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract highlights from AZW3 book (runs under calibre-debug -e)")
    parser.add_argument("json_file", help="Path to annotations JSON file")
    parser.add_argument("azw3_file", help="Path to AZW3 book file")
    parser.add_argument("--title", type=str, default=None,
                        help="Override the book title")
    args = parser.parse_args()

    # Load annotations
    with open(args.json_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    ann_obj = data.get("annotation.cache.object", {})
    annotations = ann_obj.get("annotation.personal.highlight", [])
    notes = ann_obj.get("annotation.personal.note", [])

    if not annotations and not notes:
        # Output empty result
        result = {"title": "", "authors": [], "year": "", "items": []}
        json.dump(result, sys.stdout, ensure_ascii=False)
        return

    # Decompress book content
    all_html = decompress_azw3(args.azw3_file)

    # Extract metadata
    title, authors, year = extract_metadata(args.azw3_file)
    if args.title:
        title = args.title

    # Build page map
    pages = build_page_map(all_html)

    # Parse annotation positions
    # AZW3 positions are plain integers (byte offsets), not "prefix:offset"
    def parse_position(pos_str):
        """Parse an annotation position string to an integer byte offset."""
        if ':' in str(pos_str):
            return int(str(pos_str).split(':')[1])
        return int(pos_str)

    # Build notes lookup by end position
    notes_by_end = {}
    for n in notes:
        pos = parse_position(n["endPosition"])
        notes_by_end.setdefault(pos, []).append(n["note"])

    # Sort annotations by start position
    annotations.sort(key=lambda a: parse_position(a["startPosition"]))

    # Deduplicate overlapping highlights
    deduped = []
    for ann in annotations:
        s = parse_position(ann["startPosition"])
        e = parse_position(ann["endPosition"])
        if deduped:
            ps, pe, _ = deduped[-1]
            if s >= ps and e <= pe:
                continue  # fully contained, skip
            if ps >= s and pe <= e:
                deduped[-1] = (s, e, ann)
                continue
        deduped.append((s, e, ann))

    # Extract highlight text
    items = []
    for start, end, ann in deduped:
        # Slice the decompressed HTML at byte offsets
        highlight_html = all_html[start:end]
        text = strip_html_tags(highlight_html)

        if not text:
            continue

        page = page_for_offset(pages, start) if pages else None

        items.append({
            "creationTime": ann.get("creationTime", ""),
            "text": text,
            "page": page,
            "location": start,
            "section": None,
            "chapter": None,
            "type": "highlight",
        })

        # Attach any notes at this highlight's end position
        for note_text in notes_by_end.get(end, []):
            items.append({
                "creationTime": "",
                "text": note_text,
                "page": page,
                "location": start,
                "section": None,
                "chapter": None,
                "type": "note",
            })

    # Output as JSON to stdout for the orchestrator
    result = {
        "title": title,
        "authors": authors,
        "year": year,
        "items": items,
    }
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
