#!/usr/bin/env python3
"""
Evolutionary search for the Robust Allrounder deck and best decks against the field.

Evaluates candidate decks against the standard sim field:
- 9 probe decks: only_attack_{h,m,l}, only_block_{h,m,l}, only_init_{h,l}, even_mix
- 6 degenerate archetypes: deg_chainsaw_oneshot, deg_guardbreak_flood, deg_init_blitz,
                          deg_multiblock_pressure, deg_superguard_control, deg_two_damage
- 2 category decks: only_melee, only_ranged
"""

import sys
import random
import time
import multiprocessing as mp
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from simulation.simulate import (
    load_cards, load_deck, _run_decks, SimConfig, ZONES, Card, scale_deck
)

FIELD_DECK_NAMES = [
    "even_mix",
    "only_attack_high",
    "only_attack_mid",
    "only_attack_low",
    "only_block_high",
    "only_block_mid",
    "only_block_low",
    "only_init_high",
    "only_init_low",
    "only_melee",
    "only_ranged",
    "deg_chainsaw_oneshot",
    "deg_guardbreak_flood",
    "deg_init_blitz",
    "deg_multiblock_pressure",
    "deg_superguard_control",
    "deg_two_damage",
]

_GLOBAL_CARDS = None
_GLOBAL_FIELD = None
_GLOBAL_CFG = None

def _init_worker(games, pool, health):
    global _GLOBAL_CARDS, _GLOBAL_FIELD, _GLOBAL_CFG
    _GLOBAL_CARDS = load_cards()
    field_dir = ROOT / "simulation" / "decks" / "sim"
    _GLOBAL_FIELD = {
        name: load_deck(str(field_dir / f"{name}.csv"), _GLOBAL_CARDS)
        for name in FIELD_DECK_NAMES
    }
    _GLOBAL_CFG = SimConfig(
        games=games, hand=2, health=health, max_rounds=20,
        intelligent=True, pool=pool
    )

def _eval_candidate(keys_tuple):
    global _GLOBAL_CARDS, _GLOBAL_FIELD, _GLOBAL_CFG
    deck = [_GLOBAL_CARDS[k] for k in keys_tuple if k in _GLOBAL_CARDS]
    if not deck:
        return 0.0, {}
    
    rng = random.Random(42 + hash(keys_tuple) % 100000)
    scores = {}
    for name, opp_deck in _GLOBAL_FIELD.items():
        res = _run_decks(deck, opp_deck, _GLOBAL_CFG, rng, name_a="cand", name_b=name)
        wins = res["wins"]
        total = wins["A"] + wins["B"]
        rate = (wins["A"] / total * 100.0) if total > 0 else 50.0
        scores[name] = rate
    mean_score = sum(scores.values()) / len(scores)
    return mean_score, scores

def run_evolution(pop_size=40, generations=20, deck_len=11, search_games=40, verify_games=200):
    cards = load_cards()
    valid_keys = [
        k for k, c in cards.items() 
        if c.is_attack or c.blocks
    ]
    print(f"Pool has {len(valid_keys)} usable cards across all groups.")
    
    sim_dir = ROOT / "simulation" / "decks" / "sim"
    current_robust_path = sim_dir / "robust_allrounder.csv"
    current_robust = [
        line.strip().replace("card/", "") 
        for line in current_robust_path.read_text().splitlines() 
        if line.strip() and not line.startswith("#")
    ]
    
    population = []
    if all(k in valid_keys for k in current_robust):
        population.append(tuple(current_robust))
    
    while len(population) < pop_size:
        size = random.choice([10, 11, 12])
        deck = tuple(random.sample(valid_keys, size))
        population.append(deck)
    
    workers = min(16, mp.cpu_count())
    print(f"Starting evolutionary search: {pop_size} decks, {generations} gens on {workers} workers ({search_games} games/matchup)...")
    
    with mp.Pool(workers, initializer=_init_worker, initargs=(search_games, 7, 4)) as pool:
        for gen in range(generations):
            results = pool.map(_eval_candidate, population)
            ranked = sorted(zip(population, results), key=lambda x: x[1][0], reverse=True)
            best_deck, (best_mean, best_scores) = ranked[0]
            print(f"Gen {gen:2d} | Best Mean: {best_mean:5.1f}% | Size: {len(best_deck)}")
            
            survivor_count = max(4, int(pop_size * 0.35))
            survivors = [cand for cand, _ in ranked[:survivor_count]]
            
            next_pop = survivors[:]
            while len(next_pop) < pop_size:
                parent = random.choice(survivors)
                mutant = list(parent)
                op = random.random()
                if op < 0.7:
                    idx = random.randrange(len(mutant))
                    mutant[idx] = random.choice(valid_keys)
                elif op < 0.85 and len(mutant) < 13:
                    mutant.append(random.choice(valid_keys))
                elif len(mutant) > 9:
                    idx = random.randrange(len(mutant))
                    mutant.pop(idx)
                next_pop.append(tuple(mutant))
            population = next_pop

    print("\nSearch complete! Verifying top 3 candidates at high game count (200 games)...")
    with mp.Pool(workers, initializer=_init_worker, initargs=(verify_games, 7, 4)) as pool:
        final_results = pool.map(_eval_candidate, survivors[:3])
    
    final_ranked = sorted(zip(survivors[:3], final_results), key=lambda x: x[1][0], reverse=True)
    best_deck, (best_mean, best_scores) = final_ranked[0]
    
    print("\n" + "="*70)
    print(f"VERIFIED ROBUST ALLROUNDER (Mean Win Rate: {best_mean:.1f}%)")
    print("="*70)
    print(f"Deck Size: {len(best_deck)} cards")
    for k in best_deck:
        c = cards[k]
        h, m, l = c.attacks
        hb = "H" if "High" in c.blocks else " "
        mb = "M" if "Mid" in c.blocks else " "
        lb = "L" if "Low" in c.blocks else " "
        print(f"  card/{k:30} | Init {c.initiative} | Atk H{h} M{m} L{l} | Blk [{hb}{mb}{lb}]")
    
    print("\nHead-to-head win rates vs the field:")
    for opp, wr in sorted(best_scores.items(), key=lambda x: -x[1]):
        print(f"  vs {opp:25}: {wr:5.1f}%")
        
    return best_deck, best_mean, best_scores


def search_weapon6(pop_size=30, generations=15, games=35, verify_games=150):
    cards = load_cards()
    groups_dict = Counter(c.group for c in cards.values())
    # Exclude non-weapon groups like Basic, Booster, etc.
    from simulation.simulate import weapon_groups
    wg = weapon_groups()
    print("\n" + "="*70)
    print("SEARCHING BEST 6-WEAPON-GROUP DECK AGAINST THE FIELD (W6 ROBUST)")
    print("="*70)
    print(f"Eligible weapon groups ({len(wg)}): {sorted(wg)}")
    
    # Each deck is a tuple of 6 group names
    group_cards = {w: [c.key for c in cards.values() if c.group == w] for w in wg}
    
    def groups_to_keys(g_tuple):
        keys = []
        for g in g_tuple:
            keys.extend(group_cards[g])
        return tuple(keys)
        
    current_w6_robust = ("Assault Rifle", "Chainsaw", "Halberd", "Halberd", "Spear", "Stun Baton")
    population = [current_w6_robust]
    while len(population) < pop_size:
        population.append(tuple(random.choices(wg, k=6)))
        
    workers = min(16, mp.cpu_count())
    with mp.Pool(workers, initializer=_init_worker, initargs=(games, 7, 4)) as pool:
        for gen in range(generations):
            keys_pop = [groups_to_keys(g_tuple) for g_tuple in population]
            results = pool.map(_eval_candidate, keys_pop)
            ranked = sorted(zip(population, results), key=lambda x: x[1][0], reverse=True)
            best_g, (best_mean, best_scores) = ranked[0]
            print(f"Gen {gen:2d} | Best W6 Mean: {best_mean:5.1f}% | Groups: {Counter(best_g)}")
            
            survivor_count = max(4, int(pop_size * 0.35))
            survivors = [cand for cand, _ in ranked[:survivor_count]]
            
            next_pop = survivors[:]
            while len(next_pop) < pop_size:
                parent = list(random.choice(survivors))
                # Mutate 1 or 2 groups
                idx = random.randrange(6)
                parent[idx] = random.choice(wg)
                if random.random() < 0.3:
                    idx2 = random.randrange(6)
                    parent[idx2] = random.choice(wg)
                next_pop.append(tuple(sorted(parent)))
            population = next_pop

    print("\nVerifying top 3 W6 candidates at 150 games...")
    top_keys = [groups_to_keys(g) for g in survivors[:3]]
    with mp.Pool(workers, initializer=_init_worker, initargs=(verify_games, 7, 4)) as pool:
        final_results = pool.map(_eval_candidate, top_keys)
        
    final_ranked = sorted(zip(survivors[:3], final_results), key=lambda x: x[1][0], reverse=True)
    best_g, (best_mean, best_scores) = final_ranked[0]
    
    print("\n" + "="*70)
    print(f"VERIFIED BEST 6-GROUP DECK: {Counter(best_g)} (Mean: {best_mean:.1f}%)")
    print("="*70)
    for opp, wr in sorted(best_scores.items(), key=lambda x: -x[1]):
        print(f"  vs {opp:25}: {wr:5.1f}%")
        
    return best_g, best_mean, best_scores


def search_counter(target_name="deg_multiblock_pressure", pop_size=30, generations=12, games=60):
    from simulation.search_counters import search_counter as _sc
    return _sc(target_name=target_name, pop_size=pop_size, generations=generations, search_games=games, verify_games=150)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Search Allrounder and W6 robust decks")
    parser.add_argument("--allrounder", action="store_true", help="Search robust allrounder deck")
    parser.add_argument("--w6", action="store_true", help="Search best 6-weapon-group deck")
    parser.add_argument("--counter", metavar="TARGET", help="Search counter deck against target")
    args = parser.parse_args()

    if args.counter:
        search_counter(args.counter)
    elif args.w6:
        search_weapon6()
    else:
        run_evolution()

