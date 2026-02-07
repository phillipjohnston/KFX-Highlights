#!/usr/bin/env python3
"""Extract highlights and notes from Kindle KFX books using synced annotation data."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


class DRMError(Exception):
    """Raised when extraction fails due to DRM protection."""
    pass


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


def process_pair(kfx_file, yjr_file, script_dir, output_dir, quiet=False,
                 title=None, keep_json=False, fmt="html"):
    """Run the krds + extraction pipeline for a single kfx/yjr pair."""
    output_dir.mkdir(exist_ok=True)

    krds_script = script_dir / "krds.py"
    subprocess.run(
        [sys.executable, str(krds_script), str(yjr_file), "--output-dir", str(output_dir)],
        check=True,
    )

    json_file = output_dir / (yjr_file.name + ".json")

    extract_cmd = [sys.executable, str(script_dir / "extract_highlights_kfxlib.py"),
                   str(json_file), str(kfx_file), "--output-dir", str(output_dir),
                   "--format", fmt]
    if quiet:
        extract_cmd.append("--quiet")
    if title:
        extract_cmd.extend(["--title", title])

    result = subprocess.run(extract_cmd, capture_output=True, text=True)
    if result.returncode == 0:
        if result.stdout:
            print(result.stdout, end="")
    else:
        # kfxlib raises exceptions containing "DRM" for encrypted content
        if "DRM" in (result.stderr or ""):
            if result.stderr:
                print(result.stderr, end="", file=sys.stderr)
            raise DRMError(f"DRM-protected: {kfx_file.name}")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        raise subprocess.CalledProcessError(result.returncode, extract_cmd)

    if not keep_json and json_file.exists():
        json_file.unlink()


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
    """Scan a mounted Kindle for .kfx / .yjr pairs.

    Looks in documents/ and documents/Downloads/. For each .kfx file, checks
    the sibling .sdr/ folder for a matching .yjr annotation file.
    """
    docs = validate_kindle_path(kindle_path)
    scan_dirs = [docs]
    downloads = docs / "Downloads"
    if downloads.is_dir():
        scan_dirs.append(downloads)

    pairs = []
    seen_stems = set()

    for scan_dir in scan_dirs:
        kfx_files = sorted(scan_dir.glob("*.kfx"))
        for kfx in kfx_files:
            if kfx.stem in seen_stems:
                continue
            seen_stems.add(kfx.stem)

            sdr_dir = scan_dir / f"{kfx.stem}.sdr"
            if not sdr_dir.is_dir():
                continue

            yjr_matches = sorted(sdr_dir.glob("*.yjr"))
            # Filter to those whose name starts with the kfx stem
            yjr_matches = [y for y in yjr_matches if y.stem.startswith(kfx.stem)]

            if len(yjr_matches) == 1:
                pairs.append((kfx, yjr_matches[0]))
            elif len(yjr_matches) > 1:
                # Take the most recently modified one
                yjr_matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                pairs.append((kfx, yjr_matches[0]))
            # else: no annotations for this book, skip silently

    return pairs


def filter_new_or_changed(pairs, sync_state):
    """Filter out books where both files are unchanged and previously succeeded.

    Returns (filtered_pairs, skipped_count).
    """
    books = sync_state.get("books", {})
    filtered = []
    skipped = 0

    for kfx, yjr in pairs:
        stem = kfx.stem
        record = books.get(stem)
        if record and record.get("status") == "success":
            try:
                kfx_mtime = kfx.stat().st_mtime
                yjr_mtime = yjr.stat().st_mtime
            except OSError:
                filtered.append((kfx, yjr))
                continue
            # Use tolerance for mtime comparison — Kindle uses FAT32
            # (2-second resolution) while macOS uses APFS (nanoseconds),
            # and float precision can vary across JSON round-trips.
            if (abs(kfx_mtime - record.get("kfx_mtime", 0)) < 2.0
                    and abs(yjr_mtime - record.get("yjr_mtime", 0)) < 2.0):
                skipped += 1
                continue
        filtered.append((kfx, yjr))

    return filtered, skipped


def import_pair_to_input(kfx, yjr, input_dir):
    """Copy both .kfx and .yjr files to input/. Returns (dest_kfx, dest_yjr)."""
    input_dir.mkdir(parents=True, exist_ok=True)
    dest_kfx = input_dir / kfx.name
    dest_yjr = input_dir / yjr.name
    shutil.copy2(kfx, dest_kfx)
    shutil.copy2(yjr, dest_yjr)
    return dest_kfx, dest_yjr


def import_metadata_only(yjr, pending_dir):
    """Copy just the .yjr file to input/pending/. Returns dest path."""
    pending_dir.mkdir(parents=True, exist_ok=True)
    dest_yjr = pending_dir / yjr.name
    shutil.copy2(yjr, dest_yjr)
    return dest_yjr


def _update_sync_record(sync_state, kfx, yjr, status, error=None,
                        local_kfx=None, local_yjr=None):
    """Create or update a book's entry in the sync state.

    Preserves existing kindle_*_path and *_mtime fields if the kfx/yjr
    being passed are local copies (e.g. after --import-book copies files
    to input/ and then runs extraction on the copies).
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
                process_pair(kfx, yjr, script_dir, output_dir,
                             quiet=args.quiet, keep_json=args.keep_json,
                             fmt=args.format)
                print(f"  -> Done")
                _update_sync_record(sync_state, kfx, yjr, "success")
            except DRMError:
                print(f"  -> DRM-PROTECTED")
                drm_flagged.append(kfx.name)
                _update_sync_record(sync_state, kfx, yjr, "drm-flagged",
                                    error="DRM-protected")
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
                    future.result()
                    print(f"  Done: {kfx.stem}")
                    _update_sync_record(sync_state, kfx, yjr, "success")
                except DRMError:
                    print(f"  DRM-PROTECTED: {kfx.stem}")
                    drm_flagged.append(kfx.name)
                    _update_sync_record(sync_state, kfx, yjr, "drm-flagged",
                                        error="DRM-protected")
                except subprocess.CalledProcessError as e:
                    print(f"  FAILED: {kfx.stem} (exit code {e.returncode})")
                    failed.append(kfx.name)
                    _update_sync_record(sync_state, kfx, yjr, "failed",
                                        error=f"exit code {e.returncode}")


def find_pairs(input_dir):
    """Match .kfx files to .yjr files in input_dir.

    The Kindle naming convention places the .yjr filename as an extension
    of the .kfx stem (with an appended annotation hash). So we pair a .yjr
    file with a .kfx file when the .yjr name starts with the .kfx stem.
    """
    kfx_files = sorted(input_dir.glob("*.kfx"))
    yjr_files = sorted(input_dir.glob("*.yjr"))

    pairs = []
    for kfx in kfx_files:
        matches = [y for y in yjr_files if y.stem.startswith(kfx.stem)]
        if len(matches) == 1:
            pairs.append((kfx, matches[0]))
        elif len(matches) > 1:
            print(f"Warning: multiple .yjr files match {kfx.name}, skipping:")
            for m in matches:
                print(f"  - {m.name}")
        else:
            print(f"Warning: no .yjr file found for {kfx.name}, skipping")

    unmatched_yjr = set(yjr_files) - {y for _, y in pairs}
    for y in sorted(unmatched_yjr):
        print(f"Warning: no .kfx file found for {y.name}, skipping")

    return pairs


def main():
    parser = argparse.ArgumentParser(
        description="Extract highlights and notes from Kindle KFX books.",
        epilog="""\
examples:
  %(prog)s                              Process all paired .kfx/.yjr files in input/
  %(prog)s input/book.kfx input/book.yjr   Process a single book/annotation pair
  %(prog)s -o results/ book.kfx book.yjr   Write output to a custom directory
  %(prog)s --kindle /Volumes/Kindle        Process directly from a connected Kindle
  %(prog)s --kindle /Volumes/Kindle --import-only   Copy files to input/ only
  %(prog)s --kindle /Volumes/Kindle --dry-run       Preview what would be done

In bulk mode, .kfx and .yjr files are paired by filename: the .yjr name
must start with the .kfx stem (Kindle's default naming convention).""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "kfx_file", nargs="?", type=Path, metavar="BOOK.kfx",
        help="path to the KFX book file",
    )
    parser.add_argument(
        "yjr_file", nargs="?", type=Path, metavar="ANNOTATIONS.yjr",
        help="path to the YJR annotation file",
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
        "--kindle", type=Path, default=None, metavar="PATH",
        help="path to mounted Kindle device (e.g. /Volumes/Kindle)",
    )
    import_group = parser.add_mutually_exclusive_group()
    import_group.add_argument(
        "--import-only", action="store_true",
        help="copy .kfx + .yjr from Kindle to input/ without extracting",
    )
    import_group.add_argument(
        "--import-book", action="store_true",
        help="copy .kfx + .yjr to input/ and run extraction",
    )
    import_group.add_argument(
        "--import-metadata", action="store_true",
        help="copy only .yjr to input/pending/ (for DRM books)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="preview what would be done without making changes",
    )
    parser.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="process at most N books (for testing)",
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
        elif key in ("quiet", "keep_json", "skip_existing", "jobs"):
            argparse_defaults[key] = value
        # citation_style and theme are reserved for future use

    if argparse_defaults:
        parser.set_defaults(**argparse_defaults)

    args = parser.parse_args()

    if config and not args.quiet:
        print(f"Loaded config from {script_dir / 'config.yaml'}")

    output_dir = args.output_dir or (script_dir / "output")

    # Validate flag combinations
    if (args.import_only or args.import_book or args.import_metadata) and not args.kindle:
        parser.error("--import-only, --import-book, and --import-metadata require --kindle")

    if args.kindle and (args.kfx_file or args.yjr_file):
        parser.error("--kindle cannot be combined with positional kfx/yjr arguments")

    # If one positional arg is given without the other, that's an error
    if (args.kfx_file is None) != (args.yjr_file is None):
        parser.error("provide both BOOK.kfx and ANNOTATIONS.yjr, or neither for bulk mode")

    # --- Kindle device mode ---
    if args.kindle:
        sync_state = load_sync_state(script_dir)
        pairs = find_kindle_pairs(args.kindle)

        if not pairs:
            print("No paired .kfx/.yjr files found on Kindle.")
            sys.exit(0)

        print(f"Found {len(pairs)} book(s) on Kindle:\n")
        for kfx, yjr in pairs:
            print(f"  {kfx.name}")

        # Incremental sync: skip unchanged, previously successful books
        pairs, unchanged_count = filter_new_or_changed(pairs, sync_state)

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
            mode = "extract in-place"
            if args.import_only:
                mode = "copy to input/"
            elif args.import_book:
                mode = "copy to input/ and extract"
            elif args.import_metadata:
                mode = "copy .yjr to input/pending/"
            for kfx, yjr in pairs:
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
                print("Tip: pair these with unlocked .kfx files in input/ for extraction.")
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
            # Default mode: process in-place from Kindle
            print(f"\nProcessing {len(pairs)} book(s) from Kindle...")
            _run_extraction(pairs, script_dir, output_dir, args,
                            sync_state, failed, drm_flagged)

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
                  "with unlocked .kfx files.")

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
            parser.error(f"KFX file not found: {args.kfx_file}")
        if not args.yjr_file.is_file():
            parser.error(f"YJR file not found: {args.yjr_file}")

        process_pair(args.kfx_file, args.yjr_file, script_dir, output_dir,
                     quiet=args.quiet, title=args.title,
                     keep_json=args.keep_json, fmt=args.format)

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
            print("No paired .kfx/.yjr files found in input/")
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
                    process_pair(kfx, yjr, script_dir, output_dir,
                                 quiet=args.quiet, keep_json=args.keep_json,
                                 fmt=args.format)
                    print(f"  -> Done")
                except DRMError:
                    print(f"  -> DRM-PROTECTED")
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
                        future.result()
                        print(f"  Done: {kfx.stem}")
                    except DRMError:
                        print(f"  DRM-PROTECTED: {kfx.stem}")
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
                  "then pair with unlocked .kfx files.")
        if failed:
            print(f"Failed:")
            for name in failed:
                print(f"  - {name}")
            sys.exit(1)


if __name__ == "__main__":
    main()
