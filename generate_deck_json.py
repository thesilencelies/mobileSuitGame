#!/usr/bin/env python3
"""
generate_deck_json.py

Generates json/decks.json: Tabletop Simulator metadata for each playable Frame's
action deck from decks/.

The schema matches:

    {
      "<Name of Frame>": {
        "back": "<raw.githubusercontent URL of frame back in TTSImages/>",
        "cards": {
          "1": "<Name of Card 1>",
          "2": "<Name of Card 2>",
          ...
        }
      }, ...
    }

Usage:
    python generate_deck_json.py
    python generate_deck_json.py --output json/decks.json --quiet
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

WORKSPACE = Path(__file__).parent
FRAMES_CSV = WORKSPACE / "Frames.csv"
DECKS_DIR = WORKSPACE / "decks"
TTS_IMAGES_DIR = WORKSPACE / "TTSImages"
DEFAULT_OUTPUT = WORKSPACE / "json" / "decks.json"

ACTION_CSVS = (
    "Weapon actions.csv",
    "Basic actions.csv",
    "Booster actions.csv",
    "Pilot actions.csv",
    "Drone actions.csv",
)

GITHUB_REPO_RAW = (
    "https://raw.githubusercontent.com/thesilencelies/mobileSuitGame/"
    "refs/heads/master/"
)
TTS_IMAGES_URL = GITHUB_REPO_RAW + "TTSImages/"


def _cell(row: dict, column: str) -> str:
    return (row.get(column) or "").strip()


def _print_id_nonzero(row: dict) -> bool:
    val = _cell(row, "PrintID")
    if not val:
        return True
    try:
        return int(val) != 0
    except ValueError:
        return True


def card_display_name(row: dict) -> str:
    """"Axe" + "Hook" -> "Axe Hook". A card with group and name matching
    ("Swarm"/"Swarm" -> "Swarm Swarm", "Cannon"/"Cannon" -> "Cannon Cannon")
    keeps both names. A card already carrying its group's name as a prefix
    ("Missile Rack"/"Missile Rack 1") does not say it twice."""
    group, name = _cell(row, "Group"), _cell(row, "Name")
    if not group or name.startswith(group + " "):
        return name
    return f"{group} {name}"


def load_card_display_map() -> dict[str, str]:
    """Map '{Group}_{Name}' keys to card display names matching cards.json."""
    mapping: dict[str, str] = {}
    for csv_name in ACTION_CSVS:
        path = WORKSPACE / csv_name
        if not path.is_file():
            continue
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if not _print_id_nonzero(row):
                    continue
                group = _cell(row, "Group")
                name = _cell(row, "Name")
                if not group or not name:
                    continue
                key = f"{group}_{name}"
                mapping[key] = card_display_name(row)
    return mapping


def generate_decks_data(card_map: dict[str, str], quiet: bool = False) -> dict[str, dict]:
    if not FRAMES_CSV.is_file():
        sys.exit(f"Error: Frames CSV not found: {FRAMES_CSV}")

    decks_data: dict[str, dict] = {}

    with open(FRAMES_CSV, newline="", encoding="utf-8") as fh:
        frames = [row for row in csv.DictReader(fh) if _print_id_nonzero(row)]

    for row in frames:
        frame_name = _cell(row, "Name")
        if not frame_name:
            continue

        card_img = _cell(row, "CardImg")
        prefix = Path(card_img).stem
        deck_csv_path = DECKS_DIR / f"deck_{prefix}.csv"

        if not deck_csv_path.is_file():
            if not quiet:
                print(f"Warning: deck file not found for frame '{frame_name}': {deck_csv_path}", file=sys.stderr)
            continue

        back_image_name = f"{prefix}_back_image.png"
        back_url = TTS_IMAGES_URL + quote(back_image_name)

        # Read deck cards
        with open(deck_csv_path, newline="", encoding="utf-8") as dfh:
            card_lines = [line.strip() for line in dfh if line.strip() and not line.startswith("#")]

        cards_dict: dict[str, str] = {}
        for idx, key in enumerate(card_lines, start=1):
            disp_name = card_map.get(key)
            if disp_name is None:
                # Fallback: if key starts with 'card/', strip it and retry
                stripped_key = key[5:] if key.startswith("card/") else key
                disp_name = card_map.get(stripped_key, key)
            cards_dict[str(idx)] = disp_name

        decks_data[frame_name] = {
            "back": back_url,
            "cards": cards_dict,
        }

    return decks_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate json/decks.json for Tabletop Simulator decks.")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Destination JSON path (default: {DEFAULT_OUTPUT.relative_to(WORKSPACE)})",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress informational stdout output.",
    )
    args = parser.parse_args()

    card_map = load_card_display_map()
    decks = generate_decks_data(card_map, quiet=args.quiet)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(decks, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    if not args.quiet:
        total_cards = sum(len(d["cards"]) for d in decks.values())
        print(f"Wrote {len(decks)} decks ({total_cards} cards total) to {args.output.relative_to(WORKSPACE)}")


if __name__ == "__main__":
    main()
