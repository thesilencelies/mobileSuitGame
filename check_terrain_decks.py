#!/usr/bin/env python3
"""Check terrain deck coverage and objective counts."""

import csv
import os
from pathlib import Path

ROOT = Path(__file__).parent


def load_terrain_cards():
    """Return list of dicts from Terrain_square.csv, skipping PrintID=0."""
    cards = []
    with open(ROOT / "Terrain_square.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("PrintID", "1") == "0":
                continue
            # Helpcard is a reference card, not a terrain tile
            if row["Name"] == "Helpcard":
                continue
            defend = _parse_points(row.get("Defend Points", "0"))
            attack = _parse_points(row.get("Attack Points", "0"))
            cards.append({
                "name": row["Name"],
                "defend": defend,
                "attack": attack,
                "is_objective": defend > 0 or attack > 0,
            })
    return cards


def _parse_points(value):
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


def load_deck(path):
    """Return set of card names referenced by a deck CSV."""
    names = set()
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            for cell in row:
                cell = cell.strip()
                # Entries look like: terrain_<Name>.tex
                if cell.startswith("terrain_") and cell.endswith(".tex"):
                    name = cell[len("terrain_"):-len(".tex")]
                    names.add(name)
    return names


def main():
    terrain_cards = load_terrain_cards()
    all_names = {c["name"] for c in terrain_cards}
    objectives = [c for c in terrain_cards if c["is_objective"]]

    deck_paths = sorted((ROOT / "decks").glob("deck_terrain_*.csv"))

    # --- All objectives for reference ---
    print("=" * 60)
    print("OBJECTIVES (terrain with non-zero points)")
    print("=" * 60)
    for c in objectives:
        print(f"  {c['name']:30s}  defend={c['defend']}  attack={c['attack']}")
    print()

    # --- Per-deck report ---
    print("=" * 60)
    print("TERRAIN DECK COVERAGE")
    print("=" * 60)

    for deck_path in deck_paths:
        deck_name = deck_path.stem
        deck_names = load_deck(deck_path)

        extra   = sorted(deck_names - all_names)
        obj_count = sum(
            1 for c in objectives if c["name"] in deck_names
        )

        print(f"\n{deck_name}")
        print(f"  Cards in deck : {len(deck_names)}/{len(all_names)}")
        print(f"  Objectives    : {obj_count}")

        if extra:
            print(f"  Unrecognised entries ({len(extra)}):")
            for name in extra:
                print(f"    ? {name}")

    # --- Union coverage ---
    all_deck_names = set()
    for deck_path in deck_paths:
        all_deck_names |= load_deck(deck_path)

    uncovered = sorted(all_names - all_deck_names)
    print(f"\n{'=' * 60}")
    print("CARDS IN NO DECK")
    print("=" * 60)
    if uncovered:
        for name in uncovered:
            print(f"  - {name}")
    else:
        print("  All terrain cards appear in at least one deck.")


if __name__ == "__main__":
    main()
