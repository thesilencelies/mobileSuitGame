#!/usr/bin/env python3
"""
generate_card_sheet.py

Reads a CSV file of card .tex filenames (one per line, no header) and generates
a LaTeX file that lays them out in a 10×7 grid suitable for Tabletop Simulator
custom card sheets.

Each card is a TikZ image of 6.4cm × 8.9cm, included via \input{}.
Cards fill left-to-right, top-to-bottom. If the final sheet has fewer than 70
cards, the remaining cells are left blank.

Usage:
    python generate_card_sheet.py --csv cards.csv --output card_sheet.tex
    python generate_card_sheet.py --csv cards.csv --output card_sheet.tex --no-bleed

Arguments:
    --csv       Path to the CSV file listing card .tex filenames (required)
    --output    Path for the generated .tex file (default: card_sheet.tex)
    --bleed     Gap between cards in cm (default: 0.0 — cards flush together)
"""

import argparse
import csv
import math
import os
import sys

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------
COLS = 10
ROWS = 7
CARDS_PER_SHEET = COLS * ROWS  # 70

CARD_WIDTH_CM  = 6.4
CARD_HEIGHT_CM = 8.9

# ---------------------------------------------------------------------------
# LaTeX template pieces
# ---------------------------------------------------------------------------

PREAMBLE = r"""\documentclass{{article}}
\usepackage{{tikz}}
\usepackage{{geometry}}

% Page size matches the exact grid: {page_width}cm x {page_height}cm
\geometry{{
    paperwidth={page_width}cm,
    paperheight={page_height}cm,
    margin=0cm
}}

\setlength{{\parindent}}{{0pt}}
\setlength{{\parskip}}{{0pt}}
\pagestyle{{empty}}

\begin{{document}}
"""

SHEET_BEGIN = r"""%% --- Sheet {sheet_num} ({first_card}–{last_card}) ---
\noindent
"""

# Each card is placed in a \hbox of exact card dimensions, clipped so that
# any TikZ drawing that strays outside the card boundary doesn't shift
# neighbouring cells. No outer tikzpicture — cards contain their own.
CARD_INCLUDE = r"""\hbox to {width}cm{{\hss
  \begin{{minipage}}[t][{height}cm][t]{{{width}cm}}%
    \vspace{{0pt}}%
    \input{{{filename}}}%
  \end{{minipage}}%
  \hss}}%
"""

EMPTY_CELL = r"""\hbox to {width}cm{{\hss
  \begin{{minipage}}[t][{height}cm][t]{{{width}cm}}%
  \end{{minipage}}%
  \hss}}%
"""

ROW_BEGIN = r"""\noindent\makebox[0pt][l]{}%
"""

ROW_END = r"""\par\vspace{{0pt}}%
"""

SHEET_END = r"""\newpage
"""

POSTAMBLE = r"""\end{document}
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_card_list(csv_path: str) -> list[str]:
    """Read a single-column CSV (no header) and return a list of filenames."""
    if not os.path.isfile(csv_path):
        sys.exit(f"Error: CSV file not found: {csv_path}")

    cards = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        for row in reader:
            if row:  # skip completely blank lines
                filename = row[0].strip()
                if filename:
                    cards.append(filename)

    if not cards:
        sys.exit("Error: No card filenames found in the CSV.")

    return cards



def generate_latex(cards: list[str], bleed: float) -> str:
    """Build the full LaTeX source string."""

    num_sheets = math.ceil(len(cards) / CARDS_PER_SHEET)

    page_width  = COLS * CARD_WIDTH_CM  + (COLS - 1) * bleed
    page_height = ROWS * CARD_HEIGHT_CM + (ROWS - 1) * bleed

    w = f"{CARD_WIDTH_CM:.4f}"
    h = f"{CARD_HEIGHT_CM:.4f}"

    lines = []
    lines.append(PREAMBLE.format(
        page_width=f"{page_width:.4f}",
        page_height=f"{page_height:.4f}",
    ))

    for sheet in range(num_sheets):
        first_idx  = sheet * CARDS_PER_SHEET
        last_idx   = min(first_idx + CARDS_PER_SHEET, len(cards))
        sheet_cards = cards[first_idx:last_idx]

        lines.append(SHEET_BEGIN.format(
            sheet_num=sheet + 1,
            first_card=first_idx + 1,
            last_card=last_idx,
        ))

        for row in range(ROWS):
            lines.append(ROW_BEGIN)
            for col in range(COLS):
                pos = row * COLS + col
                if pos < len(sheet_cards):
                    lines.append(CARD_INCLUDE.format(
                        width=w, height=h,
                        filename=sheet_cards[pos],
                    ))
                else:
                    lines.append(EMPTY_CELL.format(width=w, height=h))
            lines.append(ROW_END)

        lines.append(SHEET_END)

    lines.append(POSTAMBLE)
    return "".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate a LaTeX card sheet for Tabletop Simulator."
    )
    parser.add_argument(
        "--csv", required=True,
        help="Path to the single-column CSV listing card .tex filenames."
    )
    parser.add_argument(
        "--output", default="card_sheet.tex",
        help="Output .tex filename (default: card_sheet.tex)."
    )
    parser.add_argument(
        "--bleed", type=float, default=0.0,
        help="Gap between cards in cm (default: 0.0)."
    )
    args = parser.parse_args()

    cards = read_card_list(args.csv)
    print(f"Found {len(cards)} card(s) → "
          f"{math.ceil(len(cards) / CARDS_PER_SHEET)} sheet(s) of up to {CARDS_PER_SHEET}.")

    latex_source = generate_latex(cards, bleed=args.bleed)

    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(latex_source)

    print(f"LaTeX file written to: {args.output}")
    print(f"Compile with:  pdflatex {args.output}")


if __name__ == "__main__":
    main()
