#!/usr/bin/env python3
"""Extract highlights and notes from Kindle KFX books using synced annotation data."""

import argparse
import subprocess
import sys
from pathlib import Path


def process_pair(kfx_file, yjr_file, script_dir, output_dir, quiet=False):
    """Run the krds + extraction pipeline for a single kfx/yjr pair."""
    output_dir.mkdir(exist_ok=True)

    krds_script = script_dir / "krds.py"
    subprocess.run(
        [sys.executable, str(krds_script), str(yjr_file), "--output-dir", str(output_dir)],
        check=True,
    )

    json_file = output_dir / (yjr_file.name + ".json")

    extract_cmd = [sys.executable, str(script_dir / "extract_highlights_kfxlib.py"),
                   str(json_file), str(kfx_file), "--output-dir", str(output_dir)]
    if quiet:
        extract_cmd.append("--quiet")
    subprocess.run(extract_cmd, check=True)


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

    args = parser.parse_args()

    script_dir = Path(__file__).parent
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
                     quiet=args.quiet)

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

        failed = []
        skipped = []
        for i, (kfx, yjr) in enumerate(pairs, 1):
            print(f"\n{'='*60}")
            print(f"[{i}/{len(pairs)}] Processing: {kfx.stem}")
            print(f"{'='*60}")

            if args.skip_existing:
                output_html = output_dir / kfx.with_suffix(".highlights.html").name
                if output_html.exists():
                    print(f"  -> Skipped (output already exists)")
                    skipped.append(kfx.name)
                    continue

            try:
                process_pair(kfx, yjr, script_dir, output_dir, quiet=args.quiet)
                print(f"  -> Done")
            except subprocess.CalledProcessError as e:
                print(f"  -> FAILED (exit code {e.returncode})")
                failed.append(kfx.name)

        print(f"\n{'='*60}")
        processed = len(pairs) - len(failed) - len(skipped)
        print(f"Processed {processed}/{len(pairs)} books successfully.", end="")
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
