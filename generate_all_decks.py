#!/usr/bin/env python3
"""
generate_all_decks.py

Batch-processes every deck_*.csv in decks/, running the full TTS image pipeline:
  1. generate_card_sheet.py  → build/<prefix>_image.tex         (fronts)
  2. pdflatex                → build/<prefix>_image.pdf
  3. ImageMagick convert     → build/<prefix>_image.png
  4. generate_card_sheet.py  → build/<prefix>_back_image.tex    (backs, --repeat)
  5. pdflatex                → build/<prefix>_back_image.pdf
  6. ImageMagick convert     → build/<prefix>_back_image.png

Adding a new deck only requires adding deck_<name>.csv to decks/ and
generating the card files (generateCards.py creates back_<name>.tex in build/).
"""

import argparse
import math
import subprocess
import sys
from pathlib import Path
import os

WORKSPACE = Path(__file__).parent
BUILD = WORKSPACE / "build"
DECKS_DIR = WORKSPACE / "decks"

COLS = 7
ROWS = 4


def run(cmd, cwd=None, label=""):
    display = label or " ".join(str(c) for c in cmd)
    print(f"  $ {display}")
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        if result.stdout:
            print(result.stdout[-3000:])
        if result.stderr:
            print(result.stderr[-3000:])
        sys.exit(f"FAILED: {display}")


def pdf_to_png(stem):
    run(
        ["convert", "-density", "150", f"{stem}.pdf", "-compress", "lzw", f"{stem}.png"],
        cwd=BUILD,
        label=f"convert {stem}.pdf → {stem}.png",
    )


def process_deck(prefix):
    print(f"\n=== {prefix} ===")

    front_tex = f"{prefix}_image.tex"
    back_tex = f"{prefix}_back_image.tex"
    has_back = os.path.exists(f"build/back_{prefix}.tex")

    cols = COLS
    rows = ROWS

    deck_csv = DECKS_DIR / f"deck_{prefix}.csv"
    num_rows = sum(1 for line in deck_csv.read_text().splitlines() if line.strip())
    num_cards = num_rows + 1 if has_back else num_rows
    if num_cards > cols * rows:
        rows = math.ceil(num_cards / cols)
        print(f"  Expanding to {rows} rows to fit {num_cards} cards")

    deck_run = [sys.executable, "generate_card_sheet.py",
         f"--csv=decks/deck_{prefix}.csv",
         f"--output=build/{front_tex}",
         f"--cols={cols}", f"--rows={rows}"]
    if has_back:
        deck_run.append("--add_back")
        deck_run.append("--back_color=purple")
        deck_run.append("--back_text=SHUFFLE")
    run(
        deck_run,
        cwd=WORKSPACE,
        label=f"generate_card_sheet  deck_{prefix}.csv → {front_tex}",
    )
    run(["pdflatex", "-interaction=nonstopmode", front_tex], cwd=BUILD,
        label=f"pdflatex {front_tex}")
    pdf_to_png(f"{prefix}_image")

    if has_back:
        print(f"   creating back {back_tex}")
        run(
            [sys.executable, "generate_card_sheet.py",
            f"--csv=back_{prefix}.tex",
            f"--output=build/{back_tex}",
            "--repeat", "--add_back", f"--cols={COLS}", f"--rows={ROWS}"],
            cwd=WORKSPACE,
            label=f"generate_card_sheet  back_{prefix}.tex → {back_tex}",
        )
        run(["pdflatex", "-interaction=nonstopmode", back_tex], cwd=BUILD,
            label=f"pdflatex {back_tex}")
        pdf_to_png(f"{prefix}_back_image")

    print(f"  Done: {prefix}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate TTS card sheet images for all (or specified) decks."
    )
    parser.add_argument(
        "decks", nargs="*",
        help="Deck prefixes to process (e.g. collective_adam). "
             "Omit to process all deck_*.csv files in decks/.",
    )
    args = parser.parse_args()

    if args.decks:
        prefixes = args.decks
    else:
        csv_files = sorted(DECKS_DIR.glob("deck_*.csv"))
        if not csv_files:
            sys.exit(f"No deck_*.csv files found in {DECKS_DIR}")
        prefixes = [f.stem[len("deck_"):] for f in csv_files]

    print(f"Processing {len(prefixes)} deck(s): {prefixes}")

    failed = []
    for prefix in prefixes:
        try:
            process_deck(prefix)
        except SystemExit as e:
            print(f"  ERROR: {e}")
            failed.append(prefix)

    print("\n" + "=" * 40)
    if failed:
        print(f"Finished with errors in: {failed}")
        sys.exit(1)
    else:
        print(f"All {len(prefixes)} deck(s) processed successfully.")


if __name__ == "__main__":
    main()
