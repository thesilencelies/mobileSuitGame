#!/usr/bin/env python3
"""
generate_card_sheet.py

Reads a CSV file of card .tex filenames (one per line, no header) and generates
a LaTeX file that lays them out in a grid suitable for Tabletop Simulator
custom card sheets.

Each card is a TikZ image of 6.4cm × 8.9cm, included via \input{}.
Cards fill left-to-right, top-to-bottom. If the final sheet has fewer cards
than the grid, the remaining cells are left blank.

Usage:
    python generate_card_sheet.py --csv cards.csv --output card_sheet.tex
    python generate_card_sheet.py --csv cards.csv --cols 10 --rows 7
    python generate_card_sheet.py --csv cards.csv --cols 5 --rows 4 --bleed 0.1

Arguments:
    --csv       Path to the CSV file listing card .tex filenames (required)
    --output    Path for the generated .tex file (default: card_sheet.tex)
    --cols      Number of columns per sheet (default: 10)
    --rows      Number of rows per sheet (default: 7)
    --bleed     Gap between cards in cm (default: 0.0 — cards flush together)
"""

import argparse
import csv
import math
import os
import sys

# ---------------------------------------------------------------------------
# Layout defaults (overridable via CLI)
# ---------------------------------------------------------------------------
DEFAULT_COLS = 10
DEFAULT_ROWS = 7

CARD_WIDTH_CM  = 6.4
CARD_HEIGHT_CM = 8.9

# ---------------------------------------------------------------------------
# LaTeX template pieces
# ---------------------------------------------------------------------------

PREAMBLE = r"""\documentclass{{article}}
\usepackage{{tikz}}
 \usepackage[export]{{adjustbox}}
\usepackage{{geometry}}
\input{{card_macros.tex}}
 \usetikzlibrary{{positioning}}
 \usetikzlibrary{{patterns}}
 \usetikzlibrary{{arrows.meta}}

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

FINAL_CELL = r"""\hbox to {width}cm{{\hss
  \begin{{minipage}}[t][{height}cm][t]{{{width}cm}}%
    \vspace{{0pt}}%
    \begin{{tikzpicture}}[backbox/.style= {{rectangle, minimum height = 8.9cm, minimum width =6.35cm, rounded corners = 0.3cm, fill=white, opacity=0.75}}]
    \node [rectangle, minimum width = 6.4cm, minimum height = 8.7cm, fill=black!70!white!30] at (3.25,4.5){{}};
    \draw[<->, line width=12pt, draw={color}] (0.6,0.5) -- (5.8, 8.4);
    \draw[<->, line width=12pt, draw={color}] (5.8,0.5) -- (0.6, 8.4);
    \node [rectangle, draw, rounded corners = 0.4cm, minimum width = 5cm, minimum height=2cm, fill=blue!10] at (3.25,4.5) {{}};
    \node [circle, draw, minimum width = 3.4cm, fill=blue!10] at (3.25,4.5) {{\Large{{{text}}}}};
    \end{{tikzpicture}}
  \end{{minipage}}%
  \hss}}%
"""

ROW_BEGIN = r"""\noindent\makebox[0pt][l]{}%
"""

ROW_END = r"""\par\vspace{0pt}%
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



def generate_latex(cards: list[str], bleed: float, cols: int, rows: int,
                    back_text: str, back_color: str, add_back: bool) -> str:
    """Build the full LaTeX source string."""

    cards_per_sheet = cols * rows
    num_sheets = math.ceil(len(cards) / cards_per_sheet)

    page_width  = cols * CARD_WIDTH_CM  + (cols - 1) * bleed
    page_height = rows * (CARD_HEIGHT_CM + 0.14) + (rows - 1) * bleed

    w = f"{CARD_WIDTH_CM:.4f}"
    h = f"{CARD_HEIGHT_CM:.4f}"

    lines = []
    lines.append(PREAMBLE.format(
        page_width=f"{page_width:.4f}",
        page_height=f"{page_height:.4f}",
    ))

    for sheet in range(num_sheets):
        first_idx  = sheet * cards_per_sheet
        last_idx   = min(first_idx + cards_per_sheet, len(cards))
        sheet_cards = cards[first_idx:last_idx]

        lines.append(SHEET_BEGIN.format(
            sheet_num=sheet + 1,
            first_card=first_idx + 1,
            last_card=last_idx,
        ))

        for row in range(rows):
            lines.append(ROW_BEGIN)
            for col in range(cols):
                if add_back and row == rows -1 and col == cols -1:
                    lines.append(FINAL_CELL.format(width=w, height=h, text=back_text, color=back_color))
                else:
                    pos = row * cols + col
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
        "--cols", type=int, default=DEFAULT_COLS,
        help=f"Number of card columns per sheet (default: {DEFAULT_COLS})."
    )
    parser.add_argument(
        "--rows", type=int, default=DEFAULT_ROWS,
        help=f"Number of card rows per sheet (default: {DEFAULT_ROWS})."
    )
    parser.add_argument(
        "--bleed", type=float, default=0.0,
        help="Gap between cards in cm (default: 0.0)."
    )
    parser.add_argument(
        "--back_text", default="NetFrame",
        help="text to write on the back of the card"
    )
    parser.add_argument(
        "--back_color", default="blue", help="color for the lines on the back"
    )
    parser.add_argument(
        "--add_back", action="store_true"
    )
    args = parser.parse_args()

    cards_per_sheet = args.cols * args.rows
    cards = read_card_list(args.csv)
    print(f"Grid: {args.cols}×{args.rows} ({cards_per_sheet} cards per sheet)")
    print(f"Found {len(cards)} card(s) → "
          f"{math.ceil(len(cards) / cards_per_sheet)} sheet(s).")

    latex_source = generate_latex(cards, bleed=args.bleed, cols=args.cols, rows=args.rows,
                                   back_text=args.back_text, back_color=args.back_color, add_back=args.add_back)

    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(latex_source)

    print(f"LaTeX file written to: {args.output}")
    print(f"Compile with:  pdflatex {args.output}")


if __name__ == "__main__":
    main()
