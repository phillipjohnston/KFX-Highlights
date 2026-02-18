#!/usr/bin/env python3
"""Extract highlights and notes from Kindle KFX books using synced annotation data."""

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


class DRMError(Exception):
    """Raised when extraction fails due to DRM protection."""
    def __init__(self, message, highlights=0, notes=0):
        super().__init__(message)
        self.highlights = highlights
        self.notes = notes


KNOWN_CONFIG_KEYS = {
    "format": {"type": str, "choices": ["html", "md", "json", "csv"]},
    "output_dir": {"type": str},
    "quiet": {"type": bool},
    "keep_json": {"type": bool},
    "skip_existing": {"type": bool},
    "jobs": {"type": int},
    "citation_style": {"type": str, "choices": ["apa"]},
    "theme": {"type": str, "choices": ["default"]},
    "kindle_path": {"type": str},
    "calibre_library": {"type": str},
}


def load_config(script_dir):
    """Load config.yaml from the script directory.

    Returns a dict of config values suitable for argparse set_defaults().
    Returns an empty dict if the file is missing or PyYAML is not installed.
    """
    config_path = script_dir / "config.yaml"
    if not config_path.is_file():
        return {}

    if not HAS_YAML:
        print("Warning: config.yaml found but pyyaml is not installed — ignoring config file")
        return {}

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        if raw is not None:
            print("Warning: config.yaml is not a YAML mapping — ignoring")
        return {}

    defaults = {}
    for key, value in raw.items():
        if key not in KNOWN_CONFIG_KEYS:
            print(f"Warning: unknown config key '{key}' — ignoring")
            continue

        spec = KNOWN_CONFIG_KEYS[key]
        expected_type = spec["type"]

        # Allow int where bool expected (YAML 1/0), but not the reverse
        if expected_type is bool and not isinstance(value, bool):
            print(f"Warning: config key '{key}' should be {expected_type.__name__}, "
                  f"got {type(value).__name__} — ignoring")
            continue
        if not isinstance(value, expected_type):
            print(f"Warning: config key '{key}' should be {expected_type.__name__}, "
                  f"got {type(value).__name__} — ignoring")
            continue

        if "choices" in spec and value not in spec["choices"]:
            print(f"Warning: config key '{key}' must be one of {spec['choices']}, "
                  f"got '{value}' — ignoring")
            continue

        defaults[key] = value

    return defaults


def load_sync_state(script_dir):
    """Load the book registry / sync state from .sync_state.json.

    Returns a fresh empty state dict if the file is missing or unreadable.
    """
    state_path = script_dir / ".sync_state.json"
    if state_path.is_file():
        try:
            with open(state_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: could not read {state_path}: {e} — starting fresh")
    return {"version": 1, "last_sync": None, "books": {}}


def save_sync_state(script_dir, state):
    """Write sync state atomically (write to .tmp, then replace)."""
    state_path = script_dir / ".sync_state.json"
    tmp_path = script_dir / ".sync_state.json.tmp"
    state["last_sync"] = datetime.now(timezone.utc).isoformat()
    with open(tmp_path, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")
    os.replace(tmp_path, state_path)


_SAVED_RE = re.compile(r"Saved (\d+) highlights? and (\d+) notes?")
_ASIN_RE = re.compile(r'_([A-Z0-9]{10,})$')

# Book file extensions and their annotation sidecar extensions (in preference order).
# For AZW3, .azw3r (highlights) is preferred over .azw3f (bookmarks/reading state).
# For MOBI6/.AZW, .mbp1 is the highlights sidecar; .mbs is an older fallback.
_BOOK_FORMATS = {
    ".kfx": [".yjr"],
    ".azw3": [".azw3r", ".azw3f"],
    ".mobi": [".mbp1", ".mbs"],
    ".azw": [".mbp1", ".mbs"],
}


def find_calibre_debug():
    """Locate the calibre-debug executable.

    Checks PATH first, then common installation locations.
    Returns the path string, or None if not found.
    """
    path = shutil.which("calibre-debug")
    if path:
        return path

    # Common locations
    candidates = [
        "/Applications/calibre.app/Contents/MacOS/calibre-debug",
        os.path.expanduser("~/Applications/calibre.app/Contents/MacOS/calibre-debug"),
        "/usr/bin/calibre-debug",
        "/usr/local/bin/calibre-debug",
    ]
    for candidate in candidates:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    return None


def extract_asin(stem):
    """Extract ASIN from Kindle stem like 'Design Patte~ng Series)_B000SEIBB8'."""
    m = _ASIN_RE.search(stem)
    return m.group(1) if m else None


def kindle_stem_to_title(stem):
    """Convert a Kindle filename stem to a rough title for fuzzy matching.

    Strips the ASIN suffix and replaces ~ with space.
    """
    # Remove ASIN suffix
    title = _ASIN_RE.sub('', stem)
    # Replace ~ (Kindle filename truncation marker) with space
    title = title.replace('~', ' ')
    return title.strip()


def _count_annotations(json_file):
    """Count highlights and notes from a krds JSON file.

    Returns (n_highlights, n_notes). Returns (0, 0) on any error.
    """
    try:
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        ann_obj = data.get("annotation.cache.object", {})
        nh = len(ann_obj.get("annotation.personal.highlight", []))
        nn = len(ann_obj.get("annotation.personal.note", []))
        return nh, nn
    except (OSError, json.JSONDecodeError, AttributeError):
        return 0, 0


def _is_azw3(book_file):
    """Check if a book file is AZW3 format (vs KFX)."""
    return Path(book_file).suffix.lower() == ".azw3"


def _is_mobi(book_file):
    """Check if a book file is MOBI6 or AZW (non-KF8) format."""
    return Path(book_file).suffix.lower() in (".mobi", ".azw")


def _format_azw3_output(result_json, book_file, output_dir, fmt, title=None, quiet=False):
    """Format AZW3 extraction results into the requested output format.

    The AZW3 extractor outputs JSON with {title, authors, year, items}.
    This function writes the formatted output file and returns (n_highlights, n_notes).
    """
    data = json.loads(result_json)
    items = data.get("items", [])
    book_title = title or data.get("title", "") or Path(book_file).stem
    authors = data.get("authors", [])
    year = data.get("year", "")

    n_highlights = sum(1 for i in items if i["type"] == "highlight")
    n_notes = sum(1 for i in items if i["type"] == "note")

    if not items:
        return 0, 0

    # Formatting is handled inline here rather than importing from
    # extract_highlights_kfxlib.py, which has kfxlib imports at module level
    # that would fail outside the kfxlib environment.
    from html import escape
    import csv as csv_mod

    ext_map = {"html": ".highlights.html", "md": ".highlights.md",
               "json": ".highlights.json", "csv": ".highlights.csv"}
    ext = ext_map[fmt]

    safe_title = re.sub(r'[<>:"/\\|?*]', '_', book_title)
    safe_title = re.sub(r'\s+', ' ', safe_title).strip()
    output_name = safe_title + ext
    output_file = output_dir / output_name

    # Handle name collisions
    if output_file.exists():
        original_name = output_name
        counter = 2
        while True:
            collision_name = f"{safe_title}-{counter}{ext}"
            output_file = output_dir / collision_name
            if not output_file.exists():
                if not quiet:
                    print(f"Note: {original_name} exists, using {collision_name}",
                          file=sys.stderr)
                break
            counter += 1

    if fmt == "json":
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump({"title": book_title, "authors": authors,
                        "year": year, "items": items},
                       f, indent=2, ensure_ascii=False)
    elif fmt == "csv":
        fields = ["type", "text", "section", "chapter", "page", "location",
                   "creationTime"]
        with open(output_file, "w", encoding="utf-8", newline="") as f:
            writer = csv_mod.DictWriter(f, fieldnames=fields,
                                        extrasaction="ignore")
            writer.writeheader()
            writer.writerows(items)
    elif fmt == "md":
        lines = [f"# {book_title}", ""]
        if authors:
            lines.append(f"**{', '.join(authors)}**")
            lines.append("")
        lines.extend(["---", ""])
        current_section = None
        for item in items:
            if item.get("section") and item["section"] != current_section:
                lines.append(f"## {item['section']}")
                lines.append("")
                current_section = item["section"]
            meta_parts = []
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
                lines.append(f"**Highlight** - {meta_str}" if meta_str
                             else "**Highlight**")
                lines.append("")
                quoted = "\n".join(f"> {line}" for line in text.split("\n"))
                lines.append(quoted)
            lines.append("")
        with open(output_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    else:  # html
        css_path = Path(__file__).parent / "highlights.css"
        css = css_path.read_text(encoding="utf-8") if css_path.is_file() else ""
        style = f"<style type=\"text/css\">\n{css}</style>"
        html_parts = [
            "<?xml version='1.0' encoding='UTF-8' ?>",
            "<!DOCTYPE html PUBLIC '-//W3C//DTD XHTML 1.0 Strict//EN'",
            "  'http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd'>",
            "<html xmlns='http://www.w3.org/TR/1999/REC-html-in-xml' "
            "xml:lang='en' lang='en'>",
            "<head>", "<meta charset='UTF-8' />", style,
            "<title></title>", "</head>", "<body>",
            "<div class='bodyContainer'>",
            "<div class='notebookFor'>Notebook for</div>",
            f"<div class='bookTitle'>{escape(book_title)}</div>",
            f"<div class='authors'>{escape(', '.join(authors))}</div>",
            "<hr />",
        ]
        current_section = None
        for item in items:
            if item.get("section") and item["section"] != current_section:
                html_parts.append(
                    f"<div class='sectionHeading'>"
                    f"{escape(item['section'])}</div>")
                current_section = item["section"]
            meta_parts = []
            if item.get("page"):
                meta_parts.append(f"Page {item['page']}")
            if item.get("location") is not None:
                meta_parts.append(f"Location {item['location']}")
            meta_str = (" - " + " >  ".join(meta_parts)) if meta_parts else ""
            text = escape(item.get("text", "")).replace("\n", "<br/>")
            if item.get("type") == "note":
                html_parts.append(
                    f"<div class='noteHeading'>Note{meta_str}</div>")
            else:
                html_parts.append(
                    f"<div class='noteHeading'>Highlight "
                    f"(<span class='highlight_yellow'>yellow</span>)"
                    f"{meta_str}</div>")
            html_parts.append(f"<div class='noteText'>{text}</div>")
        html_parts.extend(["</div>", "</body>", "</html>"])
        with open(output_file, "w", encoding="utf-8") as f:
            f.write("\n".join(html_parts))

    print(f"Saved {n_highlights} highlights and {n_notes} notes to {output_file}")
    return n_highlights, n_notes


def process_pair(book_file, annotation_file, script_dir, output_dir, quiet=False,
                 title=None, keep_json=False, fmt="html"):
    """Run the krds + extraction pipeline for a single book/annotation pair.

    Supports both KFX (.kfx + .yjr) and AZW3 (.azw3 + .azw3r/.azw3f) formats.

    Returns (n_highlights, n_notes) parsed from the extraction output.
    Returns (0, 0) when no highlights are found or counts can't be parsed.
    """
    output_dir.mkdir(exist_ok=True)

    krds_script = script_dir / "krds.py"
    subprocess.run(
        [sys.executable, str(krds_script), str(annotation_file),
         "--output-dir", str(output_dir)],
        check=True,
    )

    json_file = output_dir / (annotation_file.name + ".json")

    # Count annotations from the krds JSON before extraction — this
    # gives us counts even if the book extraction fails (e.g. DRM).
    raw_highlights, raw_notes = _count_annotations(json_file)

    if _is_azw3(book_file):
        # AZW3 path: use calibre-debug to run the AZW3 extractor
        calibre_debug = find_calibre_debug()
        if not calibre_debug:
            print("Error: calibre-debug not found. Install Calibre or add it to PATH.",
                  file=sys.stderr)
            raise subprocess.CalledProcessError(1, ["calibre-debug"])

        azw3_script = script_dir / "extract_highlights_azw3.py"
        extract_cmd = [calibre_debug, "-e", str(azw3_script),
                       "--", str(json_file), str(book_file)]
        if title:
            extract_cmd.extend(["--title", title])

        result = subprocess.run(extract_cmd, capture_output=True, text=True)
        n_highlights, n_notes = raw_highlights, raw_notes

        if result.returncode == 0:
            if result.stdout:
                try:
                    nh, nn = _format_azw3_output(
                        result.stdout, book_file, output_dir, fmt,
                        title=title, quiet=quiet)
                    n_highlights, n_notes = nh, nn
                except (json.JSONDecodeError, KeyError) as e:
                    print(f"Warning: failed to parse AZW3 extraction output: {e}",
                          file=sys.stderr)
        else:
            if "DRM" in (result.stderr or ""):
                if result.stderr:
                    print(result.stderr, end="", file=sys.stderr)
                raise DRMError(f"DRM-protected: {book_file.name}",
                               highlights=raw_highlights, notes=raw_notes)
            if result.stderr:
                print(result.stderr, end="", file=sys.stderr)
            raise subprocess.CalledProcessError(result.returncode, extract_cmd)
    elif _is_mobi(book_file):
        # MOBI6 / AZW path: use extract_highlights_mobi.py (runs under sys.executable)
        mobi_script = script_dir / "extract_highlights_mobi.py"
        extract_cmd = [sys.executable, str(mobi_script),
                       str(json_file), str(book_file)]
        if title:
            extract_cmd.extend(["--title", title])

        result = subprocess.run(extract_cmd, capture_output=True, text=True)
        n_highlights, n_notes = raw_highlights, raw_notes

        if result.returncode == 0:
            if result.stdout:
                try:
                    nh, nn = _format_azw3_output(
                        result.stdout, book_file, output_dir, fmt,
                        title=title, quiet=quiet)
                    n_highlights, n_notes = nh, nn
                except (json.JSONDecodeError, KeyError) as e:
                    print(f"Warning: failed to parse MOBI extraction output: {e}",
                          file=sys.stderr)
        else:
            if "DRM" in (result.stderr or ""):
                if result.stderr:
                    print(result.stderr, end="", file=sys.stderr)
                raise DRMError(f"DRM-protected: {book_file.name}",
                               highlights=raw_highlights, notes=raw_notes)
            if result.stderr:
                print(result.stderr, end="", file=sys.stderr)
            raise subprocess.CalledProcessError(result.returncode, extract_cmd)
    else:
        # KFX path: use the existing kfxlib extractor
        extract_cmd = [sys.executable,
                       str(script_dir / "extract_highlights_kfxlib.py"),
                       str(json_file), str(book_file),
                       "--output-dir", str(output_dir),
                       "--format", fmt]
        if quiet:
            extract_cmd.append("--quiet")
        if title:
            extract_cmd.extend(["--title", title])

        result = subprocess.run(extract_cmd, capture_output=True, text=True)
        n_highlights, n_notes = raw_highlights, raw_notes
        if result.returncode == 0:
            if result.stdout:
                print(result.stdout, end="")
                m = _SAVED_RE.search(result.stdout)
                if m:
                    n_highlights, n_notes = int(m.group(1)), int(m.group(2))
        else:
            # kfxlib raises exceptions containing "DRM" for encrypted content
            if "DRM" in (result.stderr or ""):
                if result.stderr:
                    print(result.stderr, end="", file=sys.stderr)
                raise DRMError(f"DRM-protected: {book_file.name}",
                               highlights=raw_highlights, notes=raw_notes)
            if result.stderr:
                print(result.stderr, end="", file=sys.stderr)
            raise subprocess.CalledProcessError(result.returncode, extract_cmd)

    if not keep_json and json_file.exists():
        json_file.unlink()

    return n_highlights, n_notes


def validate_kindle_path(kindle_path):
    """Confirm the Kindle mount point exists and has a documents/ subdirectory."""
    if not kindle_path.is_dir():
        print(f"Error: Kindle path not found: {kindle_path}")
        sys.exit(1)
    docs = kindle_path / "documents"
    if not docs.is_dir():
        print(f"Error: no documents/ directory found in {kindle_path}")
        print("  Make sure this is a mounted Kindle device.")
        sys.exit(1)
    return docs


def find_kindle_pairs(kindle_path):
    """Scan a mounted Kindle for book/annotation pairs.

    Supports both KFX (.kfx + .yjr) and AZW3 (.azw3 + .azw3r/.azw3f) pairs.

    Looks in documents/, documents/Downloads/, and any subdirectories of
    Downloads/ (e.g. Downloads/Items01/). For each book file, checks the
    sibling .sdr/ folder for a matching annotation file.
    """
    docs = validate_kindle_path(kindle_path)
    scan_dirs = [docs]
    downloads = docs / "Downloads"
    if downloads.is_dir():
        scan_dirs.append(downloads)
        # Kindle may organize downloads into subdirectories like Items01/
        for child in sorted(downloads.iterdir()):
            if child.is_dir() and not child.name.endswith(".sdr"):
                scan_dirs.append(child)

    pairs = []
    seen_stems = set()

    for scan_dir in scan_dirs:
        for book_ext, ann_exts in _BOOK_FORMATS.items():
            book_files = sorted(scan_dir.glob(f"*{book_ext}"))
            for book in book_files:
                if book.stem in seen_stems:
                    continue

                sdr_dir = scan_dir / f"{book.stem}.sdr"
                if not sdr_dir.is_dir():
                    continue

                # Search for annotation files in preference order
                best_match = None
                for ann_ext in ann_exts:
                    ann_matches = sorted(sdr_dir.glob(f"*{ann_ext}"))
                    ann_matches = [a for a in ann_matches
                                   if a.stem.startswith(book.stem)]
                    if ann_matches:
                        if len(ann_matches) > 1:
                            # Take the most recently modified one
                            ann_matches.sort(
                                key=lambda p: p.stat().st_mtime, reverse=True)
                        best_match = ann_matches[0]
                        break  # Use first matching extension type (preferred)

                if best_match:
                    seen_stems.add(book.stem)
                    pairs.append((book, best_match))

    return pairs


def filter_new_or_changed(pairs, sync_state, metadata_only=False):
    """Filter out books where files are unchanged and previously processed.

    When metadata_only=True (--import-metadata mode), also skips books whose
    annotation file is unchanged and previously imported as metadata-only. Only
    the annotation mtime is checked in that case (no book file is copied).

    Returns (filtered_pairs, skipped_count).
    """
    books = sync_state.get("books", {})
    filtered = []
    skipped = 0

    for book, ann in pairs:
        stem = book.stem
        record = books.get(stem)
        if record:
            status = record.get("status")
            try:
                ann_mtime = ann.stat().st_mtime
            except OSError:
                filtered.append((book, ann))
                continue

            # Use tolerance for mtime comparison — Kindle uses FAT32
            # (2-second resolution) while macOS uses APFS (nanoseconds),
            # and float precision can vary across JSON round-trips.
            # Note: sync state uses "kfx_mtime"/"yjr_mtime" field names
            # for historical reasons; they apply to any book/annotation format.
            if status == "success":
                try:
                    book_mtime = book.stat().st_mtime
                except OSError:
                    filtered.append((book, ann))
                    continue
                if (abs(book_mtime - record.get("kfx_mtime", 0)) < 2.0
                        and abs(ann_mtime - record.get("yjr_mtime", 0)) < 2.0):
                    skipped += 1
                    continue
            elif metadata_only and status == "metadata-only":
                # For --import-metadata, only the annotation file is copied;
                # skip if it hasn't changed since the last import.
                if abs(ann_mtime - record.get("yjr_mtime", 0)) < 2.0:
                    skipped += 1
                    continue

        filtered.append((book, ann))

    return filtered, skipped


def import_pair_to_input(kfx, yjr, input_dir):
    """Copy both book and annotation files to input/. Returns (dest_book, dest_ann)."""
    input_dir.mkdir(parents=True, exist_ok=True)
    dest_kfx = input_dir / kfx.name
    dest_yjr = input_dir / yjr.name
    shutil.copy2(kfx, dest_kfx)
    shutil.copy2(yjr, dest_yjr)
    return dest_kfx, dest_yjr


def import_metadata_only(yjr, pending_dir):
    """Copy just the annotation file to input/pending/. Returns dest path."""
    pending_dir.mkdir(parents=True, exist_ok=True)
    dest_yjr = pending_dir / yjr.name
    shutil.copy2(yjr, dest_yjr)
    return dest_yjr


def _update_sync_record(sync_state, kfx, yjr, status, error=None,
                        local_kfx=None, local_yjr=None,
                        highlights=None, notes=None):
    """Create or update a book's entry in the sync state.

    Preserves existing kindle_*_path and *_mtime fields if the kfx/yjr
    being passed are local copies (e.g. after --import-book copies files
    to input/ and then runs extraction on the copies).

    Note: Field names use "kfx" and "yjr" for historical reasons. They
    apply to any book format (KFX, AZW3) and annotation format (.yjr,
    .azw3r, .azw3f) respectively.
    """
    stem = kfx.stem
    books = sync_state.setdefault("books", {})
    record = books.get(stem, {})

    # Only update Kindle paths and mtimes if they aren't already set,
    # or if the paths match the existing Kindle paths (i.e., we're
    # processing from the Kindle directly, not from local copies).
    existing_kindle_kfx = record.get("kindle_kfx_path")
    if not existing_kindle_kfx or str(kfx) == existing_kindle_kfx:
        record["kindle_kfx_path"] = str(kfx)
        record["kindle_yjr_path"] = str(yjr)
        try:
            record["kfx_mtime"] = kfx.stat().st_mtime
            record["yjr_mtime"] = yjr.stat().st_mtime
        except OSError:
            pass

    record["status"] = status
    record["last_attempt"] = datetime.now(timezone.utc).isoformat()
    record["error"] = error
    if local_kfx is not None:
        record["local_kfx_path"] = str(local_kfx)
    if local_yjr is not None:
        record["local_yjr_path"] = str(local_yjr)
    if highlights is not None:
        record["highlights"] = highlights
    if notes is not None:
        record["notes"] = notes

    books[stem] = record


def _run_extraction(to_process, script_dir, output_dir, args, sync_state,
                    failed, drm_flagged):
    """Run extraction on a list of (kfx, yjr) pairs with DRM-aware error handling.

    Mutates sync_state, failed, and drm_flagged lists.
    """
    jobs = args.jobs if args.jobs >= 1 else (os.cpu_count() or 1)

    if jobs == 1:
        for i, (kfx, yjr) in enumerate(to_process, 1):
            print(f"\n{'='*60}")
            print(f"[{i}/{len(to_process)}] Processing: {kfx.stem}")
            print(f"{'='*60}")
            try:
                nh, nn = process_pair(kfx, yjr, script_dir, output_dir,
                                      quiet=True, keep_json=args.keep_json,
                                      fmt=args.format)
                print(f"  -> Done ({nh} highlights, {nn} notes)")
                _update_sync_record(sync_state, kfx, yjr, "success",
                                    highlights=nh, notes=nn)
            except DRMError as e:
                print(f"  -> DRM-PROTECTED ({e.highlights} highlights, {e.notes} notes)")
                drm_flagged.append(kfx.name)
                _update_sync_record(sync_state, kfx, yjr, "drm-flagged",
                                    error="DRM-protected",
                                    highlights=e.highlights, notes=e.notes)
            except subprocess.CalledProcessError as e:
                print(f"  -> FAILED (exit code {e.returncode})")
                failed.append(kfx.name)
                _update_sync_record(sync_state, kfx, yjr, "failed",
                                    error=f"exit code {e.returncode}")
    else:
        print(f"\nProcessing {len(to_process)} book(s) with {jobs} workers...")
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            futures = {
                pool.submit(process_pair, kfx, yjr, script_dir, output_dir,
                            quiet=True, keep_json=args.keep_json,
                            fmt=args.format): (kfx, yjr)
                for kfx, yjr in to_process
            }
            for future in as_completed(futures):
                kfx, yjr = futures[future]
                try:
                    nh, nn = future.result()
                    print(f"  Done: {kfx.stem} ({nh} highlights, {nn} notes)")
                    _update_sync_record(sync_state, kfx, yjr, "success",
                                        highlights=nh, notes=nn)
                except DRMError as e:
                    print(f"  DRM-PROTECTED: {kfx.stem} ({e.highlights} highlights, {e.notes} notes)")
                    drm_flagged.append(kfx.name)
                    _update_sync_record(sync_state, kfx, yjr, "drm-flagged",
                                        error="DRM-protected",
                                        highlights=e.highlights, notes=e.notes)
                except subprocess.CalledProcessError as e:
                    print(f"  FAILED: {kfx.stem} (exit code {e.returncode})")
                    failed.append(kfx.name)
                    _update_sync_record(sync_state, kfx, yjr, "failed",
                                        error=f"exit code {e.returncode}")


def build_calibre_index(calibre_path):
    """Query Calibre's metadata.db to build lookup indexes.

    Returns (asin_to_book, asin_to_title, title_index) where:
    - asin_to_book: {asin: {title, book_path, format, book_id}} — books with KFX/KFX-ZIP/AZW3
    - asin_to_title: {asin: {title, book_id}} — all books with ASINs
    - title_index: {book_id: {title, has_book, book_path}} — for fuzzy matching
    """
    db_path = calibre_path / "metadata.db"
    if not db_path.is_file():
        print(f"Error: Calibre metadata.db not found at {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        # All books with mobi-asin identifiers
        asin_rows = conn.execute("""
            SELECT b.id AS book_id, b.title, i.val AS asin
            FROM books b
            JOIN identifiers i ON i.book = b.id
            WHERE i.type = 'mobi-asin'
        """).fetchall()

        # All KFX/KFX-ZIP/AZW3/MOBI/AZW format entries
        book_rows = conn.execute("""
            SELECT d.book, d.format, d.name, b.title, b.path
            FROM data d
            JOIN books b ON b.id = d.book
            WHERE d.format IN ('KFX', 'KFX-ZIP', 'AZW3', 'MOBI', 'AZW')
        """).fetchall()
    finally:
        conn.close()

    # Format preference: KFX > KFX-ZIP > AZW3 > MOBI > AZW
    _FORMAT_PRIORITY = {"KFX": 0, "KFX-ZIP": 1, "AZW3": 2, "MOBI": 3, "AZW": 4}

    # Build book lookup: book_id -> {format, book_path, title}
    books_by_id = {}
    for row in book_rows:
        book_id = row["book"]
        fmt = row["format"]
        # Keep the highest-priority format
        if book_id in books_by_id:
            existing_priority = _FORMAT_PRIORITY.get(books_by_id[book_id]["format"], 99)
            new_priority = _FORMAT_PRIORITY.get(fmt, 99)
            if new_priority >= existing_priority:
                continue

        ext_map = {"KFX": ".kfx", "KFX-ZIP": ".kfx-zip", "AZW3": ".azw3",
                   "MOBI": ".mobi", "AZW": ".azw"}
        ext = ext_map.get(fmt, f".{fmt.lower()}")
        # Calibre stores files as: library/Author/Title (ID)/name.ext
        # The 'name' column from the data table is the actual filename stem
        book_dir = calibre_path / row["path"]
        book_path = book_dir / f"{row['name']}{ext}"
        if not book_path.is_file():
            # Fall back to globbing the directory
            try:
                if fmt == "KFX-ZIP":
                    pattern = "*.kfx-zip"
                else:
                    pattern = f"*.{fmt.lower()}"
                matches = list(book_dir.glob(pattern))
                if not matches:
                    matches = [p for p in book_dir.iterdir()
                               if p.suffix.lower() == ext]
                book_path = matches[0] if matches else None
            except OSError:
                book_path = None

        if book_path and book_path.is_file():
            books_by_id[book_id] = {
                "format": fmt,
                "book_path": book_path,
                "title": row["title"],
            }

    # Build the three indexes
    asin_to_book = {}
    asin_to_title = {}
    for row in asin_rows:
        asin = row["asin"].upper()
        book_id = row["book_id"]
        asin_to_title[asin] = {"title": row["title"], "book_id": book_id}
        if book_id in books_by_id:
            info = books_by_id[book_id]
            asin_to_book[asin] = {
                "title": info["title"],
                "kfx_path": info["book_path"],  # kept as kfx_path for compatibility
                "format": info["format"],
                "book_id": book_id,
            }

    title_index = {}
    # Include books that have a supported format (with or without ASIN)
    for book_id, info in books_by_id.items():
        title_index[book_id] = {
            "title": info["title"],
            "has_kfx": True,  # kept as has_kfx for compatibility
            "kfx_path": info["book_path"],
        }
    # Add ASIN-bearing books without any supported format
    for row in asin_rows:
        if row["book_id"] not in title_index:
            title_index[row["book_id"]] = {
                "title": row["title"],
                "has_kfx": False,
                "kfx_path": None,
            }

    return asin_to_book, asin_to_title, title_index


def fuzzy_match_title(kindle_title, title_index, threshold=0.80):
    """Find the best fuzzy match for a Kindle title in the Calibre title index.

    Returns {book_id, title, has_kfx, kfx_path, score} or None.
    """
    best_score = 0
    best_match = None
    kindle_lower = kindle_title.lower()

    for book_id, info in title_index.items():
        score = SequenceMatcher(None, kindle_lower, info["title"].lower()).ratio()
        if score > best_score:
            best_score = score
            best_match = (book_id, info)

    if best_match and best_score >= threshold:
        book_id, info = best_match
        return {
            "book_id": book_id,
            "title": info["title"],
            "has_kfx": info["has_kfx"],
            "kfx_path": info["kfx_path"],
            "score": best_score,
        }
    return None


def find_annotation_for_stem(stem, sync_state, script_dir):
    """Locate the annotation file for a Kindle book stem.

    Searches for .yjr, .azw3r, and .azw3f files (in preference order).

    Search order:
    1. local_yjr_path from sync state (set by prior --import-metadata)
    2. Scan input/pending/ by stem prefix match
    3. kindle_yjr_path from sync state (on-device path)

    Returns Path or None.
    """
    all_ann_exts = [".yjr", ".azw3r", ".azw3f", ".mbp1", ".mbs"]

    books = sync_state.get("books", {})
    record = books.get(stem, {})

    # Check sync state for a known local annotation path
    local_yjr = record.get("local_yjr_path")
    if local_yjr:
        p = Path(local_yjr)
        if p.is_file():
            return p

    # Scan input/pending/ for a matching annotation file
    pending_dir = script_dir / "input" / "pending"
    if pending_dir.is_dir():
        for ann_ext in all_ann_exts:
            for ann in sorted(pending_dir.glob(f"*{ann_ext}")):
                if ann.stem.startswith(stem):
                    return ann

    # Fallback: on-device annotation path (e.g. for --all-books remapping)
    kindle_yjr = record.get("kindle_yjr_path")
    if kindle_yjr:
        p = Path(kindle_yjr)
        if p.is_file():
            return p

    return None


def match_calibre_books(sync_state, calibre_path, script_dir, all_books=False):
    """Match books from sync state to Calibre library entries.

    By default, only considers DRM-flagged/metadata-only books. When
    all_books=True, also includes successfully processed books so they
    can be remapped to Calibre KFX files.

    Returns (matched, matched_no_kfx, unmatched, no_yjr) where:
    - matched: [{stem, asin, calibre_title, kfx_path, yjr_path, fuzzy, score}]
    - matched_no_kfx: [{stem, asin, calibre_title}]
    - unmatched: [stem]
    - no_yjr: [{stem, calibre_title, kfx_path}]
    """
    asin_to_kfx, asin_to_title, title_index = build_calibre_index(calibre_path)

    books = sync_state.get("books", {})
    if all_books:
        eligible_statuses = {"drm-flagged", "metadata-only", "success",
                             "imported", "failed"}
        candidate_books = {stem: record for stem, record in books.items()
                          if record.get("status") in eligible_statuses}
    else:
        candidate_books = {stem: record for stem, record in books.items()
                          if record.get("status") in {"drm-flagged",
                                                       "metadata-only"}}

    matched = []
    matched_no_kfx = []
    unmatched = []
    no_yjr = []

    for stem, record in sorted(candidate_books.items()):
        asin = extract_asin(stem)

        # Try ASIN match first
        if asin and asin in asin_to_kfx:
            info = asin_to_kfx[asin]
            yjr_path = find_annotation_for_stem(stem, sync_state, script_dir)
            if yjr_path:
                matched.append({
                    "stem": stem,
                    "asin": asin,
                    "calibre_title": info["title"],
                    "kfx_path": info["kfx_path"],
                    "yjr_path": yjr_path,
                    "fuzzy": False,
                    "score": 1.0,
                })
            else:
                no_yjr.append({
                    "stem": stem,
                    "calibre_title": info["title"],
                    "kfx_path": info["kfx_path"],
                })
            continue

        if asin and asin in asin_to_title:
            # ASIN matched a Calibre book but it has no supported format
            info = asin_to_title[asin]
            matched_no_kfx.append({
                "stem": stem,
                "asin": asin,
                "calibre_title": info["title"],
            })
            continue

        # Fallback: fuzzy title matching
        kindle_title = kindle_stem_to_title(stem)
        fuzzy = fuzzy_match_title(kindle_title, title_index)
        if fuzzy and fuzzy["has_kfx"]:
            yjr_path = find_annotation_for_stem(stem, sync_state, script_dir)
            if yjr_path:
                matched.append({
                    "stem": stem,
                    "asin": asin,
                    "calibre_title": fuzzy["title"],
                    "kfx_path": fuzzy["kfx_path"],
                    "yjr_path": yjr_path,
                    "fuzzy": True,
                    "score": fuzzy["score"],
                })
            else:
                no_yjr.append({
                    "stem": stem,
                    "calibre_title": fuzzy["title"],
                    "kfx_path": fuzzy["kfx_path"],
                })
        elif fuzzy and not fuzzy["has_kfx"]:
            matched_no_kfx.append({
                "stem": stem,
                "asin": asin,
                "calibre_title": fuzzy["title"],
            })
        else:
            unmatched.append(stem)

    return matched, matched_no_kfx, unmatched, no_yjr


def run_calibre_matching(args, script_dir, sync_state, output_dir):
    """Top-level handler for --calibre-library mode.

    Matches books to Calibre library, prints a report, and processes
    matched books. By default only DRM-flagged books; with --all-books
    also includes successfully processed and other synced books.
    """
    calibre_path = args.calibre_library

    if not calibre_path.is_dir():
        print(f"Error: Calibre library not found: {calibre_path}")
        sys.exit(1)

    matched, matched_no_kfx, unmatched, no_yjr = match_calibre_books(
        sync_state, calibre_path, script_dir,
        all_books=args.all_books or args.reprocess or args.reprocess_missing or args.rematch)

    # Count candidate books for context
    books = sync_state.get("books", {})
    include_all = args.all_books or args.reprocess or args.reprocess_missing
    if include_all:
        eligible_statuses = {"drm-flagged", "metadata-only", "success",
                             "imported", "failed"}
    else:
        eligible_statuses = {"drm-flagged", "metadata-only"}
    candidate_count = sum(1 for r in books.values()
                         if r.get("status") in eligible_statuses)

    # Separate ASIN and fuzzy matches for reporting
    asin_matched = [m for m in matched if not m["fuzzy"]]
    fuzzy_matched = [m for m in matched if m["fuzzy"]]

    # --- Report ---
    print(f"\nCalibre library: {calibre_path}")
    if include_all:
        if args.reprocess:
            label = "--reprocess"
        elif args.reprocess_missing:
            label = "--reprocess-missing"
        else:
            label = "--all-books"
        print(f"Eligible books in sync state: {candidate_count} ({label})\n")
    else:
        print(f"DRM-flagged books in sync state: {candidate_count}\n")

    print(f"ASIN-matched with KFX: {len(asin_matched)}")
    if not args.quiet:
        for m in asin_matched:
            print(f"  {m['stem']}")
            print(f"    -> {m['calibre_title']}")

    if fuzzy_matched:
        print(f"\nFuzzy-matched with KFX: {len(fuzzy_matched)}"
              + (" (will be skipped — use --accept-fuzzy to include)"
                 if not args.accept_fuzzy else ""))
        for m in fuzzy_matched:
            print(f"  {m['stem']}")
            print(f"    -> {m['calibre_title']} (score: {m['score']:.0%})")

    if matched_no_kfx:
        print(f"\nMatched but no supported format (KFX/AZW3/MOBI): {len(matched_no_kfx)}")
        if not args.quiet:
            for m in matched_no_kfx:
                print(f"  {m['stem']}")
                print(f"    -> {m['calibre_title']}")

    if no_yjr:
        print(f"\nMatched but no annotation file found: {len(no_yjr)}")
        if not args.quiet:
            for m in no_yjr:
                print(f"  {m['stem']}")
                print(f"    -> {m['calibre_title']}")
        print("  Tip: use --kindle --import-metadata to copy annotations first.")

    if unmatched:
        print(f"\nNo match found: {len(unmatched)}")
        if not args.quiet:
            for stem in unmatched:
                title = kindle_stem_to_title(stem)
                print(f"  {title}")

    # Filter to processable books
    to_process = list(asin_matched)
    if args.accept_fuzzy:
        to_process.extend(fuzzy_matched)

    if not to_process:
        print("\nNo books to process.")
        return

    # If --reprocess-missing, filter to books whose output file doesn't exist
    if args.reprocess_missing:
        ext_map = {"html": ".highlights.html", "md": ".highlights.md",
                   "json": ".highlights.json", "csv": ".highlights.csv"}
        ext = ext_map[args.format]
        missing = []
        for m in to_process:
            title = m["calibre_title"]
            safe_title = re.sub(r'[<>:"/\\|?*]', '_', title)
            safe_title = re.sub(r'\s+', ' ', safe_title).strip()
            output_file = output_dir / (safe_title + ext)
            if not output_file.exists():
                missing.append(m)
        skipped_count = len(to_process) - len(missing)
        to_process = missing
        if skipped_count:
            print(f"\nSkipping {skipped_count} book(s) with existing output files")
        if not to_process:
            print("No books with missing output files found.")
            return

    # If --rematch mode, just update the sync state with new paths
    if args.rematch:
        # If --missing-only, filter to only books with missing Calibre files
        if args.missing_only:
            missing_books = []
            for m in to_process:
                record = books.get(m["stem"], {})
                old_path = record.get("calibre_kfx_path")
                if old_path and not Path(old_path).is_file():
                    missing_books.append(m)

            original_count = len(to_process)
            to_process = missing_books
            print(f"\nFiltering to {len(to_process)} book(s) with missing Calibre files (out of {original_count} total)")

            if not to_process:
                print("No books with missing Calibre files found.")
                return

        print(f"\nUpdating Calibre paths in sync state for {len(to_process)} book(s)...")
        updated = 0
        skipped_disabled = 0
        for m in to_process:
            record = books.get(m["stem"], {})

            # Skip if rematch is explicitly disabled for this book
            if record.get("rematch_disabled"):
                print(f"  Skipped (rematch disabled): {m['calibre_title']}")
                skipped_disabled += 1
                continue

            old_path = record.get("calibre_kfx_path")
            new_path = str(m["kfx_path"])

            if old_path != new_path:
                record["calibre_kfx_path"] = new_path
                record["calibre_title"] = m["calibre_title"]
                record["last_attempt"] = datetime.now(timezone.utc).isoformat()
                # Clear file_missing flag since we found the file
                if "file_missing" in record:
                    del record["file_missing"]
                books[m["stem"]] = record
                updated += 1
                print(f"  Updated: {m['calibre_title']}")
                if old_path:
                    print(f"    Old: {old_path}")
                print(f"    New: {new_path}")
            else:
                print(f"  Unchanged: {m['calibre_title']}")

        save_sync_state(script_dir, sync_state)
        summary_parts = [f"{updated} path(s) updated", f"{len(to_process) - updated - skipped_disabled} unchanged"]
        if skipped_disabled:
            summary_parts.append(f"{skipped_disabled} skipped (rematch disabled)")
        print(f"\nRematch complete: {', '.join(summary_parts)}")
        return

    # Apply --limit
    if args.limit and len(to_process) > args.limit:
        print(f"\nLimiting to first {args.limit} book(s)")
        to_process = to_process[:args.limit]

    # Apply --dry-run
    if args.dry_run:
        print(f"\nDry run — would process {len(to_process)} book(s):")
        for m in to_process:
            label = "fuzzy" if m["fuzzy"] else "asin"
            print(f"  [{label}] {m['calibre_title']}")
        return

    # Process matched books
    print(f"\nProcessing {len(to_process)} book(s)...\n")
    failed = []
    succeeded = 0

    for i, m in enumerate(to_process, 1):
        print(f"{'='*60}")
        print(f"[{i}/{len(to_process)}] {m['calibre_title']}")
        print(f"{'='*60}")

        # Check if files exist before processing
        if not m["kfx_path"].is_file():
            print(f"  -> FAILED: Calibre book file not found")
            failed.append(m)
            record = books.get(m["stem"], {})
            record["last_attempt"] = datetime.now(timezone.utc).isoformat()
            record["error"] = "calibre-file-missing"
            record["file_missing"] = True
            record["calibre_kfx_path"] = str(m["kfx_path"])
            books[m["stem"]] = record
            continue

        if not m["yjr_path"].is_file():
            print(f"  -> FAILED: Annotation file not found")
            failed.append(m)
            record = books.get(m["stem"], {})
            record["last_attempt"] = datetime.now(timezone.utc).isoformat()
            record["error"] = "annotation-file-missing"
            record["file_missing"] = True
            books[m["stem"]] = record
            continue

        try:
            nh, nn = process_pair(
                m["kfx_path"], m["yjr_path"], script_dir, output_dir,
                quiet=True, title=m["calibre_title"],
                keep_json=args.keep_json, fmt=args.format)
            print(f"  -> Done ({nh} highlights, {nn} notes)")
            succeeded += 1

            # Update sync state directly by Kindle stem
            record = books.get(m["stem"], {})
            record["status"] = "success"
            record["last_attempt"] = datetime.now(timezone.utc).isoformat()
            record["error"] = None
            record["calibre_kfx_path"] = str(m["kfx_path"])
            record["calibre_title"] = m["calibre_title"]
            # Clear file_missing flag on success
            if "file_missing" in record:
                del record["file_missing"]
            if nh is not None:
                record["highlights"] = nh
            if nn is not None:
                record["notes"] = nn
            books[m["stem"]] = record

        except DRMError as e:
            print(f"  -> FAILED: Calibre KFX is also DRM-protected")
            failed.append(m)
            record = books.get(m["stem"], {})
            record["last_attempt"] = datetime.now(timezone.utc).isoformat()
            record["error"] = "calibre-kfx-drm"
            record["calibre_kfx_path"] = str(m["kfx_path"])
            books[m["stem"]] = record
        except subprocess.CalledProcessError as e:
            print(f"  -> FAILED (exit code {e.returncode})")
            failed.append(m)
            record = books.get(m["stem"], {})
            record["last_attempt"] = datetime.now(timezone.utc).isoformat()
            record["error"] = f"calibre-exit-{e.returncode}"
            books[m["stem"]] = record

    # Summary
    print(f"\n{'='*60}")
    print(f"Processed: {succeeded}/{len(to_process)}", end="")
    if failed:
        print(f"  Failed: {len(failed)}", end="")
    print()

    if failed:
        print("\nFailed:")
        for m in failed:
            print(f"  - {m['calibre_title']}")

    save_sync_state(script_dir, sync_state)


def find_pairs(input_dir):
    """Match book files to annotation files in input_dir.

    Supports both KFX (.kfx + .yjr) and AZW3 (.azw3 + .azw3r/.azw3f) pairs.

    The Kindle naming convention places the annotation filename as an extension
    of the book stem (with an appended annotation hash). So we pair an annotation
    file with a book file when the annotation name starts with the book stem.
    """
    pairs = []
    all_annotation_files = set()

    for book_ext, ann_exts in _BOOK_FORMATS.items():
        book_files = sorted(input_dir.glob(f"*{book_ext}"))
        ann_files = []
        for ann_ext in ann_exts:
            ann_files.extend(input_dir.glob(f"*{ann_ext}"))
        ann_files = sorted(set(ann_files))
        all_annotation_files.update(ann_files)

        for book in book_files:
            # Find annotation files whose stem starts with the book stem
            matches = [a for a in ann_files if a.stem.startswith(book.stem)]
            if len(matches) == 1:
                pairs.append((book, matches[0]))
            elif len(matches) > 1:
                # For AZW3, prefer .azw3r over .azw3f (first ext in list)
                preferred = [a for a in matches
                             if a.suffix in _BOOK_FORMATS.get(book_ext, [])[:1]]
                if len(preferred) == 1:
                    pairs.append((book, preferred[0]))
                else:
                    print(f"Warning: multiple annotation files match {book.name}, skipping:")
                    for m in matches:
                        print(f"  - {m.name}")
            else:
                ann_desc = "/".join(ann_exts)
                print(f"Warning: no {ann_desc} file found for {book.name}, skipping")

    matched_annotations = {a for _, a in pairs}
    unmatched = all_annotation_files - matched_annotations
    for a in sorted(unmatched):
        print(f"Warning: no book file found for {a.name}, skipping")

    return pairs


def main():
    parser = argparse.ArgumentParser(
        description="Extract highlights and notes from Kindle books (KFX and AZW3).",
        epilog="""\
examples:
  %(prog)s                              Process all paired books in input/
  %(prog)s input/book.kfx input/book.yjr   Process a single KFX book
  %(prog)s input/book.azw3 input/book.azw3r  Process a single AZW3 book
  %(prog)s -o results/ book.kfx book.yjr   Write output to a custom directory
  %(prog)s --kindle /Volumes/Kindle        Process directly from a connected Kindle
  %(prog)s --kindle /Volumes/Kindle --import-only   Copy files to input/ only
  %(prog)s --kindle /Volumes/Kindle --dry-run       Preview what would be done

Supported formats:
  KFX:  .kfx book + .yjr annotations
  AZW3: .azw3 book + .azw3r/.azw3f annotations (requires Calibre)

In bulk mode, book and annotation files are paired by filename: the annotation
name must start with the book stem (Kindle's default naming convention).""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "kfx_file", nargs="?", type=Path, metavar="BOOK",
        help="path to the book file (.kfx or .azw3)",
    )
    parser.add_argument(
        "yjr_file", nargs="?", type=Path, metavar="ANNOTATIONS",
        help="path to the annotation file (.yjr, .azw3r, or .azw3f)",
    )
    parser.add_argument(
        "-o", "--output-dir", type=Path, default=None, metavar="DIR",
        help="directory for output files (default: output/)",
    )
    parser.add_argument(
        "--skip-existing", action="store_true",
        help="skip books whose output HTML already exists (bulk mode only)",
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true",
        help="suppress per-highlight console output (show summary only)",
    )
    parser.add_argument(
        "--title", type=str, default=None,
        help="override the book title in the output (single-pair mode only)",
    )
    parser.add_argument(
        "--keep-json", action="store_true",
        help="keep intermediate JSON files (deleted by default after success)",
    )
    parser.add_argument(
        "-f", "--format", choices=["html", "md", "json", "csv"], default="html",
        help="output format: html (default), md, json, or csv",
    )
    parser.add_argument(
        "-j", "--jobs", type=int, default=1, metavar="N",
        help="number of books to process in parallel (default: 1, 0 = CPU count)",
    )
    parser.add_argument(
        "--kindle", type=Path, nargs="?", const="USE_CONFIG", default=None, metavar="PATH",
        help="path to mounted Kindle device (e.g. /Volumes/Kindle); uses config default if no path specified",
    )
    import_group = parser.add_mutually_exclusive_group()
    import_group.add_argument(
        "--import-only", action="store_true",
        help="copy book + annotation files from Kindle to input/ without extracting",
    )
    import_group.add_argument(
        "--import-book", action="store_true",
        help="copy book + annotation files to input/ and run extraction",
    )
    import_group.add_argument(
        "--import-metadata", action="store_true",
        help="copy only annotation files to input/pending/ (for DRM books)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="preview what would be done without making changes",
    )
    parser.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="process at most N books (for testing)",
    )
    parser.add_argument(
        "--calibre-library", type=Path, nargs="?", const="USE_CONFIG", default=None, metavar="PATH",
        help="path to Calibre library (match DRM books to unlocked Calibre files); uses config default if no path specified",
    )
    parser.add_argument(
        "--accept-fuzzy", action="store_true",
        help="include fuzzy title matches when using --calibre-library (default: ASIN-only)",
    )
    parser.add_argument(
        "--all-books", action="store_true",
        help="match all synced books to Calibre, not just DRM-flagged (requires --calibre-library)",
    )
    parser.add_argument(
        "--reprocess", action="store_true",
        help="reprocess previously successful books (bypass sync state skip logic)",
    )
    parser.add_argument(
        "--reprocess-missing", action="store_true",
        help="reprocess only previously successful books whose output file is missing",
    )
    parser.add_argument(
        "--rematch", action="store_true",
        help="update Calibre book paths in sync state (useful after Calibre library reorganization)",
    )
    parser.add_argument(
        "--missing-only", action="store_true",
        help="rematch only books whose Calibre files are missing (requires --rematch and --calibre-library)",
    )

    script_dir = Path(__file__).parent
    config = load_config(script_dir)

    # Map config keys to argparse dest names and apply as defaults.
    # CLI flags override these; argparse built-in defaults are lowest priority.
    argparse_defaults = {}
    for key, value in config.items():
        if key == "output_dir":
            argparse_defaults["output_dir"] = Path(value)
        elif key == "format":
            # argparse dest is "format" (from --format)
            argparse_defaults["format"] = value
        elif key == "kindle_path":
            argparse_defaults["kindle"] = Path(value)
        elif key == "calibre_library":
            argparse_defaults["calibre_library"] = Path(value)
        elif key in ("quiet", "keep_json", "skip_existing", "jobs"):
            argparse_defaults[key] = value
        # citation_style and theme are reserved for future use

    if argparse_defaults:
        parser.set_defaults(**argparse_defaults)

    args = parser.parse_args()

    # Resolve USE_CONFIG markers for --kindle and --calibre-library
    if args.kindle == Path("USE_CONFIG"):
        if "kindle_path" in config:
            args.kindle = Path(config["kindle_path"])
        else:
            parser.error("--kindle requires a path (none found in config)")

    if args.calibre_library == Path("USE_CONFIG"):
        if "calibre_library" in config:
            args.calibre_library = Path(config["calibre_library"])
        else:
            parser.error("--calibre-library requires a path (none found in config)")

    if config and not args.quiet:
        print(f"Loaded config from {script_dir / 'config.yaml'}")

    output_dir = args.output_dir or (script_dir / "output")

    # Validate flag combinations
    if (args.import_only or args.import_book or args.import_metadata) and not args.kindle:
        parser.error("--import-only, --import-book, and --import-metadata require --kindle")

    if args.kindle and (args.kfx_file or args.yjr_file):
        parser.error("--kindle cannot be combined with positional kfx/yjr arguments")

    if args.calibre_library and (args.kfx_file or args.yjr_file):
        parser.error("--calibre-library cannot be combined with positional kfx/yjr arguments")

    # Note: Having both kindle_path and calibre_library in config is fine —
    # they're defaults for different modes. Only error if the user explicitly
    # passes both flags on the CLI, which would be ambiguous.
    # We detect explicit CLI usage by checking sys.argv.
    if args.calibre_library and args.kindle:
        if "--calibre-library" in sys.argv and "--kindle" in sys.argv:
            parser.error("--calibre-library cannot be combined with --kindle")

    if "--calibre-library" in sys.argv and (args.import_only or args.import_book or args.import_metadata):
        parser.error("--calibre-library cannot be combined with --import-* flags")

    if args.accept_fuzzy and "--calibre-library" not in sys.argv:
        parser.error("--accept-fuzzy requires --calibre-library")

    if args.all_books and "--calibre-library" not in sys.argv:
        parser.error("--all-books requires --calibre-library")

    if args.rematch and "--calibre-library" not in sys.argv:
        parser.error("--rematch requires --calibre-library")

    if args.missing_only and not args.rematch:
        parser.error("--missing-only requires --rematch")

    # If one positional arg is given without the other, that's an error
    if (args.kfx_file is None) != (args.yjr_file is None):
        parser.error("provide both BOOK and ANNOTATIONS files, or neither for bulk mode")

    # --- Calibre library matching mode ---
    # Only enter Calibre mode if explicitly requested via CLI flag, not just config default
    calibre_mode = "--calibre-library" in sys.argv
    kindle_mode = "--kindle" in sys.argv

    if calibre_mode:
        sync_state = load_sync_state(script_dir)
        run_calibre_matching(args, script_dir, sync_state, output_dir)
        sys.exit(0)

    # --- Kindle device mode ---
    if kindle_mode:
        sync_state = load_sync_state(script_dir)
        pairs = find_kindle_pairs(args.kindle)

        if not pairs:
            print("No paired book/annotation files found on Kindle.")
            sys.exit(0)

        print(f"Found {len(pairs)} book(s) on Kindle:\n")
        for kfx, yjr in pairs:
            print(f"  {kfx.name}")

        # Incremental sync: skip unchanged, previously successful books.
        # For --import-metadata, also skip books whose annotation file is unchanged
        # and was previously imported (status "metadata-only").
        if args.reprocess:
            unchanged_count = 0
        else:
            pairs, unchanged_count = filter_new_or_changed(
                pairs, sync_state, metadata_only=args.import_metadata)

        if unchanged_count:
            print(f"\n  Skipping {unchanged_count} unchanged, previously processed book(s)")

        if args.limit and len(pairs) > args.limit:
            print(f"  Limiting to first {args.limit} book(s)")
            pairs = pairs[:args.limit]

        if not pairs:
            print("\nNothing new to process (all files unchanged).")
            sys.exit(0)

        if args.dry_run:
            print(f"\nDry run — would process {len(pairs)} book(s):")
            if args.import_only:
                mode = "copy to input/"
            elif args.import_book:
                mode = "copy to input/ and extract"
            elif args.import_metadata:
                mode = "copy annotations to input/pending/"
            else:
                mode = None  # per-book mode determined below
            books = sync_state.get("books", {})
            for kfx, yjr in pairs:
                if mode is None:
                    record = books.get(kfx.stem, {})
                    calibre_path = record.get("calibre_kfx_path")
                    if calibre_path and Path(calibre_path).is_file():
                        print(f"  [via calibre] {kfx.name}")
                    else:
                        print(f"  [extract in-place] {kfx.name}")
                else:
                    print(f"  [{mode}] {kfx.name}")
            sys.exit(0)

        input_dir = script_dir / "input"
        pending_dir = input_dir / "pending"
        failed = []
        drm_flagged = []
        imported = 0

        if args.import_metadata:
            # Copy only .yjr files to input/pending/
            for kfx, yjr in pairs:
                try:
                    dest = import_metadata_only(yjr, pending_dir)
                    print(f"  Copied: {yjr.name} -> {dest}")
                    imported += 1
                    _update_sync_record(sync_state, kfx, yjr, "metadata-only",
                                        local_yjr=dest)
                except OSError as e:
                    print(f"  FAILED to copy {yjr.name}: {e}")
                    failed.append(kfx.name)
            print(f"\nImported {imported} annotation file(s) to {pending_dir}")
            if imported:
                print("Tip: pair these with unlocked book files in input/ for extraction.")
        elif args.import_only:
            # Copy .kfx + .yjr to input/
            for kfx, yjr in pairs:
                try:
                    dest_kfx, dest_yjr = import_pair_to_input(kfx, yjr, input_dir)
                    print(f"  Copied: {kfx.name}, {yjr.name}")
                    imported += 1
                    _update_sync_record(sync_state, kfx, yjr, "imported",
                                        local_kfx=dest_kfx, local_yjr=dest_yjr)
                except OSError as e:
                    print(f"  FAILED to copy {kfx.name}: {e}")
                    failed.append(kfx.name)
            print(f"\nImported {imported} book(s) to {input_dir}")
        elif args.import_book:
            # Copy to input/ then extract
            to_extract = []
            for kfx, yjr in pairs:
                try:
                    dest_kfx, dest_yjr = import_pair_to_input(kfx, yjr, input_dir)
                    print(f"  Copied: {kfx.name}, {yjr.name}")
                    imported += 1
                    _update_sync_record(sync_state, kfx, yjr, "imported",
                                        local_kfx=dest_kfx, local_yjr=dest_yjr)
                    to_extract.append((dest_kfx, dest_yjr))
                except OSError as e:
                    print(f"  FAILED to copy {kfx.name}: {e}")
                    failed.append(kfx.name)
            if to_extract:
                print(f"\nImported {imported} book(s). Extracting highlights...")
                _run_extraction(to_extract, script_dir, output_dir, args,
                                sync_state, failed, drm_flagged)
        else:
            # Default mode: process in-place from Kindle.
            # If a book has a calibre_kfx_path in the sync state, use that
            # instead of the on-device KFX (avoids re-hitting DRM on Kindle).
            books = sync_state.get("books", {})
            kindle_pairs = []
            calibre_pairs = []
            for kfx, yjr in pairs:
                record = books.get(kfx.stem, {})
                calibre_path = record.get("calibre_kfx_path")
                if calibre_path and Path(calibre_path).is_file():
                    calibre_pairs.append((Path(calibre_path), yjr,
                                          record.get("calibre_title"), kfx, yjr))
                else:
                    kindle_pairs.append((kfx, yjr))

            if calibre_pairs:
                print(f"\nProcessing {len(calibre_pairs)} book(s) via Calibre path...")
                for calibre_kfx, yjr, title, orig_kfx, orig_yjr in calibre_pairs:
                    print(f"  {orig_kfx.stem} -> {calibre_kfx}")
                    try:
                        nh, nn = process_pair(calibre_kfx, yjr, script_dir, output_dir,
                                              quiet=True, title=title,
                                              keep_json=args.keep_json, fmt=args.format)
                        print(f"  -> Done ({nh} highlights, {nn} notes)")
                        record = books.get(orig_kfx.stem, {})
                        record["status"] = "success"
                        record["last_attempt"] = datetime.now(timezone.utc).isoformat()
                        record["error"] = None
                        record["kindle_kfx_path"] = str(orig_kfx)
                        record["kindle_yjr_path"] = str(orig_yjr)
                        try:
                            record["kfx_mtime"] = orig_kfx.stat().st_mtime
                            record["yjr_mtime"] = orig_yjr.stat().st_mtime
                        except OSError:
                            pass
                        if "file_missing" in record:
                            del record["file_missing"]
                        if nh is not None:
                            record["highlights"] = nh
                        if nn is not None:
                            record["notes"] = nn
                        books[orig_kfx.stem] = record
                    except DRMError as e:
                        print(f"  -> DRM-PROTECTED ({e.highlights} highlights, {e.notes} notes)")
                        drm_flagged.append(orig_kfx.name)
                        _update_sync_record(sync_state, orig_kfx, orig_yjr, "drm-flagged",
                                            error="DRM-protected",
                                            highlights=e.highlights, notes=e.notes)
                    except subprocess.CalledProcessError as e:
                        print(f"  -> FAILED (exit code {e.returncode})")
                        failed.append(orig_kfx.name)
                        _update_sync_record(sync_state, orig_kfx, orig_yjr, "failed",
                                            error=f"exit code {e.returncode}")

            if kindle_pairs:
                print(f"\nProcessing {len(kindle_pairs)} book(s) from Kindle...")
                _run_extraction(kindle_pairs, script_dir, output_dir, args,
                                sync_state, failed, drm_flagged)
            elif not calibre_pairs:
                print("\nNo books to process.")


        # Summary
        print(f"\n{'='*60}")
        processed = len(pairs) - len(failed) - len(drm_flagged)
        print(f"Processed: {processed}", end="")
        if unchanged_count:
            print(f"  Unchanged: {unchanged_count}", end="")
        if failed:
            print(f"  Failed: {len(failed)}", end="")
        if drm_flagged:
            print(f"  DRM-protected: {len(drm_flagged)}", end="")
        print()

        if drm_flagged:
            print("\nDRM-protected books:")
            for name in drm_flagged:
                print(f"  - {name}")
            print("Tip: use --import-metadata to copy annotations, then pair "
                  "with unlocked book files.")

        if failed:
            print("\nFailed:")
            for name in failed:
                print(f"  - {name}")

        save_sync_state(script_dir, sync_state)

        if failed:
            sys.exit(1)
        sys.exit(0)

    if args.kfx_file and args.yjr_file:
        # Single-pair mode
        if not args.kfx_file.is_file():
            parser.error(f"Book file not found: {args.kfx_file}")
        if not args.yjr_file.is_file():
            parser.error(f"Annotation file not found: {args.yjr_file}")

        try:
            nh, nn = process_pair(args.kfx_file, args.yjr_file, script_dir, output_dir,
                                  quiet=args.quiet, title=args.title,
                                  keep_json=args.keep_json, fmt=args.format)
            print(f"Done ({nh} highlights, {nn} notes)")
        except DRMError as e:
            print(f"Error: {args.kfx_file.name} is DRM-protected and cannot be processed.")
            if e.highlights or e.notes:
                print(f"  ({e.highlights} highlights, {e.notes} notes found in annotations)")
            print("Tip: use Calibre to create an unlocked copy, then try again.")
            sys.exit(1)

    else:
        # Bulk mode — scan input/ for paired files
        # Note: bulk mode uses its own processing loop rather than _run_extraction()
        # because it doesn't interact with sync state (which is Kindle-specific)
        # and has its own --skip-existing filtering logic.
        input_dir = script_dir / "input"
        if not input_dir.is_dir():
            print(f"Input directory not found: {input_dir}")
            sys.exit(1)

        pairs = find_pairs(input_dir)
        if not pairs:
            print("No paired book/annotation files found in input/")
            sys.exit(1)

        print(f"Found {len(pairs)} book(s) to process:\n")
        for kfx, yjr in pairs:
            print(f"  {kfx.name}")

        # Filter out already-processed books if requested
        to_process = []
        skipped = []
        ext_map = {"html": ".highlights.html", "md": ".highlights.md",
                   "json": ".highlights.json", "csv": ".highlights.csv"}
        ext = ext_map[args.format]
        for kfx, yjr in pairs:
            if args.skip_existing:
                output_file = output_dir / kfx.with_suffix(ext).name
                if output_file.exists():
                    skipped.append(kfx.name)
                    continue
            to_process.append((kfx, yjr))

        if skipped:
            print(f"  Skipping {len(skipped)} already-processed book(s)")

        if not to_process:
            print("Nothing to process (all skipped).")
            sys.exit(0)

        # Apply --limit
        if args.limit and len(to_process) > args.limit:
            print(f"  Limiting to first {args.limit} book(s)")
            to_process = to_process[:args.limit]

        # Apply --dry-run
        if args.dry_run:
            print(f"\nDry run — would process {len(to_process)} book(s):")
            for kfx, yjr in to_process:
                print(f"  [extract] {kfx.name}")
            sys.exit(0)

        jobs = args.jobs if args.jobs >= 1 else (os.cpu_count() or 1)
        failed = []
        drm_flagged = []

        if jobs == 1:
            # Sequential mode — keeps familiar progress output
            for i, (kfx, yjr) in enumerate(to_process, 1):
                print(f"\n{'='*60}")
                print(f"[{i}/{len(to_process)}] Processing: {kfx.stem}")
                print(f"{'='*60}")
                try:
                    nh, nn = process_pair(kfx, yjr, script_dir, output_dir,
                                          quiet=args.quiet, keep_json=args.keep_json,
                                          fmt=args.format)
                    print(f"  -> Done ({nh} highlights, {nn} notes)")
                except DRMError as e:
                    print(f"  -> DRM-PROTECTED ({e.highlights} highlights, {e.notes} notes)")
                    drm_flagged.append(kfx.name)
                except subprocess.CalledProcessError as e:
                    print(f"  -> FAILED (exit code {e.returncode})")
                    failed.append(kfx.name)
        else:
            # Parallel mode
            print(f"\nProcessing {len(to_process)} book(s) with {jobs} workers...")
            with ProcessPoolExecutor(max_workers=jobs) as pool:
                futures = {
                    pool.submit(process_pair, kfx, yjr, script_dir, output_dir,
                                quiet=True, keep_json=args.keep_json,
                                fmt=args.format): kfx
                    for kfx, yjr in to_process
                }
                for future in as_completed(futures):
                    kfx = futures[future]
                    try:
                        nh, nn = future.result()
                        print(f"  Done: {kfx.stem} ({nh} highlights, {nn} notes)")
                    except DRMError as e:
                        print(f"  DRM-PROTECTED: {kfx.stem} ({e.highlights} highlights, {e.notes} notes)")
                        drm_flagged.append(kfx.name)
                    except subprocess.CalledProcessError as e:
                        print(f"  FAILED: {kfx.stem} (exit code {e.returncode})")
                        failed.append(kfx.name)

        print(f"\n{'='*60}")
        processed = len(to_process) - len(failed) - len(drm_flagged)
        total = len(pairs)
        print(f"Processed {processed}/{total} books successfully.", end="")
        if skipped:
            print(f" ({len(skipped)} skipped)", end="")
        print()
        if drm_flagged:
            print(f"\nDRM-protected:")
            for name in drm_flagged:
                print(f"  - {name}")
            print("Tip: use --kindle --import-metadata to copy annotations, "
                  "then pair with unlocked book files.")
        if failed:
            print(f"Failed:")
            for name in failed:
                print(f"  - {name}")
            sys.exit(1)


if __name__ == "__main__":
    main()
