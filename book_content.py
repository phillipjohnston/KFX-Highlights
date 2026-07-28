#!/usr/bin/env python3
"""Importable access to KFX book content, navigation, and embedded images.

`extract_highlights_kfxlib.py` decodes a book three separate times (once each for
content, navigation, and metadata) and discards image chunks. That is fine for a
one-shot CLI run, but the extract pipeline queries the same book repeatedly, and
it needs the figures.

`BookContent` decodes once and keeps everything:

    book = BookContent(kfx_path)
    book.text_range(start, end)          # plain text for a position range
    book.chunks_range(start, end)        # text AND image chunks, in order
    book.toc_node_at(pid)                # innermost TOC node covering a position
    book.section_bounds(pid)             # (start, end) of that section
    book.image_at(pid)                   # embedded image as PNG/JPEG bytes

Differences from the CLI extractor, all deliberate:

1. Image chunks (``type == 2``) are retained. Besides making figures reachable,
   this fixes a latent slicing bug: chunk length is inferred from the *next*
   chunk's position, so dropping a 1-PID image inflated the preceding text
   chunk's length and skewed extraction near figures.
2. The book is decoded once and shared.
3. The TOC is flattened across all depths before computing section bounds. KFX
   navigation nests children inside top-level nodes and does not sort them, so a
   naive next-sibling walk over the top level would skip a section's own
   children and overstate its extent.
4. TOC positions of 0 are treated as failed lookups rather than real positions
   (they come from ``eid_to_pid.get(eid, 0)``, which silently defaults), and the
   final node is clamped to the end of the book.
"""

import sys
from pathlib import Path

# Allow importing kfxlib either from the extracted folder or the bundled zip
_base_dir = Path(__file__).parent
_extracted = _base_dir / "kfxlib_extracted"
if str(_extracted) not in sys.path and str(_base_dir / "KFX Input.zip") not in sys.path:
    sys.path.insert(0, str(_extracted) if _extracted.exists()
                    else str(_base_dir / "KFX Input.zip"))

import json  # noqa: E402

from kfxlib import yj_book  # noqa: E402
from kfxlib.ion import IonAnnotation, IonSymbol  # noqa: E402
from kfxlib.yj_container import YJFragment  # noqa: E402
from kfxlib import resources as _resources  # noqa: E402

TEXT = 1
IMAGE = 2

# Ion field symbols used by the navigation and resource fragments.
_NAV_CONTAINERS = "$392"
_NAV_TYPE = "$235"
_NAV_PAGE_LIST = "$237"
_NAV_TOC = "$212"
_NAV_ENTRIES = "$247"
_NAV_TARGET = "$246"
_NAV_LABEL_OBJ = "$241"
_NAV_LABEL = "$244"
_NAV_EID = "$155"
_NAV_OFFSET = "$143"

_RES_MIME = "$162"
_RES_LOCATION = "$165"
_RES_WIDTH = "$422"
_RES_HEIGHT = "$423"
_RES_FRAGMENT = "$164"
_RES_DATA = "$417"


def _unwrap(obj):
    """Unwrap IonAnnotation wrappers to get the underlying value."""
    while isinstance(obj, IonAnnotation):
        obj = obj.value
    return obj


def _plain(value):
    """Reduce an Ion value to a plain Python dict/list where possible."""
    value = _unwrap(value)
    if isinstance(value, YJFragment):
        return _plain(value.value)
    return value


class BookContent:
    """Decoded KFX book: positioned content chunks, navigation, and images."""

    def __init__(self, kfx_path):
        self.path = Path(kfx_path)
        self._book = yj_book.YJ_Book(str(kfx_path))
        # convert_to_json_content() calls decode_book() internally — that is the
        # single decode this class is built around, and it leaves fragments
        # (navigation, resources, metadata) available on the same instance.
        # Do NOT decode again: decode_book() appends to self.fragments without
        # clearing it, so a second call duplicates every fragment and makes
        # single-fragment lookups ambiguous.
        content_json = json.loads(
            self._book.convert_to_json_content().decode("utf-8"))

        # Keep BOTH text and image chunks so that inferred lengths stay correct
        # and figures remain reachable.
        chunks = [e for e in content_json.get("data", [])
                  if e.get("type") in (TEXT, IMAGE)]
        chunks.sort(key=lambda x: x["position"])
        for i, chunk in enumerate(chunks[:-1]):
            chunk["length"] = chunks[i + 1]["position"] - chunk["position"]
        if chunks:
            last = chunks[-1]
            last["length"] = len(last["content"]) if last["type"] == TEXT else 1
        self.chunks = chunks

        self._metadata = None
        self._pages = None
        self._toc = None
        self._flat_toc = None

    # -- metadata ---------------------------------------------------------

    @property
    def metadata(self):
        """Book metadata, decoded separately and only when asked for.

        Serializing content mutates the shared symbol table, after which
        get_metadata()'s strict fragment lookup reports `$258` as ambiguous even
        though only one such fragment exists. Metadata is needed at most once per
        book, so it gets its own short-lived YJ_Book; content, navigation, and
        images still come from the single decode in __init__.
        """
        if self._metadata is None:
            self._metadata = yj_book.YJ_Book(str(self.path)).get_metadata()
        return self._metadata

    @property
    def title(self):
        return self.metadata.title or self.path.stem

    @property
    def authors(self):
        return self.metadata.authors or []

    @property
    def end_position(self):
        """One past the last addressable position in the book."""
        if not self.chunks:
            return 0
        last = self.chunks[-1]
        return last["position"] + last.get("length", 1)

    # -- content ----------------------------------------------------------

    def _first_index_at(self, position):
        """Index of the last chunk starting at or before `position`."""
        idx = 0
        while (idx < len(self.chunks) - 1
               and self.chunks[idx + 1]["position"] <= position):
            idx += 1
        return idx

    def chunks_range(self, start, end):
        """Return chunks overlapping [start, end], in reading order.

        Text chunks are sliced to the requested range; image chunks are returned
        whole. Each result is a dict with `type`, `position`, and `content`
        (the text, or the image fragment name for images).
        """
        results = []
        idx = self._first_index_at(start)
        while idx < len(self.chunks) and self.chunks[idx]["position"] <= end:
            chunk = self.chunks[idx]
            c_start = chunk["position"]
            if chunk["type"] == IMAGE:
                if c_start >= start:
                    results.append({"type": IMAGE, "position": c_start,
                                    "content": chunk["content"]})
            else:
                c_end = c_start + chunk.get("length", len(chunk["content"]))
                slice_start = max(start, c_start)
                slice_end = min(end + 1, c_end)
                if slice_end > slice_start:
                    text = chunk["content"][slice_start - c_start:slice_end - c_start]
                    if text:
                        results.append({"type": TEXT, "position": c_start,
                                        "content": text})
            idx += 1
        return results

    def text_range(self, start, end):
        """Extract plain text between two positions (images omitted)."""
        parts = [c["content"] for c in self.chunks_range(start, end)
                 if c["type"] == TEXT]
        return "\n".join(parts).strip()

    # -- navigation -------------------------------------------------------

    def _load_navigation(self):
        """Parse the KFX navigation fragment into page and TOC lists."""
        self._pages, self._toc = [], []
        nav = self._book.fragments.get("$389", first=True)
        if nav is None:
            # Send-to-Kindle books frequently have no navigation at all.
            return

        pos_info = self._book.collect_content_position_info()
        eid_to_pid = {}
        for chunk in pos_info:
            if chunk.eid not in eid_to_pid:
                eid_to_pid[chunk.eid] = chunk.pid - chunk.eid_offset

        def target_pid(entry):
            """Resolve a nav entry's position, or None when unresolvable."""
            loc = _plain(entry[_NAV_TARGET])
            eid = loc[_NAV_EID]
            if eid not in eid_to_pid:
                return None
            return eid_to_pid[eid] + loc.get(_NAV_OFFSET, 0)

        def label_of(entry):
            obj = _plain(entry.get(_NAV_LABEL_OBJ, {}))
            return obj.get(_NAV_LABEL, "") if isinstance(obj, dict) else ""

        for container in _plain(nav.value[0]).get(_NAV_CONTAINERS, []):
            if isinstance(container, IonSymbol):
                container = self._book.fragments.get(ftype="$391", fid=container)
            data = _plain(container)
            kind = data.get(_NAV_TYPE)

            if kind == _NAV_PAGE_LIST:
                for entry in data.get(_NAV_ENTRIES, []):
                    entry = _plain(entry)
                    pid = target_pid(entry)
                    label = label_of(entry)
                    if pid is not None and label:
                        self._pages.append((pid, label))
                self._pages.sort(key=lambda x: x[0])

            elif kind == _NAV_TOC:
                def build(entries):
                    nodes = []
                    for entry in entries:
                        entry = _plain(entry)
                        nodes.append({
                            "label": label_of(entry),
                            "pid": target_pid(entry),
                            "children": build(entry.get(_NAV_ENTRIES, [])),
                        })
                    return nodes
                self._toc = build(data.get(_NAV_ENTRIES, []))

    @property
    def pages(self):
        if self._pages is None:
            self._load_navigation()
        return self._pages

    @property
    def toc(self):
        if self._toc is None:
            self._load_navigation()
        return self._toc

    @property
    def flat_toc(self):
        """TOC nodes at every depth, sorted by position.

        Flattening is what makes section bounds correct: the next node in this
        list is the true end of the current section, whatever its depth.
        """
        if self._flat_toc is None:
            flat = []

            def walk(nodes, depth):
                for node in nodes:
                    if node["pid"] is not None:
                        flat.append({"label": node["label"], "pid": node["pid"],
                                     "depth": depth})
                    walk(node["children"], depth + 1)

            walk(self.toc, 0)
            flat.sort(key=lambda n: n["pid"])
            self._flat_toc = flat
        return self._flat_toc

    def page_at(self, pid):
        """Page label covering a position, if the book has a page list."""
        label = None
        for page_pid, page_label in self.pages:
            if page_pid <= pid:
                label = page_label
            else:
                break
        return label

    def toc_node_at(self, pid):
        """Innermost TOC node covering `pid`, or None."""
        found = None
        for node in self.flat_toc:
            if node["pid"] <= pid:
                found = node
            else:
                break
        return found

    def section_bounds(self, pid):
        """(start, end) of the TOC section containing `pid`, or None.

        The end is the next flattened node's position (any depth), clamped to the
        end of the book for the final section.
        """
        flat = self.flat_toc
        if not flat:
            return None
        index = None
        for i, node in enumerate(flat):
            if node["pid"] <= pid:
                index = i
            else:
                break
        if index is None:
            return None
        start = flat[index]["pid"]
        end = flat[index + 1]["pid"] - 1 if index + 1 < len(flat) else self.end_position
        return (start, end)

    # -- images -----------------------------------------------------------

    def _resource_fragment(self, name):
        return self._book.fragments.get(ftype=_RES_FRAGMENT, fid=name, first=True)

    def image_at(self, pid):
        """Return the image chunk at exactly `pid`, or None.

        Result: {name, mime, width, height, data, ext} where `data` is bytes in a
        browser-friendly format (JPEG XR is converted to PNG or JPEG).
        """
        for chunk in self.chunks:
            if chunk["position"] == pid and chunk["type"] == IMAGE:
                return self.image_by_name(chunk["content"])
            if chunk["position"] > pid:
                break
        return None

    def image_by_name(self, name):
        """Load an embedded image by its resource fragment name."""
        fragment = self._resource_fragment(name)
        if fragment is None:
            return None
        info = _plain(fragment.value)
        mime = info.get(_RES_MIME)
        location = info.get(_RES_LOCATION)
        raw = self._book.fragments.get(ftype=_RES_DATA, fid=location, first=True)
        if raw is None:
            return None
        data = raw.value
        data = data.tobytes() if hasattr(data, "tobytes") else bytes(data)

        ext = _mime_ext(mime)
        if mime == "image/jxr":
            # kfxlib returns (bytes, format_symbol); the format is not always PNG.
            data, fmt = _resources.convert_jxr_to_jpeg_or_png(data, str(name))
            ext = ".png" if _is_png(data) else ".jpg"
            mime = "image/png" if ext == ".png" else "image/jpeg"

        return {
            "name": str(name),
            "mime": mime,
            "width": info.get(_RES_WIDTH),
            "height": info.get(_RES_HEIGHT),
            "data": data,
            "ext": ext,
        }

    def preceding_image(self, pid, max_gap=3):
        """Image chunk shortly before `pid` — how a figure caption finds its art.

        KFX places the image chunk immediately before its caption, so a caption
        highlight resolves to the figure by looking back a few positions.
        """
        best = None
        for chunk in self.chunks:
            if chunk["position"] >= pid:
                break
            if chunk["type"] == IMAGE and pid - chunk["position"] <= max_gap:
                best = chunk
        return self.image_by_name(best["content"]) if best else None


def _is_png(data):
    return data[:8] == b"\x89PNG\r\n\x1a\n"


def _mime_ext(mime):
    return {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/tiff": ".tiff",
        "image/jxr": ".jxr",
        "image/svg+xml": ".svg",
    }.get(mime, ".bin")
