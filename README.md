# KFX Highlights
Uses Synced Kindle Annotations to Make Highlights File

Most of the documents I read on my Kindle are sent via "Send to Kindle" so that I can read them on other devices. However, one of the issues I've noticed is that there's no way to extract synced highlights. Here's how I've attempted to do it:
1. When I'm ready to export the highlights, I make sure my Kindle has them synced.
2. I connect my Kindle to my laptop and pull two files from the documents/downloads folder: the KFX file for the document and the yjr file, which is in the SDR folder for the document.
3. I move those to the folder below and run the extract_highlights.py script.

jhowell released a [KRDS Parser](https://www.mobileread.com/forums/showthread.php?t=322172). It's located [here](https://github.com/K-R-D-S/KRDS). 


## Setup

Create a virtual environment and install the required dependencies:

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Activate the virtual environment before each use:

```
source .venv/bin/activate
```

## Usage

### Bulk mode (all books at once)

Place all `.kfx` and `.yjr` files in the `input/` directory, then run:

```
python extract_highlights.py
```

This scans `input/` and automatically pairs `.kfx` files with their corresponding `.yjr` annotation files by matching filenames. Each pair is processed sequentially, and failures are reported at the end without aborting the whole run.

### Single book

```
python extract_highlights.py <book.kfx> <annotations.yjr>
```

Both modes will:
1. Convert the YJR file to JSON using `krds.py`.
2. Call `extract_highlights_kfxlib.py` with the generated JSON and KFX file to create the HTML highlights file.

Output goes to `output/`.

### Options

| Flag | Description |
|------|-------------|
| `-f {html,md,json,csv}` | Output format (default: html) |
| `-q` / `--quiet` | Suppress per-highlight output, show summary only |
| `--skip-existing` | Skip books whose output file already exists (bulk mode) |
| `--title "My Title"` | Override the book title in output (single-pair mode) |
| `--keep-json` | Keep intermediate JSON files |
| `-j N` / `--jobs N` | Parallel workers (0 = CPU count, default: 1) |
| `-o DIR` | Write output to a custom directory |

### Config file

To avoid repeating the same flags on every run, copy the example config and edit it:

```
cp config.yaml.example config.yaml
```

All supported keys are listed with comments in the example file. CLI flags always override config values. The config file is gitignored since it contains user-specific preferences.
