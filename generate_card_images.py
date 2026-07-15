#!/usr/bin/env python3
"""
generate_card_images.py

Renders individual cards to standalone PNG images (e.g. for previews, a
rulebook, or a website) rather than a TTS grid sheet.

Reads a single-column CSV listing card .tex filenames -- the same format as
the deck CSVs in decks/, one build/card_*.tex (or frame_*/back_*/terrain_*)
filename per line, no header. Each card is placed on its own page via
generate_card_sheet.py --cols 1 --rows 1, which takes care of pulling in
build/card_macros.tex (the icon/ability macros) alongside the card itself.
The resulting multi-page PDF is compiled once, then each page is rasterised
to its own PNG named after the card.

Usage:
    python generate_card_images.py --csv decks/individual_cards.csv
    python generate_card_images.py --csv decks/individual_cards.csv --output-dir CardImages --density 300
"""

import argparse
import csv
import re
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).parent
BUILD = WORKSPACE / "build"

CARD_NAME_PREFIXES = ("card_", "frame_", "back_", "terrain_")


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


def read_card_list(csv_path: Path) -> list[str]:
    """Read a single-column CSV (no header) and return a list of filenames."""
    if not csv_path.is_file():
        sys.exit(f"Error: CSV file not found: {csv_path}")

    cards = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        for row in reader:
            if row and row[0].strip():
                cards.append(row[0].strip())

    if not cards:
        sys.exit("Error: No card filenames found in the CSV.")

    return cards


def card_image_name(card_tex: str) -> str:
    """card_Group_Name.tex -> Group_Name.png (also strips frame_/back_/terrain_ prefixes)."""
    stem = Path(card_tex).stem
    for prefix in CARD_NAME_PREFIXES:
        if stem.startswith(prefix):
            stem = stem[len(prefix):]
            break
    stem = re.sub(r"\s+", "_", stem)
    stem = re.sub(r"[^A-Za-z0-9_.-]", "", stem)
    return stem + ".png"


def generate_card_images(csv_path: Path, output_dir: Path, density: int, sheet_name: str):
    cards = read_card_list(csv_path)
    print(f"Found {len(cards)} card(s) in {csv_path}")

    sheet_tex = f"{sheet_name}.tex"
    sheet_pdf = f"{sheet_name}.pdf"

    run(
        [sys.executable, "generate_card_sheet.py",
         f"--csv={csv_path}", f"--output=build/{sheet_tex}",
         "--cols=1", "--rows=1"],
        cwd=WORKSPACE,
        label=f"generate_card_sheet  {csv_path} -> {sheet_tex} (1 card per page)",
    )
    run(["pdflatex", "-interaction=nonstopmode", sheet_tex], cwd=BUILD,
        label=f"pdflatex {sheet_tex}")

    output_dir.mkdir(parents=True, exist_ok=True)

    for i, card in enumerate(cards):
        out_name = card_image_name(card)
        out_path = output_dir / out_name
        run(
            ["convert", "-density", str(density), f"{sheet_pdf}[{i}]",
             "-compress", "lzw", str(out_path)],
            cwd=BUILD,
            label=f"convert {sheet_pdf} page {i} -> {output_dir.name}/{out_name}",
        )

    print(f"\nWrote {len(cards)} card image(s) to {output_dir}/")


def main():
    parser = argparse.ArgumentParser(
        description="Render individual cards from a CSV list into standalone PNG images."
    )
    parser.add_argument(
        "--csv", required=True,
        help="Single-column CSV of card .tex filenames (same format as decks/*.csv)."
    )
    parser.add_argument(
        "--output-dir", default="CardImages",
        help="Directory to write PNGs to (default: CardImages)."
    )
    parser.add_argument(
        "--density", type=int, default=150,
        help="ImageMagick rasterisation density (default: 150)."
    )
    parser.add_argument(
        "--sheet-name", default=None,
        help="Base name for the intermediate build/<name>.tex/.pdf (default: derived from --csv)."
    )
    args = parser.parse_args()

    csv_path = Path(args.csv)
    sheet_name = args.sheet_name or f"{csv_path.stem}_individual"

    generate_card_images(
        csv_path=csv_path,
        output_dir=WORKSPACE / args.output_dir,
        density=args.density,
        sheet_name=sheet_name,
    )


if __name__ == "__main__":
    main()
