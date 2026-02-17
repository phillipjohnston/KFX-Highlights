# KindleUnpack (Vendored Subset)

This directory contains a subset of files from the [KindleUnpack](https://github.com/kevinhendricks/KindleUnpack) project, vendored for use in AZW3 highlight extraction.

## Purpose

These files are used to properly process KF8 (AZW3) format ebooks by reconstructing the "Flow 0" content (assembled text after skeleton/fragment/FDST processing) that annotation positions reference.

## Files Included

- `mobi_k8proc.py` - K8Processor class for KF8 skeleton/fragment/FDST processing
- `mobi_header.py` - MobiHeader class for MOBI header parsing
- `mobi_sectioner.py` - Sectionizer class for section handling
- `mobi_index.py` - MobiIndex class for index data processing
- `mobi_uncompress.py` - Decompression utilities (HuffcdicReader, PalmdocReader)
- `mobi_utils.py` - Utility functions
- `compatibility_utils.py` - Python 2/3 compatibility layer
- `unipath.py` - Path utilities

## License

These files are licensed under the GNU General Public License v3 (GPL-3.0), as specified in `LICENSE.txt`.

Original project: https://github.com/kevinhendricks/KindleUnpack

## Changes

**Modified imports**: Relative imports (e.g., `from .mobi_utils`) have been converted to absolute imports (e.g., `from mobi_utils`) to allow these modules to be imported without being part of a package structure. This is the only modification from the upstream source.

## Version

Files copied from KindleUnpack commit/version: (as of 2026-02-16)
