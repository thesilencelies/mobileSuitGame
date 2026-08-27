#!/usr/bin/env python3
"""Check terrain and objective deck coverage against `Terrain_square.csv`.

Each player brings a 10-card terrain deck and a 5-card objective deck
(rules.tex:253), and a card that scores points may only be in the objective
deck. This reports, per deck: its size, whether any card is in the wrong deck,
and any name that is not a terrain card at all -- then, across all the decks,
which cards appear in none of them.

Deck files list one bare card name per row (`decks/deck_terrain_*.csv`,
`decks/deck_objective_*.csv`). The older `terrain/<Name>.tex` form is still
accepted so an old deck file reads correctly.
"""

import csv
from pathlib import Path

ROOT = Path(__file__).parent

TERRAIN_DECK_SIZE = 10
OBJECTIVE_DECK_SIZE = 5


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
    """Return the list of card names a deck file references, in order."""
    names = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            for cell in row:
                cell = cell.strip()
                if not cell:
                    continue
                # Bare names today; `terrain/<Name>[.tex]` in older deck files.
                if cell.startswith("terrain/"):
                    cell = cell[len("terrain/"):]
                if cell.endswith(".tex"):
                    cell = cell[:-len(".tex")]
                names.append(cell)
    return names


def report_deck(path, kind, cards):
    """One deck's line in the report. Returns the names it uses."""
    by_name = {c["name"]: c for c in cards}
    names = load_deck(path)
    wanted = TERRAIN_DECK_SIZE if kind == "terrain" else OBJECTIVE_DECK_SIZE
    unknown = [n for n in names if n not in by_name]
    known = [by_name[n] for n in names if n in by_name]
    misfiled = [
        c["name"] for c in known
        if c["is_objective"] != (kind == "objective")
    ]
    duplicates = sorted({n for n in names if names.count(n) > 1})

    print(f"\n{path.stem}")
    print(f"  Cards         : {len(names)}"
          + ("" if len(names) == wanted else f"  (expected {wanted})"))
    if unknown:
        print(f"  Not in the CSV ({len(unknown)}):")
        for name in unknown:
            print(f"    ? {name}")
    if misfiled:
        where = "scores points but is in the terrain deck" if kind == "terrain" \
            else "scores nothing but is in the objective deck"
        for name in misfiled:
            print(f"    ! {name} {where}")
    if duplicates:
        print(f"  Duplicated    : {', '.join(duplicates)}")
    return set(names)


def main():
    terrain_cards = load_terrain_cards()
    all_names = {c["name"] for c in terrain_cards}
    objectives = [c for c in terrain_cards if c["is_objective"]]

    print("=" * 60)
    print("OBJECTIVES (terrain with non-zero points)")
    print("=" * 60)
    for c in objectives:
        print(f"  {c['name']:30s}  defend={c['defend']}  attack={c['attack']}")

    print()
    print("=" * 60)
    print("DECK COVERAGE")
    print("=" * 60)

    used = set()
    for kind in ("terrain", "objective"):
        for path in sorted((ROOT / "decks").glob(f"deck_{kind}_*.csv")):
            used |= report_deck(path, kind, terrain_cards)

    uncovered = sorted(all_names - used)
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
