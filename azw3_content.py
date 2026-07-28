#!/usr/bin/env python3
"""Resolve extract spans in an AZW3 book. Runs under `calibre-debug -e`.

The KFX path has `book_content.BookContent`, which can be imported directly.
AZW3 cannot: decoding needs Calibre's MOBI libraries, which only exist inside
Calibre's own Python. So this is a subprocess tool with a JSON contract,
matching how `extract_highlights.py` already shells out to
`extract_highlights_azw3.py`.

Usage (as invoked by vault_tools.readwise_extract):

    calibre-debug -e azw3_content.py -- <book.azw3> <requests.json>

`requests.json` is a list of {id, text, kind}. Results are written to stdout as
JSON: a list of {id, ok, start, end, text, images, reason}.

AZW3 content is HTML with real tags, unlike the KFX content stream's flat list
of text chunks. That means boundaries here are structural rather than
heuristic: a figure is an <img> plus the paragraph that follows it, a section
runs to the next chapter document, and a table is an actual <table> element.
"""

import json
import os
import re
import sys
from html import unescape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from extract_highlights_azw3 import (  # noqa: E402
    extract_flow0_content,
    strip_html_tags,
)

# Paragraph-level elements. Kindle books rarely use <h1>-<h6>; they style <p>
# instead, so <p> is the unit of structure that actually exists in practice.
BLOCK_RE = re.compile(rb"<(p|div|table|blockquote|ul|ol)\b[^>]*>", re.IGNORECASE)
IMG_RE = re.compile(rb"<img\b[^>]*>", re.IGNORECASE)
TABLE_RE = re.compile(rb"<table\b[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)
LIST_RE = re.compile(rb"<(ul|ol)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
PARA_RE = re.compile(rb"<p\b[^>]*>.*?</p>", re.IGNORECASE | re.DOTALL)
# Each chapter is its own <html> document inside Flow 0; these are the only
# reliable section boundaries, since class names differ from book to book.
DOC_RE = re.compile(rb"<html\b[^>]*>", re.IGNORECASE)
IMG_SRC_RE = re.compile(rb'src="kindle:embed:([0-9A-Za-z]+)', re.IGNORECASE)

# Refuse to emit a span larger than this; see readwise_extract.MAX_SPAN_CHARS.
MAX_SPAN_CHARS = 8000


def _norm(text):
    """Collapse to a comparison key: no tags, no whitespace, lowercase.

    Matches `readwise_extract._match_key` so that highlight text stored by
    Readwise lines up with text recovered from the book.
    """
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    return re.sub(r"\s+", "", text).lower()


def find_offset(html, needle_key, probe_len=120):
    """Byte offset of the highlight text within the book HTML.

    Readwise stores its own copy of the text, not a position we can trust, so
    the text itself is the anchor. The search runs over a tag-stripped
    projection of the document and maps the hit back to a byte offset.
    """
    if len(needle_key) < 8:
        return None

    # Build a stripped projection alongside a map back to byte offsets.
    stripped = []
    offsets = []
    in_tag = False
    for i, byte in enumerate(html):
        char = chr(byte)
        if char == "<":
            in_tag = True
            continue
        if char == ">":
            in_tag = False
            continue
        if in_tag or char.isspace():
            continue
        stripped.append(char.lower())
        offsets.append(i)
    projection = "".join(stripped)

    probe = needle_key[:probe_len]
    idx = projection.find(probe)
    if idx < 0 and len(probe) > 40:
        idx = projection.find(probe[:40])
    if idx < 0:
        return None
    return offsets[idx]


def block_bounds(html, offset):
    """(start, end) of the block-level element containing `offset`."""
    starts = [m.start() for m in BLOCK_RE.finditer(html)]
    start = 0
    for pos in starts:
        if pos <= offset:
            start = pos
        else:
            break
    end = len(html)
    for pos in starts:
        if pos > offset:
            end = pos
            break
    return start, end


def section_bounds(html, offset):
    """(start, end) of the chapter document containing `offset`."""
    starts = [m.start() for m in DOC_RE.finditer(html)] or [0]
    start = 0
    for pos in starts:
        if pos <= offset:
            start = pos
        else:
            break
    end = len(html)
    for pos in starts:
        if pos > offset:
            end = pos
            break
    return start, end


def images_in(html, start, end):
    """Kindle embed ids for images in a byte range."""
    found = []
    for m in IMG_RE.finditer(html[start:end]):
        src = IMG_SRC_RE.search(m.group(0))
        if src:
            found.append(src.group(1).decode("ascii", "replace"))
    return found


def preceding_image(html, offset, max_back=1500):
    """Image shortly before `offset` — how a caption finds its figure.

    AZW3 wraps the image in its own paragraph (`<p class="img"><img/></p>`)
    directly above the caption paragraph, so a short look-back is reliable.
    """
    window_start = max(0, offset - max_back)
    last = None
    for m in IMG_RE.finditer(html[window_start:offset]):
        last = m
    if not last:
        return None, None
    src = IMG_SRC_RE.search(last.group(0))
    return (src.group(1).decode("ascii", "replace") if src else None,
            window_start + last.start())


def resolve_one(html, req):
    """Resolve a single extract request against the book."""
    kind = req.get("kind")
    key = _norm(req.get("text") or "")
    offset = find_offset(html, key)
    if offset is None:
        return {"id": req["id"], "ok": False,
                "reason": "highlight text not found in book content"}

    images = []
    if kind == "figure":
        name, img_at = preceding_image(html, offset)
        start, end = block_bounds(html, offset)
        if name:
            images = [name]
            start = min(start, img_at)
            # Include the caption paragraph plus a following paragraph of
            # explanation, matching the KFX figure rule.
            nxt = PARA_RE.search(html, end)
            if nxt and nxt.start() - end < 400:
                end = nxt.end()
        else:
            images = images_in(html, start, end)
    elif kind == "table":
        # A real <table> element, not a heuristic run of chunks.
        table = None
        for m in TABLE_RE.finditer(html):
            if m.start() - 3000 <= offset <= m.end() + 3000:
                table = m
                break
        if table:
            start = min(table.start(), block_bounds(html, offset)[0])
            end = max(table.end(), block_bounds(html, offset)[1])
        else:
            start, end = block_bounds(html, offset)
    elif kind == "list":
        lst = None
        for m in LIST_RE.finditer(html):
            if m.start() - 2000 <= offset <= m.end() + 2000:
                lst = m
                break
        if lst:
            start = min(lst.start(), block_bounds(html, offset)[0])
            end = max(lst.end(), block_bounds(html, offset)[1])
        else:
            start, end = block_bounds(html, offset)
            nxt = PARA_RE.search(html, end)
            if nxt and nxt.start() - end < 200:
                end = nxt.end()
    elif kind == "section":
        start, end = section_bounds(html, offset)
    else:
        start, end = block_bounds(html, offset)
        nxt = PARA_RE.search(html, end)
        if nxt and nxt.start() - end < 200:
            end = nxt.end()

    text = strip_html_tags(html[start:end])
    if not images:
        images = images_in(html, start, end)

    result = {"id": req["id"], "ok": True, "start": start, "end": end,
              "text": text, "images": images}
    if len(text) > MAX_SPAN_CHARS:
        result["reason"] = ("span is %d chars (cap %d)"
                            % (len(text), MAX_SPAN_CHARS))
    elif kind == "figure" and images:
        result["reason"] = "figure with image %s" % images[0]
    elif kind == "table":
        result["reason"] = "table element" if "<table" in str(html[start:end][:200]) else "table span"
    else:
        result["reason"] = "%s: html block span" % (kind or "unclassified")
    return result


def main():
    if len(sys.argv) < 3:
        print("usage: azw3_content.py <book.azw3> <requests.json>",
              file=sys.stderr)
        return 2

    book_path, requests_path = sys.argv[1], sys.argv[2]
    with open(requests_path, "r") as f:
        requests = json.load(f)

    html = extract_flow0_content(book_path)

    results = []
    for req in requests:
        try:
            results.append(resolve_one(html, req))
        except Exception as exc:  # one bad request must not lose the batch
            results.append({"id": req.get("id"), "ok": False,
                            "reason": "resolve error: %s" % exc})

    json.dump(results, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
