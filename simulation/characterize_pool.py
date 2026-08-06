#!/usr/bin/env python3
"""Characterise the action-card pool by High/Mid/Low zone distribution.

Reports, per zone, four distributions (the ones that drive zone balance):
  * attacking cards (non-feint) -- how many cards attack that zone at all
  * attack damage (non-feint)   -- total damage points thrown at that zone
  * blocking cards              -- how many cards can block that zone
  * super blocks                -- how many of those blocks are super blocks

A feint deals no damage, so it is excluded from the attack rows (its count is
shown separately for reference). Uses the same load_cards() as the simulator, so
"the pool" is exactly what the sim sees (all rows of the action CSVs).

Run:
    python simulation/characterize_pool.py
    python simulation/characterize_pool.py --by-group   # per weapon-group breakdown
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from simulation.simulate import load_cards, ZONES  # noqa: E402


def tally(cards: list) -> dict:
    """Aggregate a list of Cards into the four per-zone distributions."""
    t = {row: {z: 0 for z in ZONES} for row in
         ("atk_cards", "atk_dmg", "blocks", "supers")}
    feint_atk = {z: 0 for z in ZONES}
    for c in cards:
        for z, a in zip(ZONES, c.attacks):
            if a > 0:
                if c.feint:
                    feint_atk[z] += 1
                else:
                    t["atk_cards"][z] += 1
                    t["atk_dmg"][z] += a
        for z in c.blocks:
            t["blocks"][z] += 1
            if z in c.super_blocks:
                t["supers"][z] += 1
    return t, feint_atk


def _bar(values: dict, width: int = 24) -> str:
    """A compact proportional bar 'High|Mid|Low' for a zone distribution."""
    total = sum(values.values())
    if total <= 0:
        return ""
    cells = []
    for z in ZONES:
        n = round(width * values[z] / total)
        cells.append(z[0] * n)
    return "".join(cells)


ROWS = [
    ("atk_cards", "attacking cards (non-feint)"),
    ("atk_dmg", "attack damage (non-feint)"),
    ("blocks", "blocking cards"),
    ("supers", "  of which super blocks"),
]


def print_table(title: str, cards: list) -> None:
    t, feint_atk = tally(cards)
    w = 30
    header = f"  {title}  ({len(cards)} cards)"
    print("=" * (w + 34))
    print(header)
    print("=" * (w + 34))
    print(f"  {'metric':<{w}}{'High':>7}{'Mid':>7}{'Low':>7}{'total':>8}   share H/M/L")
    print("  " + "-" * (w + 32))
    for key, label in ROWS:
        d = t[key]
        tot = sum(d.values())
        share = "  ".join(
            f"{(100 * d[z] / tot):>3.0f}%" if tot else "  - " for z in ZONES)
        print(f"  {label:<{w}}{d['High']:>7}{d['Mid']:>7}{d['Low']:>7}{tot:>8}   {share}")
        print(f"  {'':<{w}}{_bar(d)}")
    if sum(feint_atk.values()):
        fa = feint_atk
        print("  " + "-" * (w + 32))
        print(f"  {'(feint attack marks, no dmg)':<{w}}{fa['High']:>7}{fa['Mid']:>7}{fa['Low']:>7}"
              f"{sum(fa.values()):>8}")
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--by-group", action="store_true",
                    help="also print a per-weapon-group breakdown")
    ap.add_argument("--exclude-ranged", action="store_true",
                    help="drop ranged attacks (almost all block Mid, so they skew the "
                         "block stats) to see the melee-only pool")
    args = ap.parse_args()

    cards = list(load_cards().values())
    label = "mobileSuitGame card pool — zone characterisation"
    if args.exclude_ranged:
        cards = [c for c in cards if not c.ranged]
        label += "  [melee only, ranged excluded]"
    print_table(label, cards)

    if args.by_group:
        groups: dict[str, list] = {}
        for c in cards:
            groups.setdefault(c.group or "(none)", []).append(c)
        for g in sorted(groups):
            print_table(f"group: {g}", groups[g])


if __name__ == "__main__":
    main()
