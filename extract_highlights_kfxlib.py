#!/usr/bin/env python3
"""Extract highlight text from a KFX file using position data from a .yjr JSON file.

This script relies on the KFX Input plugin's `kfxlib` library (included in this
repository) to decode the KFX container. It converts the book into a JSON
structure with content and position information and then maps the annotation
positions directly onto that content.
"""
import csv
import json
import re
import sys
from pathlib import Path
from html import escape

# Allow importing kfxlib either from the extracted folder or the bundled zip
base_dir = Path(__file__).parent
extracted = base_dir / "kfxlib_extracted"
if extracted.exists():
    sys.path.insert(0, str(extracted))
else:
    sys.path.insert(0, str(base_dir / "KFX Input.zip"))
from kfxlib import yj_book
from kfxlib.ion import IonAnnotation, IonSymbol
from kfxlib.yj_container import YJFragment


def _unwrap(obj):
    """Unwrap IonAnnotation wrappers to get the underlying value."""
    while isinstance(obj, IonAnnotation):
        obj = obj.value
    return obj


def load_content_sections(kfx_path):
    """Return sorted list of text sections with position and length."""
    book = yj_book.YJ_Book(kfx_path)
    content_json = json.loads(book.convert_to_json_content().decode("utf-8"))

    sections = [e for e in content_json.get("data", []) if e.get("type") == 1]
    sections.sort(key=lambda x: x["position"])

    # Infer text length from next section's position
    for i, sec in enumerate(sections[:-1]):
        sec["length"] = sections[i + 1]["position"] - sec["position"]
    if sections:
        sections[-1]["length"] = len(sections[-1]["content"])
    return sections


def extract_text(sections, start, end):
    """Extract text between the given start and end positions."""
    parts = []
    # Find the first section that might contain the start position
    idx = 0
    while idx < len(sections) - 1 and sections[idx + 1]["position"] <= start:
        idx += 1

    # Collect text from overlapping sections
    while idx < len(sections) and sections[idx]["position"] < end:
        sec = sections[idx]
        sec_start = sec["position"]
        sec_end = sec_start + sec["length"]
        slice_start = max(start, sec_start)
        slice_end = min(end + 1, sec_end)
        if slice_end > slice_start:
            a = slice_start - sec_start
            b = slice_end - sec_start
            parts.append(sec["content"][a:b])
        idx += 1
    return "".join(parts).strip()


def load_navigation(kfx_path):
    """Return (pages, toc) from the KFX navigation data."""
    book = yj_book.YJ_Book(kfx_path)
    book.decode_book(set_approximate_pages=0)

    nav = book.fragments.get("$389", first=True)
    if nav is None:
        return [], []

    pages = []
    toc_items = []

    pos_info = book.collect_content_position_info()
    eid_to_pid = {}
    for chunk in pos_info:
        if chunk.eid not in eid_to_pid:
            eid_to_pid[chunk.eid] = chunk.pid - chunk.eid_offset

    for container in nav.value[0].get("$392", []):
        if isinstance(container, IonSymbol):
            container = book.fragments.get(ftype="$391", fid=container)
        data = container.value if isinstance(container, YJFragment) else container
        data = _unwrap(data)
        typ = data.get("$235")
        if typ == "$237":  # page list
            page_list = data.get("$247", [])
            for entry in page_list:
                entry = _unwrap(entry)
                loc = _unwrap(entry["$246"])
                pid = eid_to_pid.get(loc["$155"], 0) + loc.get("$143", 0)
                label_obj = _unwrap(entry.get("$241", {}))
                label = label_obj.get("$244") if isinstance(label_obj, dict) else None
                if label is not None:
                    pages.append((pid, label))
            pages.sort(key=lambda x: x[0])
        elif typ == "$212":  # toc
            def build_items(items):
                result = []
                for itm in items:
                    itm = _unwrap(itm)
                    loc = _unwrap(itm["$246"])
                    eid = loc["$155"]
                    offset = loc.get("$143", 0)
                    pid = eid_to_pid.get(eid, 0) + offset
                    label_obj = _unwrap(itm.get("$241", {}))
                    label = label_obj.get("$244", "") if isinstance(label_obj, dict) else ""
                    node = {
                        "label": label,
                        "pid": pid,
                        "children": build_items(itm.get("$247", [])),
                    }
                    result.append(node)
                return result

            toc_items = build_items(data.get("$247", []))

    return pages, toc_items


def clean_title(raw_title):
    """Strip Kindle filename noise (ISBNs, order IDs, email fragments) from a title."""
    title = raw_title
    # Remove ISBN-like sequences (10 or 13 digits, optionally with hyphens)
    title = re.sub(r'\s*-?\s*\d{10,13}\b', '', title)
    # Remove "Order -XXXX-..." patterns (Kindle order identifiers)
    title = re.sub(r'\s*-?\s*Order\s*-[A-Za-z0-9-]+', '', title, flags=re.IGNORECASE)
    # Remove email-like fragments (often mangled with dashes for dots)
    title = re.sub(r'\s*-?\s*[A-Za-z0-9_.+-]+-[A-Za-z0-9-]+-(?:gmail|yahoo|hotmail|outlook|icloud|protonmail)-com-?\s*', '', title, flags=re.IGNORECASE)
    # Remove trailing dashes and whitespace
    title = re.sub(r'[\s-]+$', '', title)
    # Remove leading dashes and whitespace
    title = re.sub(r'^[\s-]+', '', title)
    return title.strip() or raw_title


def _format_citation_html(title, authors, year):
    """Build an APA citation div, gracefully omitting missing fields."""
    parts = []
    if authors and year:
        parts.append(f"{escape(authors[0])} ({escape(year)}). ")
    elif authors:
        parts.append(f"{escape(authors[0])}. ")
    elif year:
        parts.append(f"({escape(year)}). ")
    parts.append(f"<i>{escape(title)}</i>")
    parts.append(" [Kindle version]. Retrieved from Amazon.com")
    citation = "".join(parts)
    return f"<div class='citation'>Citation (APA): {citation}</div>"


def _compute_stats(items):
    """Compute summary statistics from a list of highlight/note items."""
    n_highlights = sum(1 for i in items if i["type"] == "highlight")
    n_notes = sum(1 for i in items if i["type"] == "note")
    sections = {i["section"] for i in items if i.get("section")}
    dates = [i["creationTime"] for i in items if i.get("creationTime")]
    dates.sort()
    return {
        "highlights": n_highlights,
        "notes": n_notes,
        "sections": len(sections),
        "first_date": dates[0].split("T")[0] if dates else None,
        "last_date": dates[-1].split("T")[0] if dates else None,
    }


def _format_stats_line(stats):
    """Format stats dict into a human-readable summary string."""
    nh, nn = stats["highlights"], stats["notes"]
    parts = [f"{nh} highlight{'s' if nh != 1 else ''}"]
    if nn:
        parts.append(f"{nn} note{'s' if nn != 1 else ''}")
    if stats["sections"]:
        ns = stats["sections"]
        parts.append(f"{ns} section{'s' if ns != 1 else ''}")
    if stats["first_date"] and stats["last_date"]:
        if stats["first_date"] == stats["last_date"]:
            parts.append(stats["first_date"])
        else:
            parts.append(f"{stats['first_date']} to {stats['last_date']}")
    return " | ".join(parts)


def _load_css():
    """Load CSS from the external highlights.css file next to this script."""
    css_path = Path(__file__).parent / "highlights.css"
    return css_path.read_text(encoding="utf-8")


def generate_html(title, authors, items, output_path, year=""):
    """Write highlights to an HTML file with simple Kindle Notebook styling."""
    style = f"<style type=\"text/css\">\n{_load_css()}</style>"

    html_parts = [
        "<?xml version='1.0' encoding='UTF-8' ?>",
        "<!DOCTYPE html PUBLIC '-//W3C//DTD XHTML 1.0 Strict//EN'",
        "  'http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd'>",
        "<html xmlns='http://www.w3.org/TR/1999/REC-html-in-xml' xml:lang='en' lang='en'>",
        "<head>",
        "<meta charset='UTF-8' />",
        style,
        "<title></title>",
        "</head>",
        "<body>",
        "<div class='bodyContainer'>",
        "<div class='notebookFor'>Notebook for</div>",
        f"<div class='bookTitle'>{escape(title)}</div>",
        f"<div class='authors'>{escape(', '.join(authors))}</div>",
        _format_citation_html(title, authors, year),
    ]

    stats_line = _format_stats_line(_compute_stats(items))
    html_parts.append(f"<div class='authors'>{escape(stats_line)}</div>")
    html_parts.append("<hr />")

    current_section = None
    for item in items:
        if item.get("section") and item["section"] != current_section:
            html_parts.append(f"<div class='sectionHeading'>{escape(item['section'])}</div>")
            current_section = item["section"]

        meta_parts = []
        if item.get("chapter"):
            meta_parts.append(item["chapter"])
        if item.get("page"):
            meta_parts.append(f"Page {item['page']}")
        if item.get("location") is not None:
            meta_parts.append(f"Location {item['location']}")
        meta_str = " - " + " >  ".join(meta_parts) if meta_parts else ""

        text = escape(item.get("text", "")).replace("\n", "<br/>")
        if item.get("type") == "note":
            html_parts.append(f"<div class='noteHeading'>Note{meta_str}</div>")
        else:
            html_parts.append(
                f"<div class='noteHeading'>Highlight (<span class='highlight_yellow'>yellow</span>){meta_str}</div>"
            )
        html_parts.append(f"<div class='noteText'>{text}</div>")

    html_parts.extend(["</div>", "</body>", "</html>"])

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(html_parts))


def _format_citation_text(title, authors, year):
    """Build an APA citation string for plain-text formats (Markdown, etc.)."""
    parts = []
    if authors and year:
        parts.append(f"{authors[0]} ({year}). ")
    elif authors:
        parts.append(f"{authors[0]}. ")
    elif year:
        parts.append(f"({year}). ")
    parts.append(f"*{title}*")
    parts.append(" [Kindle version]. Retrieved from Amazon.com")
    return "".join(parts)


def generate_markdown(title, authors, items, output_path, year=""):
    """Write highlights to a Markdown file."""
    lines = [
        f"# {title}",
        "",
    ]
    if authors:
        lines.append(f"**{', '.join(authors)}**")
        lines.append("")
    lines.append(f"Citation (APA): {_format_citation_text(title, authors, year)}")
    lines.append("")

    lines.append(_format_stats_line(_compute_stats(items)))
    lines.append("")
    lines.append("---")
    lines.append("")

    current_section = None
    for item in items:
        if item.get("section") and item["section"] != current_section:
            lines.append(f"## {item['section']}")
            lines.append("")
            current_section = item["section"]

        meta_parts = []
        if item.get("chapter"):
            meta_parts.append(item["chapter"])
        if item.get("page"):
            meta_parts.append(f"Page {item['page']}")
        if item.get("location") is not None:
            meta_parts.append(f"Location {item['location']}")
        meta_str = " > ".join(meta_parts) if meta_parts else ""

        text = item.get("text", "")
        if item.get("type") == "note":
            lines.append(f"**Note** - {meta_str}" if meta_str else "**Note**")
            lines.append("")
            lines.append(text)
        else:
            lines.append(f"**Highlight** - {meta_str}" if meta_str else "**Highlight**")
            lines.append("")
            # Prefix each line with > for multi-line blockquotes
            quoted = "\n".join(f"> {line}" for line in text.split("\n"))
            lines.append(quoted)
        lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def generate_json(title, authors, items, output_path, year=""):
    """Write highlights to a JSON file."""
    data = {
        "title": title,
        "authors": authors,
        "year": year,
        "items": items,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def generate_csv(title, authors, items, output_path, year=""):
    """Write highlights to a CSV file."""
    fields = ["type", "text", "section", "chapter", "page", "location", "creationTime"]
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(items)


def main():
    import argparse as _argparse

    parser = _argparse.ArgumentParser(description="Extract highlights from KFX book")
    parser.add_argument("json_file", help="Path to annotations JSON file")
    parser.add_argument("kfx_file", help="Path to KFX book file")
    parser.add_argument("--output-dir", help="Directory for output file (default: same as KFX file)")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="suppress per-highlight output (show summary only)")
    parser.add_argument("--title", type=str, default=None,
                        help="override the book title in the output")
    parser.add_argument("-f", "--format", choices=["html", "md", "json", "csv"],
                        default="html", help="output format (default: html)")
    args = parser.parse_args()

    json_file = args.json_file
    kfx_file = args.kfx_file

    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    ann_obj = data.get("annotation.cache.object", {})
    annotations = ann_obj.get("annotation.personal.highlight", [])
    notes = ann_obj.get("annotation.personal.note", [])
    if not annotations and not notes:
        print("No highlights or notes found in annotation data.")
        return

    sections = load_content_sections(kfx_file)
    meta = yj_book.YJ_Book(kfx_file).get_metadata()
    pages, toc = load_navigation(kfx_file)

    def page_for_pid(pid):
        p = None
        for pp, label in pages:
            if pp <= pid:
                p = label
            else:
                break
        return p

    def find_section(pid):
        section = None
        chapter = None
        for sec in toc:
            if sec["pid"] <= pid:
                section = sec
            else:
                break
        if section:
            for ch in section.get("children", []):
                if ch["pid"] <= pid:
                    chapter = ch
                else:
                    break
        return (section["label"] if section else None,
                chapter["label"] if chapter else None)

    highlights = []
    notes_by_end = {}
    for n in notes:
        pos = int(n["startPosition"].split(":")[1])
        notes_by_end.setdefault(pos, []).append(n["note"])

    annotations.sort(key=lambda a: int(a["startPosition"].split(":")[1]))

    # Deduplicate overlapping highlights: if one range fully contains another,
    # keep the longer one
    deduped = []
    for ann in annotations:
        s = int(ann["startPosition"].split(":")[1])
        e = int(ann["endPosition"].split(":")[1])
        # Check if this annotation is contained within the previous one
        if deduped:
            ps, pe, _ = deduped[-1]
            if s >= ps and e <= pe:
                continue  # fully contained, skip
            # Check if the previous one is contained within this one
            if ps >= s and pe <= e:
                deduped[-1] = (s, e, ann)
                continue
        deduped.append((s, e, ann))

    n_removed = len(annotations) - len(deduped)
    annotations_deduped = [(s, e, a) for s, e, a in deduped]

    quiet = args.quiet
    if not quiet:
        msg = f"Found {len(annotations_deduped)} highlights"
        if n_removed:
            msg += f" ({n_removed} overlapping removed)"
        print(f"{msg}:\n{'='*60}")
    for i, (start, end, ann) in enumerate(annotations_deduped, 1):
        text = extract_text(sections, start, end)
        page = page_for_pid(start)
        section, chapter = find_section(start)
        if not quiet:
            print(f"\nHighlight #{i}")
            print(f"Created: {ann['creationTime']}")
            print(f"Text: {text}\n{'-'*60}")
        highlights.append({
            "creationTime": ann["creationTime"],
            "text": text,
            "page": page,
            "location": start,
            "section": section,
            "chapter": chapter,
            "type": "highlight",
        })
        for note_text in notes_by_end.get(end, []):
            highlights.append({
                "creationTime": "",
                "text": note_text,
                "page": page,
                "location": start,
                "section": section,
                "chapter": chapter,
                "type": "note",
            })

    kfx_path = Path(kfx_file)
    ext_map = {"html": ".highlights.html", "md": ".highlights.md",
               "json": ".highlights.json", "csv": ".highlights.csv"}
    ext = ext_map[args.format]
    output_name = kfx_path.with_suffix(ext).name
    if args.output_dir:
        output_file = Path(args.output_dir) / output_name
    else:
        output_file = kfx_path.with_suffix(ext)
    year = ""
    if getattr(meta, "issue_date", None):
        year = str(meta.issue_date).split("-")[0]
    if args.title:
        title = args.title
    else:
        title = meta.title or clean_title(kfx_path.stem)
    authors = meta.authors or []
    generators = {
        "html": generate_html,
        "md": generate_markdown,
        "json": generate_json,
        "csv": generate_csv,
    }
    generators[args.format](title, authors, highlights, output_file, year)
    n_highlights = sum(1 for h in highlights if h["type"] == "highlight")
    n_notes = sum(1 for h in highlights if h["type"] == "note")
    print(f"Saved {n_highlights} highlights and {n_notes} notes to {output_file}")


if __name__ == "__main__":
    main()
