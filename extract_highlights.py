#!/usr/bin/env python3
"""Extract highlights and notes from Kindle KFX books using synced annotation data."""

import argparse
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


KNOWN_CONFIG_KEYS = {
    "format": {"type": str, "choices": ["html", "md", "json", "csv"]},
    "output_dir": {"type": str},
    "quiet": {"type": bool},
    "keep_json": {"type": bool},
    "skip_existing": {"type": bool},
    "jobs": {"type": int},
    "citation_style": {"type": str, "choices": ["apa"]},
    "theme": {"type": str, "choices": ["default"]},
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
    subprocess.run(extract_cmd, check=True)

    if not keep_json and json_file.exists():
        json_file.unlink()


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
        elif key in ("quiet", "keep_json", "skip_existing", "jobs"):
            argparse_defaults[key] = value
        # citation_style and theme are reserved for future use

    if argparse_defaults:
        parser.set_defaults(**argparse_defaults)

    args = parser.parse_args()

    if config and not args.quiet:
        print(f"Loaded config from {script_dir / 'config.yaml'}")

    output_dir = args.output_dir or (script_dir / "output")

    # If one positional arg is given without the other, that's an error
    if (args.kfx_file is None) != (args.yjr_file is None):
        parser.error("provide both BOOK.kfx and ANNOTATIONS.yjr, or neither for bulk mode")

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

        jobs = args.jobs if args.jobs >= 1 else (os.cpu_count() or 1)
        failed = []

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
                    except subprocess.CalledProcessError as e:
                        print(f"  FAILED: {kfx.stem} (exit code {e.returncode})")
                        failed.append(kfx.name)

        print(f"\n{'='*60}")
        processed = len(to_process) - len(failed)
        total = len(pairs)
        print(f"Processed {processed}/{total} books successfully.", end="")
        if skipped:
            print(f" ({len(skipped)} skipped)", end="")
        print()
        if failed:
            print(f"Failed:")
            for name in failed:
                print(f"  - {name}")
            sys.exit(1)


if __name__ == "__main__":
    main()
