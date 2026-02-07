#!/usr/bin/env python3
"""Convert a YJR annotations file and extract highlights from a KFX book.

Usage:
    Single pair:  python extract_highlights.py <book.kfx> <annotations.yjr>
    Bulk mode:    python extract_highlights.py
        (scans input/ for paired .kfx and .yjr files)
"""

import subprocess
import sys
from pathlib import Path


def process_pair(kfx_file, yjr_file, script_dir, output_dir):
    """Run the krds + extraction pipeline for a single kfx/yjr pair."""
    output_dir.mkdir(exist_ok=True)

    krds_script = script_dir / "krds.py"
    subprocess.run(
        [sys.executable, str(krds_script), str(yjr_file), "--output-dir", str(output_dir)],
        check=True,
    )

    json_file = output_dir / (yjr_file.name + ".json")

    extract_script = script_dir / "extract_highlights_kfxlib.py"
    subprocess.run(
        [sys.executable, str(extract_script), str(json_file), str(kfx_file),
         "--output-dir", str(output_dir)],
        check=True,
    )


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
    script_dir = Path(__file__).parent
    output_dir = script_dir / "output"

    if len(sys.argv) == 3:
        # Single-pair mode
        kfx_file = Path(sys.argv[1])
        yjr_file = Path(sys.argv[2])

        if not kfx_file.is_file():
            print(f"KFX file not found: {kfx_file}")
            sys.exit(1)
        if not yjr_file.is_file():
            print(f"YJR file not found: {yjr_file}")
            sys.exit(1)

        process_pair(kfx_file, yjr_file, script_dir, output_dir)

    elif len(sys.argv) == 1:
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
        for i, (kfx, yjr) in enumerate(pairs, 1):
            print(f"\n{'='*60}")
            print(f"[{i}/{len(pairs)}] Processing: {kfx.stem}")
            print(f"{'='*60}")
            try:
                process_pair(kfx, yjr, script_dir, output_dir)
                print(f"  -> Done")
            except subprocess.CalledProcessError as e:
                print(f"  -> FAILED (exit code {e.returncode})")
                failed.append(kfx.name)

        print(f"\n{'='*60}")
        print(f"Processed {len(pairs) - len(failed)}/{len(pairs)} books successfully.")
        if failed:
            print(f"Failed:")
            for name in failed:
                print(f"  - {name}")
            sys.exit(1)

    else:
        print("Usage:")
        print("  Single pair:  python extract_highlights.py <book.kfx> <annotations.yjr>")
        print("  Bulk mode:    python extract_highlights.py")
        sys.exit(1)


if __name__ == "__main__":
    main()
