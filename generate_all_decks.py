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

It also renders the annotated rules-reference cards (build/rules_*_doc.tex,
written by generateCards.py) to trimmed PNGs in RulesImages/.
"""

import argparse
import math
import subprocess
import sys
from pathlib import Path
import os

from generate_card_images import generate_card_images

WORKSPACE = Path(__file__).parent
BUILD = WORKSPACE / "build"
DECKS_DIR = WORKSPACE / "decks"
TTS_IMAGES = WORKSPACE / "TTSImages"
CARD_IMAGES = WORKSPACE / "CardImages"
RULES_IMAGES = WORKSPACE / "RulesImages"
INDIVIDUAL_CARDS_CSV = WORKSPACE / "individual_cards.csv"

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


def pdf_to_png(stem, out_dir=TTS_IMAGES, trim=False, out_name=None, background=None):
    out_dir.mkdir(exist_ok=True)
    name = out_name or stem
    out = out_dir / f"{name}.png"
    cmd = ["convert", "-density", "150", f"{stem}.pdf"]
    if background:
        # a PDF page renders transparent; flatten it onto a solid colour first so
        # any later trim crops against that colour rather than the alpha channel
        cmd += ["-background", background, "-alpha", "remove", "-alpha", "off"]
    if trim:
        # crop the a4 page down to the card + its callouts
        cmd += ["-trim", "+repage"]
    cmd += ["-compress", "lzw", str(out)]
    run(cmd, cwd=BUILD, label=f"convert {stem}.pdf → {out.relative_to(WORKSPACE)}")


def process_deck(prefix):
    print(f"\n=== {prefix} ===")

    front_tex = f"{prefix}_image.tex"
    back_tex = f"{prefix}_back_image.tex"
    has_back = os.path.exists(f"build/back_{prefix}.tex")

    # deck_terrain_* list terrain tiles; every other deck lists weapon/pilot/etc.
    # cards. The type tells generate_card_sheet which build/ subfolder to prepend.
    deck_type = "terrain" if prefix == "terrain" or prefix.startswith("terrain_") else "card"

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
         f"--type={deck_type}",
         f"--cols={cols}", f"--rows={rows}"]
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
            "--repeat", f"--cols={COLS}", f"--rows={ROWS}"],
            cwd=WORKSPACE,
            label=f"generate_card_sheet  back_{prefix}.tex → {back_tex}",
        )
        run(["pdflatex", "-interaction=nonstopmode", back_tex], cwd=BUILD,
            label=f"pdflatex {back_tex}")
        pdf_to_png(f"{prefix}_back_image")

    print(f"  Done: {prefix}")


def process_individual_cards():
    """Renders standalone PNGs for the cards listed in individual_cards.csv, if present."""
    if not INDIVIDUAL_CARDS_CSV.exists():
        print(f"\n(no {INDIVIDUAL_CARDS_CSV.relative_to(WORKSPACE)} found - skipping individual card images)")
        return

    print(f"\n=== individual card images ({INDIVIDUAL_CARDS_CSV.name}) ===")
    generate_card_images(
        csv_path=INDIVIDUAL_CARDS_CSV,
        output_dir=CARD_IMAGES,
        density=150,
        sheet_name="individual_cards",
    )


def process_rules_images():
    """Renders a standalone trimmed PNG for each annotated rules-reference card.

    generateCards.py writes one build/rules_<type>_doc.tex per card type (melee
    weapon, ranged weapon, drone, pilot, frame); each is compiled and cropped to
    its own PNG in RulesImages/."""
    docs = sorted(BUILD.glob("rules_*_doc.tex"))
    if not docs:
        print("\n(no build/rules_*_doc.tex found - run generateCards.py first; skipping rules images)")
        return

    print(f"\n=== rules reference images ({len(docs)}) ===")
    for doc in docs:
        stem = doc.stem                     # e.g. rules_frame_doc
        img_name = stem[:-len("_doc")]      # e.g. rules_frame
        run(["pdflatex", "-interaction=nonstopmode", doc.name], cwd=BUILD,
            label=f"pdflatex {doc.name}")
        pdf_to_png(stem, out_dir=RULES_IMAGES, trim=True, out_name=img_name,
                   background="white")


def main():
    parser = argparse.ArgumentParser(
        description="Generate TTS card sheet images for all (or specified) decks."
    )
    parser.add_argument(
        "decks", nargs="*",
        help="Deck prefixes to process (e.g. collective_adam). "
             "Omit to process all deck_*.csv files in decks/.",
    )
    parser.add_argument(
        "--skip-individual", action="store_true",
        help="Skip rendering individual card images from individual_cards.csv.",
    )
    parser.add_argument(
        "--skip-rules", action="store_true",
        help="Skip rendering the annotated rules-reference card images.",
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

    if not args.skip_individual:
        try:
            process_individual_cards()
        except SystemExit as e:
            print(f"  ERROR: {e}")
            failed.append("individual_cards")

    if not args.skip_rules:
        try:
            process_rules_images()
        except SystemExit as e:
            print(f"  ERROR: {e}")
            failed.append("rules_images")

    print("\n" + "=" * 40)
    if failed:
        print(f"Finished with errors in: {failed}")
        sys.exit(1)
    else:
        print(f"All {len(prefixes)} deck(s) processed successfully.")


if __name__ == "__main__":
    main()
