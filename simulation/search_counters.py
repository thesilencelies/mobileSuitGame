#!/usr/bin/env python3
"""
Dedicated evolutionary search for Counter Decks (ctr_*.csv) vs degenerate/probe leaders.
Optimizes a deck specifically to beat a single target deck with maximum win rate.
"""

import sys
import random
import multiprocessing as mp
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from simulation.simulate import (
    load_cards, load_deck, _run_decks, SimConfig, ZONES
)

_TARGET_DECK = None
_TARGET_NAME = None
_GLOBAL_CARDS = None
_GLOBAL_CFG = None

def _init_counter_worker(target_name, games, pool, health):
    global _TARGET_DECK, _TARGET_NAME, _GLOBAL_CARDS, _GLOBAL_CFG
    _GLOBAL_CARDS = load_cards()
    _TARGET_NAME = target_name
    tgt_path = ROOT / "simulation" / "decks" / "sim" / f"{target_name}.csv"
    _TARGET_DECK = load_deck(str(tgt_path), _GLOBAL_CARDS)
    _GLOBAL_CFG = SimConfig(
        games=games, hand=2, health=health, max_rounds=20,
        intelligent=True, pool=pool
    )

def _eval_counter_cand(keys_tuple):
    global _TARGET_DECK, _TARGET_NAME, _GLOBAL_CARDS, _GLOBAL_CFG
    deck = [_GLOBAL_CARDS[k] for k in keys_tuple if k in _GLOBAL_CARDS]
    if not deck:
        return 0.0
    rng = random.Random(42 + hash(keys_tuple) % 100000)
    res = _run_decks(deck, _TARGET_DECK, _GLOBAL_CFG, rng, name_a="counter", name_b=_TARGET_NAME)
    w = res["wins"]
    tot = w["A"] + w["B"]
    return (w["A"] / tot * 100.0) if tot > 0 else 50.0

def search_counter(target_name, pop_size=40, generations=16, search_games=50, verify_games=200):
    cards = load_cards()
    valid_keys = [k for k, c in cards.items() if c.is_attack or c.blocks]
    sim_dir = ROOT / "simulation" / "decks" / "sim"
    existing_ctr_path = sim_dir / f"ctr_{target_name.replace('deg_', '')}.csv"
    
    print("\n" + "="*70)
    print(f"SEARCHING BEST COUNTER DECK VS {target_name.upper()}")
    print("="*70)
    
    population = []
    if existing_ctr_path.exists():
        existing = [
            line.strip().replace("card/", "")
            for line in existing_ctr_path.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
        if all(k in valid_keys for k in existing):
            population.append(tuple(existing))
            
    while len(population) < pop_size:
        size = random.choice([9, 10, 11, 12])
        population.append(tuple(random.sample(valid_keys, size)))
        
    workers = min(16, mp.cpu_count())
    with mp.Pool(workers, initializer=_init_counter_worker, initargs=(target_name, search_games, 7, 4)) as pool:
        for gen in range(generations):
            scores = pool.map(_eval_counter_cand, population)
            ranked = sorted(zip(population, scores), key=lambda x: x[1], reverse=True)
            cand, score = ranked[0]
            print(f"Gen {gen:2d} | Top counter wr vs {target_name}: {score:5.1f}% | Size: {len(cand)}")
            
            survivors = [c for c, _ in ranked[:max(4, int(pop_size * 0.35))]]
            next_pop = survivors[:]
            while len(next_pop) < pop_size:
                p = list(random.choice(survivors))
                op = random.random()
                if op < 0.7:
                    idx = random.randrange(len(p))
                    p[idx] = random.choice(valid_keys)
                elif op < 0.85 and len(p) < 14:
                    p.append(random.choice(valid_keys))
                elif len(p) > 8:
                    idx = random.randrange(len(p))
                    p.pop(idx)
                next_pop.append(tuple(p))
            population = next_pop

    print(f"\nVerifying top 3 candidates vs {target_name} at {verify_games} games...")
    with mp.Pool(workers, initializer=_init_counter_worker, initargs=(target_name, verify_games, 7, 4)) as pool:
        final_scores = pool.map(_eval_counter_cand, survivors[:3])
    final_ranked = sorted(zip(survivors[:3], final_scores), key=lambda x: x[1], reverse=True)
    best_cand, best_score = final_ranked[0]
    
    print("\n" + "="*70)
    print(f"VERIFIED BEST COUNTER vs {target_name}: {best_score:.1f}%")
    print("="*70)
    for k in best_cand:
        c = cards[k]
        h, m, l = c.attacks
        hb = "H" if "High" in c.blocks else " "
        mb = "M" if "Mid" in c.blocks else " "
        lb = "L" if "Low" in c.blocks else " "
        sb = "!" if c.super_blocks else " "
        print(f"  card/{k:30} | Init {c.initiative} | Atk H{h} M{m} L{l} | Blk [{hb}{mb}{lb}]{sb}")
        
    return best_cand, best_score

if __name__ == "__main__":
    targets = [
        "deg_chainsaw_oneshot",
        "deg_multiblock_pressure",
        "deg_two_damage",
    ]
    for tgt in targets:
        best_cand, score = search_counter(tgt, pop_size=36, generations=15, search_games=50, verify_games=200)
