#!/usr/bin/env python3
"""
Reports how many times each defined action card appears across all action decks,
highlighting cards with zero or few inclusions.

Flags:
  --by-group   Group cards by weapon group; show how many distinct decks include
               any card from each group, sorted by deck coverage ascending.
"""

import argparse
import csv
import os
from collections import defaultdict

CARD_CSVS = [
    "Weapon actions.csv",
    "Basic actions.csv",
    "Booster actions.csv",
    "Pilot actions.csv",
    "Drone actions.csv"
]

DECKS_DIR = "decks"
DECK_PREFIXES = ("deck_aegis", "deck_church", "deck_collective", "deck_guild", "deck_ouwa", "deck_revolution")


def load_defined_cards():
    """Return dict of tex_filename -> (source_csv, group, name)."""
    cards = {}
    for csv_file in CARD_CSVS:
        if not os.path.exists(csv_file):
            print(f"Warning: {csv_file} not found, skipping.")
            continue
        with open(csv_file, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row.get("Name", "").strip()
                group = row.get("Group", "").strip()
                if not name or not group:
                    continue
                tex_name = f"card/{group}_{name}.tex"
                cards[tex_name] = (os.path.splitext(csv_file)[0], group, name)
    return cards


def load_deck_counts():
    """Return (counts, deck_files, card_to_decks).

    counts        : tex_filename -> total appearances across all action decks
    deck_files    : list of deck csv filenames scanned
    card_to_decks : tex_filename -> set of deck filenames that include it
    """
    counts = defaultdict(int)
    card_to_decks = defaultdict(set)
    deck_files_used = []
    for fname in sorted(os.listdir(DECKS_DIR)):
        if not fname.endswith(".csv"):
            continue
        if not any(fname.startswith(p) for p in DECK_PREFIXES):
            continue
        deck_path = os.path.join(DECKS_DIR, fname)
        deck_files_used.append(fname)
        with open(deck_path, newline="", encoding="utf-8") as f:
            for line in f:
                tex = line.strip()
                if not tex.startswith("card/"):
                    continue
                # Deck CSVs list base names without the .tex extension; append
                # it so keys match those built in load_defined_cards().
                if not tex.endswith(".tex"):
                    tex += ".tex"
                counts[tex] += 1
                card_to_decks[tex].add(fname)
    return counts, deck_files_used, card_to_decks


def print_by_group(defined, counts, card_to_decks, deck_files):
    """Group view: one row per group showing distinct-deck coverage."""
    total_decks = len(deck_files)

    # Build group -> {tex -> (source, name)}
    group_cards = defaultdict(dict)
    for tex, (source, group, name) in defined.items():
        group_cards[group][tex] = (source, name)

    # For each group compute: decks that include ANY card from the group
    rows = []
    for group, cards in group_cards.items():
        covering_decks = set()
        for tex in cards:
            covering_decks |= card_to_decks.get(tex, set())
        total_copies = sum(counts.get(tex, 0) for tex in cards)
        card_count = len(cards)
        missing_cards = [name for tex, (_, name) in cards.items() if counts.get(tex, 0) == 0]
        source = next(iter(cards.values()))[0]
        rows.append((len(covering_decks), total_copies, group, source, card_count, missing_cards))

    rows.sort(key=lambda r: (r[0], r[1], r[2]))

    col_w = 22
    print("=" * 75)
    print(f"GROUPS BY DECK COVERAGE  (out of {total_decks} decks)")
    print("=" * 75)
    print(f"  {'Decks':>5}  {'Copies':>6}  {'Source':<20} {'Group':<{col_w}} {'Cards'}")
    print(f"  {'-----':>5}  {'------':>6}  {'-'*18} {'-'*col_w} {'-----'}")
    for deck_n, copies, group, source, card_count, missing_cards in rows:
        missing_note = f"  [missing: {', '.join(missing_cards)}]" if missing_cards else ""
        print(f"  {deck_n:>5}  {copies:>6}  {source:<20} {group:<{col_w}} {card_count} cards{missing_note}")

    print()
    groups_zero = [r[2] for r in rows if r[0] == 0]
    groups_partial = [r[2] for r in rows if 0 < r[0] < total_decks]
    print("=" * 75)
    print("SUMMARY")
    print("=" * 75)
    print(f"  Total groups:                 {len(rows)}")
    print(f"  Groups in 0 decks:            {len(groups_zero)}")
    if groups_zero:
        print(f"    {', '.join(groups_zero)}")
    print(f"  Groups in some decks (<{total_decks}):   {len(groups_partial)}")
    print(f"  Groups in all decks:          {sum(1 for r in rows if r[0] == total_decks)}")


def main():
    parser = argparse.ArgumentParser(description="Check card coverage across decks.")
    parser.add_argument(
        "--by-group",
        action="store_true",
        help="Group cards by weapon group; show distinct-deck coverage per group.",
    )
    args = parser.parse_args()

    defined = load_defined_cards()
    counts, deck_files, card_to_decks = load_deck_counts()

    print(f"Defined action cards: {len(defined)}")
    print(f"Action decks scanned: {len(deck_files)}")
    print(f"  {', '.join(deck_files)}")
    print()

    if args.by_group:
        print_by_group(defined, counts, card_to_decks, deck_files)
        return

    # Gather all tex names that appear in either defined or decks
    all_names = set(defined.keys()) | set(counts.keys())

    # Separate into categories
    missing = []    # defined but never in any deck
    present = []    # defined and in at least one deck
    orphan = []     # in a deck but not in any CSV

    for tex in sorted(all_names):
        count = counts.get(tex, 0)
        if tex in defined:
            source, group, name = defined[tex]
            if count == 0:
                missing.append((source, group, name, tex, count))
            else:
                present.append((source, group, name, tex, count))
        else:
            orphan.append((tex, count))

    # Sort present by count ascending so under-represented cards surface first
    present.sort(key=lambda x: (x[4], x[1], x[2]))
    missing.sort(key=lambda x: (x[1], x[2]))

    col_w = 22

    print("=" * 70)
    print(f"CARDS NOT IN ANY DECK ({len(missing)})")
    print("=" * 70)
    if missing:
        print(f"  {'Source':<20} {'Group':<{col_w}} {'Card Name'}")
        print(f"  {'-'*18} {'-'*col_w} {'-'*25}")
        for source, group, name, tex, _ in missing:
            print(f"  {source:<20} {group:<{col_w}} {name}")
    else:
        print("  (all defined cards appear in at least one deck)")

    print()
    print("=" * 70)
    print(f"CARDS BY DECK INCLUSION COUNT ({len(present)} cards)")
    print("=" * 70)
    print(f"  {'#':>3}  {'Source':<20} {'Group':<{col_w}} {'Card Name'}")
    print(f"  {'---':>3}  {'-'*18} {'-'*col_w} {'-'*25}")
    for source, group, name, tex, count in present:
        print(f"  {count:>3}  {source:<20} {group:<{col_w}} {name}")

    if orphan:
        print()
        print("=" * 70)
        print(f"DECK ENTRIES WITH NO MATCHING CSV DEFINITION ({len(orphan)})")
        print("=" * 70)
        for tex, count in sorted(orphan):
            print(f"  {count:>3}x  {tex}")

    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    max_count = max((c for _, _, _, _, c in present), default=0)
    print(f"  Total defined cards:          {len(defined)}")
    print(f"  Cards in at least one deck:   {len(present)}")
    print(f"  Cards in NO deck:             {len(missing)}")
    print(f"  Max inclusions (any card):    {max_count}")
    if orphan:
        print(f"  Orphan deck entries:          {len(orphan)}")


if __name__ == "__main__":
    main()
