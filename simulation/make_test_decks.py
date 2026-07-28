#!/usr/bin/env python3
"""Generate balance-test decks in simulation/decks/ from REAL cards only.

Each deck fixes one metric and stays as even as the card pool allows across every
other metric (initiative bucket + the free attack/block zone signature). Cards
that both attack and block are preferred. Decks are padded to a uniform size by
cycling the balanced selection, so matchups are fair.

Run:  python simulation/make_test_decks.py
"""

import csv
from collections import Counter
from pathlib import Path

ZONES = ("High", "Mid", "Low")
ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "decks"
CARD_CSVS = ["Weapon actions.csv", "Basic actions.csv", "Booster actions.csv",
             "Drone actions.csv", "Pilot actions.csv"]
DECK_SIZE = 12


def _to_int(v):
    v = (v or "").strip()
    try:
        return int(float(v))
    except ValueError:
        return 0


def load_cards():
    cards = []
    for fname in CARD_CSVS:
        path = ROOT / fname
        if not path.exists():
            continue
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                name, group = (r.get("Name") or "").strip(), (r.get("Group") or "").strip()
                if not name:
                    continue
                atk = tuple(_to_int(r.get(z + "Attack", "")) for z in ZONES)
                blk = tuple(z for z in ZONES if _to_int(r.get(z + "Block", "")) > 0)
                cards.append({
                    "key": f"{group}_{name}",
                    "atk": atk,
                    "azones": tuple(z for z, a in zip(ZONES, atk) if a > 0),
                    "blk": blk,
                    "init": _to_int(r.get("Initiative", "")),
                })
    return cards


def init_bucket(c):
    i = c["init"]
    return "lo" if i <= 3 else ("md" if i <= 5 else "hi")


def balanced_pick(pool, feature_fns, size):
    """Greedily pick `size` cards from `pool`, flattening the histograms produced
    by every function in `feature_fns`. Cards that both attack and block are
    preferred as a tiebreak."""
    chosen, hists = [], [Counter() for _ in feature_fns]
    remaining = list(pool)
    while remaining and len(chosen) < size:
        def cost(card):
            # Sum of current counts for this card's features: lower = flatter.
            return sum(h[f(card)] for h, f in zip(hists, feature_fns))
        best = min(remaining, key=lambda c: (cost(c),
                                             0 if (c["azones"] and c["blk"]) else 1,
                                             c["key"]))
        chosen.append(best)
        remaining.remove(best)
        for h, f in zip(hists, feature_fns):
            h[f(best)] += 1
    return chosen


def write_deck(name, chosen):
    """Pad to DECK_SIZE by cycling the balanced selection, then write the file."""
    if not chosen:
        print(f"  !! {name}: no matching cards")
        return
    cards = [chosen[i % len(chosen)] for i in range(DECK_SIZE)]
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / f"{name}.csv", "w") as fh:
        for c in cards:
            fh.write(f"card/{c['key']}\n")
    inits = Counter(init_bucket(c) for c in cards)
    ablk = Counter(c["blk"] or ("--",) for c in cards)
    print(f"  {name+'.csv':22} {len(chosen):>2} distinct  "
          f"init{dict(inits)}  blockzones{{{', '.join(''.join(z[0] for z in k) or '-' for k in ablk)}}}")


def main():
    cards = load_cards()
    attackers = [c for c in cards if c["azones"]]

    # Every test deck should both attack and block (the requested preference),
    # so balancing never pulls in a do-nothing pure-block or pure-move card.
    both = [c for c in cards if c["azones"] and c["blk"]]
    by_init = lambda c: c["init"]  # exact initiative, so decks span the full range

    # Attack-zone-constrained: fix the (single) attacked zone; balance initiative
    # and block zone across the rest.
    for zone in ZONES:
        pool = [c for c in both if c["azones"] == (zone,)]
        write_deck(f"only_attack_{zone.lower()}",
                   balanced_pick(pool, [by_init, lambda c: c["blk"]], DECK_SIZE))

    # Block-zone-constrained: fix the (single) blocked zone; balance initiative
    # and attacked zone. Restricted to cards that also attack.
    for zone in ZONES:
        pool = [c for c in both if c["blk"] == (zone,)]
        write_deck(f"only_block_{zone.lower()}",
                   balanced_pick(pool, [by_init, lambda c: c["azones"]], DECK_SIZE))

    # Initiative-constrained: fix low/high initiative; balance attack and block
    # zones (and still spread initiative within the band).
    lo = [c for c in both if c["init"] <= 3]
    hi = [c for c in both if c["init"] >= 6]
    write_deck("only_init_low",
               balanced_pick(lo, [by_init, lambda c: c["azones"], lambda c: c["blk"]], DECK_SIZE))
    write_deck("only_init_high",
               balanced_pick(hi, [by_init, lambda c: c["azones"], lambda c: c["blk"]], DECK_SIZE))

    # Even mix: balanced across initiative, attack zone and block zone at once.
    write_deck("even_mix",
               balanced_pick(both, [by_init, lambda c: c["azones"],
                                    lambda c: c["blk"]], DECK_SIZE))


if __name__ == "__main__":
    print("Generating balance-test decks in simulation/decks/ ...")
    main()
    print("done")
