#!/usr/bin/env python3
"""Refined weapon-quality test: rank each weapon group by its OFFENCE alone, with
its native blocking taken out of the equation.

A pure weapon-group deck couples two things -- how much damage the weapon lands AND
how many zones it can block. Ranged weapons block only Mid, so they leak High/Low and
look weak even when their attacks are fine. To separate the two, we drop each weapon
into a deck that otherwise holds ALL the Basic actions: Basic_Block covers every zone,
so DEFENCE is equalised across weapons and the head-to-head measures offence.

Compared against the pure weapon-group round-robin, the delta shows how much each
weapon's win-rate was really about its (in)ability to block:
  * big positive delta  -> the weapon's problem was BLOCKING (it recovers with cover)
  * ~zero / negative     -> the weapon's problem is OFFENCE (slow, or low damage)

    python simulation/weapon_quality_test.py            # both rankings + delta
    python simulation/weapon_quality_test.py --games 400
"""
from __future__ import annotations
import argparse, random, sys
import multiprocessing as mp
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from simulation.simulate import (load_cards, load_deck, _run_decks, SimConfig,  # noqa: E402
                                 scale_deck, weapon_groups)

_DECKS = None  # worker-local: list of (label, deck)


def _build_decks():
    C = load_cards()
    G = defaultdict(list)
    for c in C.values():
        G[c.group].append(c)
    basics = G["Basic"]
    wg = weapon_groups()
    inb = [(f"{w}", scale_deck(G[w] + basics)) for w in wg]       # weapon + all basics
    inb.append(("(basics only)", scale_deck(basics + basics)))
    pure = [(w, load_deck(f"weapon:{w}", C)) for w in wg]         # pure weapon group
    return inb, pure


def _init(games):
    global _DECKS, _GAMES
    _DECKS = _build_decks()
    _GAMES = games


def _cell(task):
    kind, i, j = task
    decks = _DECKS[0] if kind == "inb" else _DECKS[1]
    cfg = SimConfig(games=_GAMES, hand=2, health=4, max_rounds=20,
                    intelligent=True, pool=7)
    s = _run_decks(decks[i][1], decks[j][1], cfg, random.Random(7))
    w = s["wins"]["A"] + s["wins"]["B"]
    return (kind, i, j, 100.0 * s["wins"]["A"] / w if w else 50.0)


def _means(decks, cells):
    n = len(decks)
    M = [[50.0] * n for _ in range(n)]
    for i, j, wr in cells:
        M[i][j] = wr
    return {decks[i][0]: sum(M[i][j] for j in range(n) if j != i) / (n - 1)
            for i in range(n)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--games", type=int, default=200, help="games per matchup")
    ap.add_argument("--jobs", type=int, default=16, help="worker processes")
    args = ap.parse_args()

    inb, pure = _build_decks()
    tasks = ([("inb", i, j) for i in range(len(inb)) for j in range(len(inb)) if i != j]
             + [("pure", i, j) for i in range(len(pure)) for j in range(len(pure)) if i != j])
    with mp.Pool(args.jobs, initializer=_init, initargs=(args.games,)) as pool:
        res = pool.map(_cell, tasks)
    inb_cells = [(i, j, wr) for k, i, j, wr in res if k == "inb"]
    pure_cells = [(i, j, wr) for k, i, j, wr in res if k == "pure"]
    inb_mean = _means(inb, inb_cells)
    pure_mean = _means(pure, pure_cells)

    print(f"Weapon quality with defence EQUALISED (weapon + all basics), {args.games} "
          f"games/matchup.")
    print(f"  {'weapon':<15}{'offence-only':>13}{'pure-group':>12}{'delta':>7}")
    print("  " + "-" * 47)
    for w in sorted(inb_mean, key=lambda x: -inb_mean[x]):
        p = pure_mean.get(w)
        d = f"{inb_mean[w]-p:+6.0f}" if p is not None else "     -"
        pv = f"{p:>11.0f}%" if p is not None else " " * 12
        print(f"  {w:<15}{inb_mean[w]:>12.0f}%{pv}{d}")
    print("\n  delta > 0: blocking was the weapon's problem (it recovers once covered).")
    print("  delta ~ 0 / < 0: OFFENCE is the problem -- too slow (attacks blocked for "
          "free) or too little damage. Railgun/Assault Rifle stay weak here; the "
          "mono-Mid-block ranged weapons (Missile Rack, Cannon, Stun Baton) recover.")


if __name__ == "__main__":
    main()
