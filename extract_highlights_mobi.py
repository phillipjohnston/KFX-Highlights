#!/usr/bin/env python3
"""Extract highlight text from a MOBI6 / AZW file using position data from annotation JSON.

This script runs under the standard .venv Python environment (no calibre-debug needed).
It uses KindleUnpack's MobiHeader + Sectionizer to decompress the MOBI6 rawML directly.

MOBI6 annotation positions (from .mbp1 files) are byte offsets into the decompressed
raw markup (rawML). The position string format is:

    "start_offset:spine_offset:total_length:base64_blob"

Only the first field (start_offset) is the byte offset we need. The base64 blob is a
Kindle CDEKey used for sync/DRM and is not needed for text extraction.
"""

import io
import json
import re
import sys
import os
from html import unescape


def extract_rawml(mobi_path):
    """Decompress and return the raw markup bytes from a MOBI6/.AZW file.

    Uses KindleUnpack's MobiHeader + Sectionizer. Returns bytes.
    Suppresses KindleUnpack's print() calls to keep stdout clean.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    kindleunpack_dir = os.path.join(script_dir, 'KindleUnpack')
    if kindleunpack_dir not in sys.path:
        sys.path.insert(0, kindleunpack_dir)

    from mobi_header import MobiHeader
    from mobi_sectioner import Sectionizer

    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        sect = Sectionizer(mobi_path)
        mh = MobiHeader(sect, 0)
        rawml = mh.getRawML()
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    return mh, rawml


def extract_metadata(mh):
    """Extract title, authors, and publication year from a parsed MobiHeader.

    Returns (title, authors_list, year_str).
    """
    # Suppress print output during metadata parsing
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        mh.parseMetaData()
        meta = mh.getMetaData()
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    # Title: prefer EXTH Updated_Title (503), fall back to PalmDB title
    title = ''
    if 'Updated_Title' in meta:
        title = meta['Updated_Title'][0]
    if not title:
        title = meta.get('Title', [''])[0]

    # Authors: EXTH record 100 is 'Creator'
    authors = meta.get('Creator', [])

    # Publication year: EXTH record 106 is 'Published' (ISO date string)
    year = ''
    published = meta.get('Published', [''])[0]
    if published:
        # Usually "YYYY-MM-DDTHH:MM:SS+00:00" or just "YYYY"
        m = re.match(r'(\d{4})', published)
        if m:
            year = m.group(1)

    return title, authors, year


def snap_to_tag_boundaries(all_html, start, end):
    """Expand a byte range to avoid cutting mid-tag.

    If start lands inside an HTML tag, move it forward past the '>'.
    If end lands inside an HTML tag, move it backward before the '<'.
    This ensures strip_html_tags() sees only complete tags.
    """
    i = start
    while i > 0:
        i -= 1
        if all_html[i:i+1] == b'>':
            break
        if all_html[i:i+1] == b'<':
            gt = all_html.find(b'>', start)
            if gt != -1:
                start = gt + 1
            break

    j = end
    while j > 0:
        j -= 1
        if all_html[j:j+1] == b'>':
            break
        if all_html[j:j+1] == b'<':
            end = j
            break

    return all_html[start:end]


def strip_html_tags(html_bytes):
    """Strip HTML tags from bytes, returning plain text.

    Handles tag removal and entity unescaping while preserving meaningful
    whitespace (paragraph/break boundaries become newlines).
    """
    text = html_bytes.decode('utf-8', errors='replace')

    # Clean up partial tag at the start
    if not text.startswith('<'):
        gt_pos = text.find('>')
        if gt_pos != -1:
            lt_pos = text.find('<')
            if lt_pos == -1 or lt_pos > gt_pos:
                text = text[gt_pos + 1:]

    # Clean up partial tag at the end
    last_lt = text.rfind('<')
    if last_lt != -1:
        last_gt = text.rfind('>')
        if last_gt < last_lt:
            text = text[:last_lt]

    text = re.sub(r'<br\s*/?\s*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</(p|div|h[1-6]|li|tr|blockquote)>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = unescape(text)
    lines = text.split('\n')
    lines = [' '.join(line.split()) for line in lines]
    text = '\n'.join(line for line in lines if line)
    return text.strip()


def build_page_map(all_html):
    """Build an estimated page map from <mbp:pagebreak> tags in the HTML.

    Returns a list of (byte_offset, page_label) tuples sorted by offset.
    """
    pages = []
    for m in re.finditer(rb'<mbp:pagebreak\s*/?\s*>', all_html):
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


def parse_position(pos_str):
    """Parse a MOBI6 annotation position string to an integer byte offset.

    MOBI6 positions from .mbp1 files use the format:
        "start_offset:spine_offset:total_length:base64_blob"

    The first field is the byte offset into the decompressed rawML.
    """
    s = str(pos_str)
    if ':' in s:
        return int(s.split(':')[0])
    return int(s)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract highlights from a MOBI6/.AZW book file")
    parser.add_argument("json_file", help="Path to annotations JSON file (from krds.py)")
    parser.add_argument("mobi_file", help="Path to MOBI6 or AZW book file")
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
        result = {"title": "", "authors": [], "year": "", "items": []}
        json.dump(result, sys.stdout, ensure_ascii=False)
        return

    # Decompress rawML and extract metadata
    mh, all_html = extract_rawml(args.mobi_file)
    title, authors, year = extract_metadata(mh)
    if args.title:
        title = args.title

    # Build page map from <mbp:pagebreak> tags
    pages = build_page_map(all_html)

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

    result = {
        "title": title,
        "authors": authors,
        "year": year,
        "items": items,
    }
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
