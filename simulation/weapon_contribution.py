#!/usr/bin/env python3
"""Weapon power measured as CONTRIBUTION, not solo strength.

A weapon's real worth is what it adds to a deck that is already doing other things.
Sword lands little damage but resolves early; Great Axe hits hard but resolves last --
each is weak alone, together they cover. Solo win-rate misses that. So we measure the
average MARGINAL contribution of adding a weapon to a deck that already runs another:

    contribution(X | Y) = winrate(X + Y + basics)  -  winrate(Y + basics)      (vs a fixed field)
    power(X)            = mean over Y != X of contribution(X | Y)

i.e. how much better a one-weapon deck gets, on average, when you add weapon X -- an
approximate Shapley value over weapon coalitions. The fixed field is every
single-weapon-in-basics deck plus basics-only, so defence is always covered by
Basic_Block and the number reflects offence/synergy, not blocking.

    python simulation/weapon_contribution.py            # power(X) + solo, and top synergies
    python simulation/weapon_contribution.py --games 80
"""
from __future__ import annotations
import argparse, itertools, random, sys
import multiprocessing as mp
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from simulation.simulate import (load_cards, _run_decks, SimConfig,  # noqa: E402
                                 scale_deck, weapon_groups)

_STATE = None  # (candidates, field) built once per worker


def _build():
    C = load_cards()
    G = defaultdict(list)
    for c in C.values():
        G[c.group].append(c)
    basics = G["Basic"]
    wg = weapon_groups()
    solo = {w: scale_deck(G[w] + basics) for w in wg}
    pair = {frozenset((a, b)): scale_deck(G[a] + G[b] + basics)
            for a, b in itertools.combinations(wg, 2)}
    field = [scale_deck(G[w] + basics) for w in wg] + [scale_deck(basics + basics)]
    avg_init = {w: sum(c.initiative for c in G[w]) / len(G[w]) for w in wg}
    return wg, solo, pair, field, avg_init


def _init(games):
    global _STATE, _GAMES
    _STATE = _build()
    _GAMES = games


def _wr_vs_field(task):
    kind, key = task
    wg, solo, pair, field, _ = _STATE
    deck = solo[key] if kind == "solo" else pair[key]
    cfg = SimConfig(games=_GAMES, hand=2, health=4, max_rounds=20,
                    intelligent=True, pool=7)
    rs = []
    for od in field:
        s1 = _run_decks(deck, od, cfg, random.Random(7))
        s2 = _run_decks(od, deck, cfg, random.Random(7))
        wa = s1["wins"]["A"] + s2["wins"]["B"]
        wb = s1["wins"]["B"] + s2["wins"]["A"]
        rs.append(100.0 * wa / (wa + wb) if (wa + wb) else 50.0)
    return (kind, key, sum(rs) / len(rs))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--games", type=int, default=60, help="games per matchup")
    ap.add_argument("--jobs", type=int, default=16)
    args = ap.parse_args()

    wg, solo, pair, field, avg_init = _build()
    tasks = ([("solo", w) for w in wg]
             + [("pair", k) for k in pair])
    with mp.Pool(args.jobs, initializer=_init, initargs=(args.games,)) as pool:
        res = pool.map(_wr_vs_field, tasks, chunksize=4)
    solo_wr, pair_wr = {}, {}
    for kind, key, wr in res:
        (solo_wr if kind == "solo" else pair_wr)[key] = wr

    # contribution(X|Y) = pair(X,Y) - solo(Y);  power(X) = mean over Y of that
    contrib = defaultdict(dict)
    for a, b in itertools.combinations(wg, 2):
        p = pair_wr[frozenset((a, b))]
        contrib[a][b] = p - solo_wr[b]   # X=a added to a b-deck
        contrib[b][a] = p - solo_wr[a]
    power = {x: sum(contrib[x].values()) / len(contrib[x]) for x in wg}

    print(f"Weapon CONTRIBUTION power (avg lift when added to another weapon's deck), "
          f"{args.games} games:")
    print(f"  {'weapon':<15}{'power':>7}{'solo':>7}{'rank_delta':>11}   (power - solo)")
    print("  " + "-" * 50)
    solo_rank = {w: r for r, w in enumerate(sorted(wg, key=lambda x: -solo_wr[x]), 1)}
    pow_rank = {w: r for r, w in enumerate(sorted(wg, key=lambda x: -power[x]), 1)}
    for w in sorted(wg, key=lambda x: -power[x]):
        rd = solo_rank[w] - pow_rank[w]  # + = better as a contributor than solo
        print(f"  {w:<15}{power[w]:>+7.1f}{solo_wr[w]:>7.0f}{rd:>+11d}")

    # mutual synergy: pairs where BOTH weapons lift each other the most
    syn = []
    for a, b in itertools.combinations(wg, 2):
        syn.append((min(contrib[a][b], contrib[b][a]), pair_wr[frozenset((a, b))], a, b))
    print("\n  Top complementary pairs (both partners gain the most; win% = pair vs field):")
    for m, p, a, b in sorted(syn, reverse=True)[:12]:
        print(f"    +{m:4.1f} min-lift  {p:4.0f}% pair   {a}+{b}  "
              f"(init {avg_init[a]:.1f}/{avg_init[b]:.1f})")

    # the user's example
    if "Sword" in wg and "Great Axe" in wg:
        print(f"\n  example -- Sword(init {avg_init['Sword']:.1f}) + "
              f"Great Axe(init {avg_init['Great Axe']:.1f}):")
        print(f"    solo Sword {solo_wr['Sword']:.0f}%, solo Great Axe {solo_wr['Great Axe']:.0f}%, "
              f"pair {pair_wr[frozenset(('Sword','Great Axe'))]:.0f}%")
        print(f"    Sword adds {contrib['Sword']['Great Axe']:+.0f} to a Great Axe deck; "
              f"Great Axe adds {contrib['Great Axe']['Sword']:+.0f} to a Sword deck")


if __name__ == "__main__":
    main()
