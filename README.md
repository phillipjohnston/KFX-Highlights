# KFX Highlights
Uses Synced Kindle Annotations to Make Highlights File

Most of the documents I read on my Kindle are sent via "Send to Kindle" so that I can read them on other devices. However, one of the issues I've noticed is that there's no way to extract synced highlights. Here's how I've attempted to do it:
1. When I'm ready to export the highlights, I make sure my Kindle has them synced.
2. I connect my Kindle to my laptop and pull two files from the documents/downloads folder: the KFX file for the document and the yjr file, which is in the SDR folder for the document.
3. I move those to the folder below and run the extract_highlights.py script.

jhowell released a [KRDS Parser](https://www.mobileread.com/forums/showthread.php?t=322172). It's located [here](https://github.com/K-R-D-S/KRDS). 


Dependencies:
pip install pillow
pip install pypdf
pip install lxml
pip install beautifulsoup4

## Usage

To convert a `.yjr` annotations file and extract highlights from a `.kfx` book in one step run:

```
python extract_highlights.py <book.kfx> <annotations.yjr>
```

This will:
1. Convert the YJR file to JSON using `krds.py`.
2. Call `extract_highlights_kfxlib.py` with the generated JSON and KFX file to create the HTML highlights file.
