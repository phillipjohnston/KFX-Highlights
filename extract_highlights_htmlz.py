#!/usr/bin/env python3
"""Extract highlight text from an HTMLZ file using position data from annotation JSON.

This script handles AZW1 (Topaz/TPZ) books that Calibre has converted to HTMLZ format.
Annotation positions from .tal/.tas files are plain integer byte offsets into book.html
inside the HTMLZ zip.

HTMLZ is a zip archive containing book.html, CSS, and images. Calibre embeds page break
markers as <div id="pageNNNN"> elements which we use to build a page map.
"""

import argparse
import json
import re
import sys
import zipfile
from html import unescape
from pathlib import Path


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


def extract_metadata_from_html(html_bytes):
    """Extract title, author, and year from HTML <meta> tags.

    Calibre's HTMLZ output embeds metadata in the <head> as:
      <meta name="Title" content="...">
      <meta name="Author" content="...">
      <meta name="DC.Date.issued" content="YYYY-..."> (or similar)

    Returns (title, authors_list, year).
    """
    html_text = html_bytes.decode('utf-8', errors='replace')

    title = ''
    authors = []
    year = ''

    # Extract meta tags from the head
    # Match <meta name="..." content="..."> (case-insensitive, attribute order-agnostic)
    meta_pattern = re.compile(
        r'<meta\s+(?:[^>]*?\s+)?name=["\']([^"\']+)["\'][^>]*?content=["\']([^"\']*)["\']'
        r'|<meta\s+(?:[^>]*?\s+)?content=["\']([^"\']*)["\'][^>]*?name=["\']([^"\']+)["\']',
        re.IGNORECASE | re.DOTALL,
    )
    for m in meta_pattern.finditer(html_text):
        if m.group(1):
            name, content = m.group(1), m.group(2)
        else:
            name, content = m.group(4), m.group(3)

        name_lower = name.lower()
        if name_lower in ('title', 'dc.title') and not title:
            title = content.strip()
        elif name_lower in ('author', 'dc.creator', 'creator') and content.strip():
            authors.append(content.strip())
        elif name_lower in ('date', 'dc.date', 'dc.date.issued', 'pubdate') and not year:
            # Extract year from dates like "2001-01-01" or "2001"
            m_year = re.match(r'(\d{4})', content.strip())
            if m_year:
                year = m_year.group(1)

    # Fallback: try <title> tag if no meta title
    if not title:
        m_title = re.search(r'<title[^>]*>([^<]+)</title>', html_text, re.IGNORECASE)
        if m_title:
            title = m_title.group(1).strip()

    return title, authors, year


def build_page_map(html_bytes):
    """Build a page map from <div id="pageNNNN"> markers in the HTML.

    Calibre embeds these when converting Topaz books to HTMLZ. Returns a list
    of (byte_offset, page_label) tuples sorted by offset.
    """
    pages = []
    # Match id="page0110", id="page110", etc. — Calibre zero-pads to 4 digits
    for m in re.finditer(rb'<[^>]+\bid=["\']page(\d+)["\'][^>]*>', html_bytes):
        page_num = m.group(1).decode('ascii').lstrip('0') or '0'
        pages.append((m.start(), page_num))

    pages.sort(key=lambda x: x[0])
    return pages


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
    parser = argparse.ArgumentParser(
        description="Extract highlights from HTMLZ book (AZW1/Topaz via Calibre conversion)")
    parser.add_argument("json_file", help="Path to annotations JSON file (from krds.py)")
    parser.add_argument("htmlz_file", help="Path to HTMLZ book file")
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

    # Read book.html from the HTMLZ zip
    try:
        with zipfile.ZipFile(args.htmlz_file, 'r') as z:
            html_bytes = z.read("book.html")
    except (zipfile.BadZipFile, KeyError) as e:
        print(f"Error: could not read book.html from {args.htmlz_file}: {e}",
              file=sys.stderr)
        sys.exit(1)

    # Extract metadata from HTML meta tags
    title, authors, year = extract_metadata_from_html(html_bytes)
    if args.title:
        title = args.title

    # Build page map from <div id="pageNNNN"> markers
    pages = build_page_map(html_bytes)

    # Parse annotation positions — TAL positions are plain integers
    def parse_position(pos_str):
        pos_str = str(pos_str)
        if ':' in pos_str:
            return int(pos_str.split(':')[1])
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
    html_len = len(html_bytes)
    items = []
    for start, end, ann in deduped:
        if start >= html_len or end > html_len:
            print(f"Warning: position {start}:{end} out of range "
                  f"(book.html is {html_len} bytes) — skipping",
                  file=sys.stderr)
            continue

        highlight_html = snap_to_tag_boundaries(html_bytes, start, end)
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

    # Output JSON to stdout for the orchestrator
    result = {
        "title": title,
        "authors": authors,
        "year": year,
        "items": items,
    }
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
