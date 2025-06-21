#!/usr/bin/env python3
"""Convert a YJR annotations file and extract highlights from a KFX book."""

import subprocess
import sys
from pathlib import Path


def main():
    if len(sys.argv) != 3:
        print("Usage: python extract_highlights.py <book.kfx> <annotations.yjr>")
        sys.exit(1)

    kfx_file = Path(sys.argv[1])
    yjr_file = Path(sys.argv[2])

    if not kfx_file.is_file():
        print(f"KFX file not found: {kfx_file}")
        sys.exit(1)
    if not yjr_file.is_file():
        print(f"YJR file not found: {yjr_file}")
        sys.exit(1)

    script_dir = Path(__file__).parent

    # Convert YJR to JSON using krds.py
    krds_script = script_dir / "krds.py"
    subprocess.run([sys.executable, str(krds_script), str(yjr_file)], check=True)

    json_file = yjr_file.with_suffix(yjr_file.suffix + ".json")

    # Extract highlights using the generated JSON and KFX file
    extract_script = script_dir / "extract_highlights_kfxlib.py"
    subprocess.run(
        [sys.executable, str(extract_script), str(json_file), str(kfx_file)],
        check=True,
    )


if __name__ == "__main__":
    main()
