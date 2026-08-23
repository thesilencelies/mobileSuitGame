#!/usr/bin/env python3
"""
generate_card_images.py

Renders individual cards to standalone PNG images (e.g. for previews, a
rulebook, or a website) rather than a TTS grid sheet.

Reads a single-column CSV listing card .tex filenames -- one build/card/*.tex
(or build/terrain/*.tex, build/frame/*.tex, back_*) filename per line, no header.
individual_cards.csv is a mixed deck listing explicit folder-prefixed paths
(unlike the typed decks in decks/, which list bare names). Each card is placed on its own page via
generate_card_sheet.py --cols 1 --rows 1, which takes care of pulling in
build/card_macros.tex (the icon/ability macros) alongside the card itself.
The resulting multi-page PDF is compiled once, then each page is rasterised
to its own PNG named after the card.

Also supports --all, which ignores --csv entirely and instead enumerates every
printable row from all the action CSVs plus Frames.csv and Terrain_square.csv,
rendering one PNG per card into a separate folder (default AllCardImages/) for
a companion app that looks up card art by its "{Group}_{Name}" key. This
requires generateCards.py to have already been run so the build/card/,
build/frame/ and build/terrain/ .tex files it reads exist.

Usage:
    python generate_card_images.py --csv individual_cards.csv
    python generate_card_images.py --csv individual_cards.csv --output-dir CardImages --density 300
    python generate_card_images.py --all
    python generate_card_images.py --all --output-dir AllCardImages --density 300
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

# The action CSVs share a Group+Name schema; build/card/*.tex files are named
# literally "{Group}_{Name}.tex" (see generateCards.py's make_card_from_row),
# so --all reproduces that exact naming rather than the sanitised
# card_image_name() used for the --csv path.
ACTION_CSVS = (
    "Weapon actions.csv",
    "Basic actions.csv",
    "Booster actions.csv",
    "Pilot actions.csv",
    "Drone actions.csv",
)
FRAMES_CSV = "Frames.csv"
TERRAIN_CSV = "Terrain_square.csv"


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
                filename = row[0].strip()
                if not filename.endswith(".tex"):
                    filename += ".tex"
                cards.append(filename)

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


def _compile_and_rasterize(sheet_name: str, output_names: list[str], output_dir: Path, density: int):
    """Compile build/<sheet_name>.tex (already written) and rasterise each page
    to output_names[page] in output_dir. Shared tail of both --csv and --all."""
    sheet_tex = f"{sheet_name}.tex"
    sheet_pdf = f"{sheet_name}.pdf"

    run(["pdflatex", "-interaction=nonstopmode", sheet_tex], cwd=BUILD,
        label=f"pdflatex {sheet_tex}")

    output_dir.mkdir(parents=True, exist_ok=True)

    for i, out_name in enumerate(output_names):
        out_path = output_dir / out_name
        run(
            ["convert", "-density", str(density), f"{sheet_pdf}[{i}]",
             "-compress", "lzw", str(out_path)],
            cwd=BUILD,
            label=f"convert {sheet_pdf} page {i} -> {output_dir.name}/{out_name}",
        )

    print(f"\nWrote {len(output_names)} card image(s) to {output_dir}/")


def generate_card_images(csv_path: Path, output_dir: Path, density: int, sheet_name: str):
    cards = read_card_list(csv_path)
    print(f"Found {len(cards)} card(s) in {csv_path}")

    sheet_tex = f"{sheet_name}.tex"

    run(
        [sys.executable, "generate_card_sheet.py",
         f"--csv={csv_path}", f"--output=build/{sheet_tex}",
         "--cols=1", "--rows=1"],
        cwd=WORKSPACE,
        label=f"generate_card_sheet  {csv_path} -> {sheet_tex} (1 card per page)",
    )

    output_names = [card_image_name(card) for card in cards]
    _compile_and_rasterize(sheet_name, output_names, output_dir, density)


def _print_id_nonzero(row: dict) -> bool:
    """True unless the row's PrintID is explicitly 0 (the CSVs' "omit from
    output" convention). Missing/unparseable PrintID is treated as printable
    rather than silently dropping the row."""
    raw = row.get("PrintID")
    if raw is None or not str(raw).strip():
        return True
    try:
        return int(raw) != 0
    except ValueError:
        return True


def enumerate_all_cards() -> list[tuple[str, str]]:
    """Enumerate every printable row across the action CSVs, Frames.csv and
    Terrain_square.csv, returning (build-relative .tex path, output .png name)
    pairs in a stable order.

    Action-card names are the literal "{Group}_{Name}" engine key (no
    sanitising) so they match build/card/*.tex exactly. Frames and terrain
    tiles reuse card_image_name(), the same sanitising the --csv path uses.
    """
    pairs: list[tuple[str, str]] = []

    for csv_name in ACTION_CSVS:
        csv_path = WORKSPACE / csv_name
        if not csv_path.is_file():
            sys.exit(f"Error: expected card CSV not found: {csv_path}")
        with open(csv_path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if not _print_id_nonzero(row):
                    continue
                group, name = row["Group"], row["Name"]
                pairs.append((f"card/{group}_{name}.tex", f"{group}_{name}.png"))

    frames_path = WORKSPACE / FRAMES_CSV
    if not frames_path.is_file():
        sys.exit(f"Error: expected frames CSV not found: {frames_path}")
    with open(frames_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if not _print_id_nonzero(row):
                continue
            tex = f"frame/{row['Name']}.tex"
            pairs.append((tex, card_image_name(tex)))

    terrain_path = WORKSPACE / TERRAIN_CSV
    if not terrain_path.is_file():
        sys.exit(f"Error: expected terrain CSV not found: {terrain_path}")
    with open(terrain_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if not _print_id_nonzero(row):
                continue
            tex = f"terrain/{row['Name']}.tex"
            pairs.append((tex, card_image_name(tex)))

    if not pairs:
        sys.exit("Error: no printable rows found across the action/frame/terrain CSVs.")

    return pairs


def _require_tex_files(pairs: list[tuple[str, str]]):
    """Fail with a clear, actionable message rather than letting pdflatex
    produce a broken PDF when generateCards.py hasn't been run (or is stale)."""
    missing = [tex for tex, _ in pairs if not (BUILD / tex).is_file()]
    if missing:
        shown = "\n  ".join(missing[:10])
        more = f"\n  ... and {len(missing) - 10} more" if len(missing) > 10 else ""
        sys.exit(
            f"Error: {len(missing)} of {len(pairs)} expected build/*.tex file(s) are "
            "missing.\nRun `python generateCards.py` first to generate them, then "
            f"re-run with --all.\nMissing, e.g.:\n  {shown}{more}"
        )


def generate_all_card_images(output_dir: Path, density: int, sheet_name: str):
    pairs = enumerate_all_cards()
    print(f"Found {len(pairs)} card(s) across all action/frame/terrain CSVs")

    _require_tex_files(pairs)

    cards = [tex for tex, _ in pairs]
    output_names = [name for _, name in pairs]

    sheet_tex = f"{sheet_name}.tex"

    # generate_card_sheet.py only accepts a CSV path, so write the resolved
    # card list to a scratch CSV in build/ (gitignored, regenerated each run)
    # to lay out exactly these cards, in this order, one per page.
    BUILD.mkdir(parents=True, exist_ok=True)
    list_csv = BUILD / f"{sheet_name}_list.csv"
    with open(list_csv, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows([card] for card in cards)

    run(
        [sys.executable, "generate_card_sheet.py",
         f"--csv={list_csv}", f"--output=build/{sheet_tex}",
         "--cols=1", "--rows=1"],
        cwd=WORKSPACE,
        label=f"generate_card_sheet  {len(cards)} card(s) -> {sheet_tex} (1 card per page)",
    )

    _compile_and_rasterize(sheet_name, output_names, output_dir, density)


def main():
    parser = argparse.ArgumentParser(
        description="Render individual cards from a CSV list into standalone PNG images."
    )
    parser.add_argument(
        "--csv", default=None,
        help="Single-column CSV of card .tex filenames (same format as decks/*.csv). "
             "Required unless --all is given; ignored if --all is given."
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Ignore --csv and render every card: every row (skipping PrintID == 0) "
             "from all action CSVs (Weapon/Basic/Booster/Pilot/Drone actions.csv), "
             "every Frame in Frames.csv, and every tile in Terrain_square.csv. "
             "Requires `python generateCards.py` to have already been run so the "
             "build/card, build/frame and build/terrain .tex files exist. "
             "Default --output-dir is AllCardImages instead of CardImages."
    )
    parser.add_argument(
        "--output-dir", default=None,
        help="Directory to write PNGs to (default: CardImages, or AllCardImages with --all)."
    )
    parser.add_argument(
        "--density", type=int, default=150,
        help="ImageMagick rasterisation density (default: 150)."
    )
    parser.add_argument(
        "--sheet-name", default=None,
        help="Base name for the intermediate build/<name>.tex/.pdf "
             "(default: derived from --csv, or 'all_cards' with --all)."
    )
    args = parser.parse_args()

    if not args.all and not args.csv:
        parser.error("--csv is required unless --all is given")

    output_dir = WORKSPACE / (args.output_dir or ("AllCardImages" if args.all else "CardImages"))

    if args.all:
        generate_all_card_images(
            output_dir=output_dir,
            density=args.density,
            sheet_name=args.sheet_name or "all_cards",
        )
    else:
        csv_path = Path(args.csv)
        generate_card_images(
            csv_path=csv_path,
            output_dir=output_dir,
            density=args.density,
            sheet_name=args.sheet_name or f"{csv_path.stem}_individual",
        )


if __name__ == "__main__":
    main()
