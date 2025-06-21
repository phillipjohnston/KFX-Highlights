# KFX Highlights
Uses Synced Kindle Annotations to Make Highlights File

## Usage

To convert a `.yjr` annotations file and extract highlights from a `.kfx` book in one step run:

```
python extract_highlights.py <book.kfx> <annotations.yjr>
```

This will:
1. Convert the YJR file to JSON using `krds.py`.
2. Call `extract_highlights_kfxlib.py` with the generated JSON and KFX file to create the HTML highlights file.
