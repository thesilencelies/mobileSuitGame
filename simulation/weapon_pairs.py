#!/usr/bin/env python3
"""Two-weapon pairing tournament: which weapon PAIRS (plus all the Basic actions)
perform best, at any team size.

Weapons come in groups and are played alongside others, so a weapon's worth is partly
what it enables in company. This drops every pair of weapon groups into a deck that
also holds all the Basic actions (Basic_Block covers every zone) and runs a round
robin, in two stages so the full 171-pair grid stays affordable:

  1. coarse pass over ALL pairs at a low game count -> keep the top fraction
  2. refine that top fraction (plus every solo weapon and basics-only, as anchors)
     at a higher game count

Reports the strongest pairs, how often each weapon appears among the top pairs (0 =
never a good partner -- a weakness to fix), and the best pair vs the best solo.

Team size matters: at 1v1 well-rounded weapons win; at 2v2/3v3 both frames focus-fire
the enemy target, which it can't fully block, so alpha-strike (one-shot) weapons like
Chainsaw take over. Use --team-size to see the shift.

    python simulation/weapon_pairs.py                       # 1v1
    python simulation/weapon_pairs.py --team-size 2         # 2v2 (extra pressure)
    python simulation/weapon_pairs.py --team-size 2 --stage1-games 24 --stage2-games 200
"""
from __future__ import annotations
import argparse, itertools, random, sys
import multiprocessing as mp
from collections import defaultdict, Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from simulation.simulate import (load_cards, SimConfig, scale_deck, weapon_groups,  # noqa: E402
                                 Frame, Pile, _apply_profiles, _collect_stats)

_STATE = None  # worker-local: (decks, n_pairs, team_size, stage_games_holder)


def _build():
    C = load_cards()
    G = defaultdict(list)
    for c in C.values():
        G[c.group].append(c)
    basics = G["Basic"]
    wg = weapon_groups()
    pair_decks = [(f"{a}+{b}", scale_deck(G[a] + G[b] + basics))
                  for a, b in itertools.combinations(wg, 2)]
    solo_decks = [(f"{w}(solo)", scale_deck(G[w] + basics)) for w in wg]
    basics_deck = [("(basics)", scale_deck(basics + basics))]
    return wg, pair_decks + solo_decks + basics_deck, len(pair_decks)


def run_teamed(deck_a, deck_b, n, cfg, rng):
    ta = [Frame(name=f"A{i}", team="A", pile=Pile(list(deck_a), rng)) for i in range(n)]
    tb = [Frame(name=f"B{i}", team="B", pile=Pile(list(deck_b), rng)) for i in range(n)]
    ta[0].is_target = True
    tb[0].is_target = True
    if cfg.intelligent:
        _apply_profiles(ta, tb, cfg)
    return _collect_stats(ta, ta[0], tb, tb[0], cfg, rng)


def _init(team_size):
    global _STATE, _TEAM
    wg, decks, npairs = _build()
    _STATE = (wg, decks, npairs)
    _TEAM = team_size


def _cell(task):
    i, j, games = task
    _, decks, _ = _STATE
    cfg = SimConfig(games=games, hand=2, health=4, max_rounds=20,
                    intelligent=True, pool=7)
    s = run_teamed(decks[i][1], decks[j][1], _TEAM, cfg, random.Random(7))
    w = s["wins"]["A"] + s["wins"]["B"]
    return (i, j, 100.0 * s["wins"]["A"] / w if w else 50.0)


def _round_robin(indices, games, pool):
    idx = list(indices)
    tasks = [(i, j, games) for i in idx for j in idx if i != j]
    res = pool.map(_cell, tasks, chunksize=64)
    wins = defaultdict(list)
    for i, j, wr in res:
        wins[i].append(wr)
    return {i: sum(v) / len(v) for i, v in wins.items()}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--team-size", type=int, default=1, help="frames per side (1=1v1, 2=2v2 ...)")
    ap.add_argument("--stage1-games", type=int, default=24, help="games/cell, coarse pass")
    ap.add_argument("--stage2-games", type=int, default=200, help="games/cell, refined pass")
    ap.add_argument("--top-frac", type=float, default=0.30, help="fraction of pairs to refine")
    ap.add_argument("--jobs", type=int, default=16)
    args = ap.parse_args()

    wg, decks, npairs = _build()
    n = args.team_size
    pool = mp.Pool(args.jobs, initializer=_init, initargs=(n,))
    try:
        print(f"[{n}v{n}] STAGE 1: all {npairs} weapon pairs (+basics), "
              f"{args.stage1_games} games/cell ({npairs*(npairs-1)} cells)...", flush=True)
        m1 = _round_robin(range(npairs), args.stage1_games, pool)
        ranked = sorted(range(npairs), key=lambda i: -m1[i])
        ncut = max(1, int(npairs * args.top_frac))
        top = ranked[:ncut]
        print(f"  cutoff: top {ncut} pairs, win% >= {m1[top[-1]]:.1f}")
        print("  --- stage-1 top 15 ---")
        for i in ranked[:15]:
            print(f"    {m1[i]:5.1f}%  {decks[i][0]}")

        print(f"\n[{n}v{n}] STAGE 2: top {ncut} pairs + solos + basics, "
              f"{args.stage2_games} games/cell...", flush=True)
        field = top + list(range(npairs, len(decks)))
        m2 = _round_robin(field, args.stage2_games, pool)
    finally:
        pool.close()

    field_sorted = sorted(field, key=lambda i: -m2[i])
    print("  --- stage-2 ranking (pairs vs solos vs basics) ---")
    for i in field_sorted:
        tag = "PAIR" if i < npairs else ("solo" if decks[i][0].endswith("(solo)") else "base")
        print(f"    {m2[i]:5.1f}%  [{tag}] {decks[i][0]}")

    top_pairs = [i for i in field_sorted if i < npairs]
    best_solo = max((i for i in field if i >= npairs and decks[i][0].endswith("(solo)")),
                    key=lambda i: m2[i])
    print(f"\n  best pair: {decks[top_pairs[0]][0]} {m2[top_pairs[0]]:.1f}%   "
          f"best solo: {decks[best_solo][0]} {m2[best_solo]:.1f}%")
    freq = Counter()
    for i in top_pairs:
        a, b = decks[i][0].split("+")
        freq[a] += 1
        freq[b] += 1
    print("\n  appearances across the top pairs (0 = never a good partner = weak):")
    for w in sorted(wg, key=lambda x: -freq[x]):
        print(f"    {freq[w]:>2}x  {w}")


if __name__ == "__main__":
    main()
