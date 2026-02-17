#!/usr/bin/env python3
"""Extract highlight text from an AZW3 file using position data from annotation JSON.

This script runs under Calibre's Python environment (via calibre-debug -e) to access
Calibre's MOBI decompression libraries and KindleUnpack's K8Processor for proper KF8
skeleton/fragment/FDST processing.

AZW3 annotation positions are byte offsets into the KF8 "Flow 0" content (the assembled
text after skeleton/fragment processing), NOT the raw decompressed HTML. We use
KindleUnpack's K8Processor.buildParts() to reconstruct this representation.
"""

import json
import re
import sys
import os
from html import unescape


def extract_flow0_content(azw3_path):
    """Extract KF8 Flow 0 content from an AZW3 file using KindleUnpack's K8Processor.

    Returns the assembled text (Flow 0) as bytes, which is what annotation positions reference.

    This uses KindleUnpack's skeleton/fragment/FDST processing to reconstruct the proper
    text representation, NOT just raw MOBI decompression.
    """
    # Add KindleUnpack to the path - add parent so relative imports work
    kindleunpack_root = '/Users/phillip/src/KindleUnpack'
    kindleunpack_lib = os.path.join(kindleunpack_root, 'lib')
    if kindleunpack_root not in sys.path:
        sys.path.insert(0, kindleunpack_root)

    # Import from the lib package
    from lib.mobi_header import MobiHeader
    from lib.mobi_sectioner import Sectionizer
    from lib.mobi_k8proc import K8Processor

    # Create a minimal "files" object that K8Processor expects
    class DummyFiles:
        def __init__(self):
            self.k8dir = '/tmp'

    # Suppress KindleUnpack's print statements (they go to stdout/stderr and break JSON output)
    import io
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()

    try:
        # Parse the AZW3 file
        sect = Sectionizer(azw3_path)
        mh = MobiHeader(sect, 0)

        # Check if this is a KF8 book
        if not mh.isK8():
            # Fall back to raw decompression for non-KF8 books
            rawML = mh.getRawML()
            return rawML

        # Extract raw markup
        rawML = mh.getRawML()

        # Process through K8Processor to get Flow 0
        files = DummyFiles()
        k8proc = K8Processor(mh, sect, files, debug=False)
        k8proc.buildParts(rawML)

        # Assemble the parts into Flow 0 content
        # (this is what annotation positions reference)
        assembled_text = b''.join(k8proc.parts)

        return assembled_text
    finally:
        # Restore stdout/stderr
        sys.stdout = old_stdout
        sys.stderr = old_stderr


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


def snap_to_tag_boundaries(all_html, start, end):
    """Expand a byte range to avoid cutting mid-tag.

    If start lands inside an HTML tag, move it forward past the '>'.
    If end lands inside an HTML tag, move it backward before the '<'.
    This ensures strip_html_tags() sees only complete tags.
    """
    # Check if start is inside a tag by scanning backward for '<' or '>'
    # If we hit '<' before '>' going backwards, we're inside a tag
    i = start
    while i > 0:
        i -= 1
        if all_html[i:i+1] == b'>':
            break  # start is outside a tag, no adjustment needed
        if all_html[i:i+1] == b'<':
            # We're inside a tag — advance start past the next '>'
            gt = all_html.find(b'>', start)
            if gt != -1:
                start = gt + 1
            break

    # Check if end is inside a tag by scanning backward from end
    j = end
    while j > 0:
        j -= 1
        if all_html[j:j+1] == b'>':
            break  # end is outside a tag
        if all_html[j:j+1] == b'<':
            # We're inside a tag — pull end back before the '<'
            end = j
            break

    return all_html[start:end]


def strip_html_tags(html_bytes):
    """Strip HTML tags from bytes, returning plain text.

    Handles tag removal and entity unescaping while preserving meaningful
    whitespace (paragraph/break boundaries become newlines).

    Since byte-offset slicing can land mid-tag (e.g. '<span clas' at the
    end or 'ss="foo">' at the start), we clean up partial tags at both
    boundaries before processing.
    """
    text = html_bytes.decode('utf-8', errors='replace')

    # Clean up partial tag at the start: if we start inside a tag,
    # everything up to the first '>' is tag debris
    if not text.startswith('<'):
        gt_pos = text.find('>')
        if gt_pos != -1:
            # Check if there's a '<' before this '>' — if not, it's a partial tag
            lt_pos = text.find('<')
            if lt_pos == -1 or lt_pos > gt_pos:
                text = text[gt_pos + 1:]

    # Clean up partial tag at the end: if we end inside a tag,
    # everything after the last '<' with no matching '>' is debris
    last_lt = text.rfind('<')
    if last_lt != -1:
        last_gt = text.rfind('>')
        if last_gt < last_lt:
            # Unclosed tag at the end — remove it
            text = text[:last_lt]

    # Replace block-level tags with newlines
    text = re.sub(r'<br\s*/?\s*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</(p|div|h[1-6]|li|tr|blockquote)>', '\n', text, flags=re.IGNORECASE)
    # Remove all remaining complete tags
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

    # Extract KF8 Flow 0 content
    all_html = extract_flow0_content(args.azw3_file)

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
        # Slice the decompressed HTML at byte offsets, then expand
        # to tag boundaries so we don't cut mid-tag
        highlight_html = snap_to_tag_boundaries(all_html, start, end)
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
