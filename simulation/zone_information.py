#!/usr/bin/env python3
"""Information-theoretic analysis of zone selection (High / Mid / Low).

Measures to what extent observable tactical contexts (engagement distance,
movement reach, elevation advantage, stripped armor, and weapon groups)
provide actionable information about what an opponent can or must choose,
evaluating whether zone combat is blind rock-paper-scissors or tactical choice.

Key concepts:
  - Shannon Entropy H(Z): uncertainty over {High, Mid, Low}. Max = log2(3) ~ 1.585 bits.
  - Conditional Entropy H(Z | Context): remaining uncertainty given observable context.
  - Mutual Information / Information Gain I(Z; Context) = H(Z) - H(Z | Context):
    bits of uncertainty eliminated by observing the tactical situation.

Run:
  python3 simulation/zone_information.py --real
  python3 simulation/zone_information.py --w6
  python3 simulation/zone_information.py --all --markdown
  python3 simulation/zone_information.py --distance-curve
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from simulation.simulate import (  # noqa: E402
    Card,
    ZONES,
    load_cards,
    load_deck,
    real_deck_paths,
)

MAX_ENTROPY = math.log2(3.0)  # ~1.58496 bits (uniform 1/3, 1/3, 1/3)


def entropy(dist: dict[str, float]) -> float:
    """Shannon entropy in bits for a distribution over ZONES."""
    total = sum(dist.values())
    if total <= 0:
        return 0.0
    h = 0.0
    for z in ZONES:
        p = dist.get(z, 0.0) / total
        if p > 1e-12:
            h -= p * math.log2(p)
    return h


def distribution_from_counts(counts: dict[str, float]) -> dict[str, float]:
    total = sum(counts.values())
    if total <= 0:
        return {z: 0.0 for z in ZONES}
    return {z: counts.get(z, 0.0) / total for z in ZONES}


def can_attack_at_distance(card: Card, dist: int, base_mv: int = 4) -> bool:
    """Determine if a card can execute an attack against a target at distance `dist`.

    Card resolution allows moving up to max(0, base_mv + card.movement) steps.
    - For melee: requires closing to adjacency (distance 1). Needed move = dist - 1.
    - For ranged: requires distance in [2, max_range + max(0, base_mv + card.movement)].
    """
    if not card.is_attack:
        return False
    reach = max(0, base_mv + card.movement)
    if card.ranged:
        max_r = card.max_range
        # Can attack from range if target is within reach + max_range, and dist >= 2
        return dist <= max_r + reach and (dist >= 2 or card.ranges[1] > 0 or any(r > 0 for r in card.ranges))
    else:
        # Melee requires closing to dist 1
        needed_steps = max(0, dist - 1)
        return needed_steps <= reach


def effective_attack_zones(card: Card, delta_elev: int) -> dict[str, int]:
    """Calculate the landing attack zones and damage after elevation shift.

    delta_elev = attacker_elev - defender_elev.
    - Ranged attacks: unaffected.
    - Melee attacks:
      - If attacker is higher (delta > 0): shifted UP (Low -> Mid, Mid -> High, High whiffs).
      - If attacker is lower (delta < 0): shifted DOWN (High -> Mid, Mid -> Low, Low whiffs).
    """
    if not card.is_attack or card.feint:
        return {z: 0 for z in ZONES}
    if card.ranged or delta_elev == 0:
        return {z: a for z, a in zip(ZONES, card.attacks)}

    shifted = {z: 0 for z in ZONES}
    for z, dmg in zip(ZONES, card.attacks):
        if dmg <= 0:
            continue
        idx = ZONES.index(z) - delta_elev
        if 0 <= idx < len(ZONES):
            shifted[ZONES[idx]] += dmg
    return shifted


def deck_zone_profile(deck: list[Card]) -> dict:
    """Unconditioned prior attack damage and block counts."""
    atk_dmg = {z: 0 for z in ZONES}
    atk_cards = {z: 0 for z in ZONES}
    blocks = {z: 0 for z in ZONES}
    for c in deck:
        for z in c.blocks:
            blocks[z] += 1
        if c.is_attack and not c.feint:
            for z, a in zip(ZONES, c.attacks):
                if a > 0:
                    atk_cards[z] += 1
                    atk_dmg[z] += a
    return {
        "atk_dmg": atk_dmg,
        "atk_cards": atk_cards,
        "blocks": blocks,
        "atk_entropy": entropy(atk_dmg),
        "blk_entropy": entropy(blocks),
    }


def analyze_distance_spread(
    deck: list[Card], distances: list[int] = None, base_mv: int = 4
) -> list[dict]:
    """Analyze attack availability and block expectations across a distance spread."""
    if distances is None:
        distances = list(range(1, 9))

    rows = []
    for d in distances:
        reachable_attacks = [c for c in deck if can_attack_at_distance(c, d, base_mv)]
        atk_dmg = {z: 0 for z in ZONES}
        blk_counts = {z: 0 for z in ZONES}
        n_ranged = 0
        n_melee = 0

        for c in reachable_attacks:
            if c.ranged:
                n_ranged += 1
            else:
                n_melee += 1
            for z, a in zip(ZONES, c.attacks):
                if a > 0 and not c.feint:
                    atk_dmg[z] += a

            # When an opponent executes an attack, their card is played as an action.
            # Its block profile represents the defensive coverage held from that action!
            for z in c.blocks:
                blk_counts[z] += 1

        total_reachable = len(reachable_attacks)
        pct_ranged = (100.0 * n_ranged / total_reachable) if total_reachable else 0.0
        h_atk = entropy(atk_dmg)
        h_blk = entropy(blk_counts)

        rows.append({
            "distance": d,
            "reachable_count": total_reachable,
            "pct_ranged": pct_ranged,
            "atk_dmg": atk_dmg,
            "blk_counts": blk_counts,
            "atk_entropy": h_atk,
            "blk_entropy": h_blk,
            "atk_info_gain": MAX_ENTROPY - h_atk,
            "blk_info_gain": MAX_ENTROPY - h_blk,
        })
    return rows


def analyze_elevation(deck: list[Card]) -> dict:
    """Analyze attack zone distribution under elevation differences."""
    results = {}
    for delta in (-1, 0, 1):
        dmg = {z: 0 for z in ZONES}
        for c in deck:
            if c.is_attack and not c.feint:
                eff = effective_attack_zones(c, delta)
                for z, a in eff.items():
                    dmg[z] += a
        results[delta] = {
            "dmg": dmg,
            "entropy": entropy(dmg),
            "info_gain": MAX_ENTROPY - entropy(dmg),
        }
    return results


def analyze_stripped_zone(deck: list[Card], stripped_zone: str) -> dict:
    """Analyze player attack intent and block need when one zone's armor is stripped."""
    # When a zone is stripped, attacks into that zone kill outright.
    # A rational player prioritizes cards that hit the stripped zone.
    matching_atks = [c for c in deck if c.is_attack and not c.feint and c.attacks[ZONES.index(stripped_zone)] > 0]
    matching_blks = [c for c in deck if stripped_zone in c.blocks]
    return {
        "matching_atks_count": len(matching_atks),
        "matching_blks_count": len(matching_blks),
        "pct_cards_can_kill": 100.0 * len(matching_atks) / (len(deck) or 1),
        "pct_cards_can_save": 100.0 * len(matching_blks) / (len(deck) or 1),
    }


def is_heavy_melee_attack(card: Card) -> bool:
    """True if card is a heavy melee attack (deals 3+ in one zone or 4+ total damage)."""
    return card.is_attack and not card.feint and not card.ranged and (max(card.attacks) >= 3 or sum(card.attacks) >= 4)


def analyze_attack_type_blocks(deck: list[Card]) -> dict:
    """Analyze the block distribution of Heavy Melee vs Ranged vs Light Melee attacks."""
    categories = {"heavy_melee": [], "ranged": [], "light_melee": []}
    for c in deck:
        if not c.is_attack or c.feint:
            continue
        if c.ranged:
            categories["ranged"].append(c)
        elif is_heavy_melee_attack(c):
            categories["heavy_melee"].append(c)
        else:
            categories["light_melee"].append(c)

    res = {}
    for cat, clist in categories.items():
        blk = {z: 0 for z in ZONES}
        for c in clist:
            for z in c.blocks:
                blk[z] += 1
        res[cat] = {
            "count": len(clist),
            "blocks": blk,
            "entropy": entropy(blk),
            "info_gain": (MAX_ENTROPY - entropy(blk)) if len(clist) > 0 else 0.0,
        }
    return res


def analyze_deck_full(deck_name: str, deck: list[Card], base_mv: int = 4) -> dict:
    """Comprehensive information profile for a single deck."""
    prior = deck_zone_profile(deck)
    dist_spread = analyze_distance_spread(deck, list(range(1, 9)), base_mv)
    elev = analyze_elevation(deck)
    stripped = {z: analyze_stripped_zone(deck, z) for z in ZONES}
    atk_types = analyze_attack_type_blocks(deck)

    return {
        "name": deck_name,
        "size": len(deck),
        "prior": prior,
        "dist_spread": dist_spread,
        "elevation": elev,
        "stripped": stripped,
        "attack_types": atk_types,
    }


def print_deck_summary(prof: dict, markdown: bool = False) -> None:
    name = prof["name"]
    prior = prof["prior"]
    print("=" * 72)
    print(f"DECK: {name} ({prof['size']} cards)")
    print("=" * 72)
    print(f"Prior Attack Dist: High={prior['atk_dmg']['High']} Mid={prior['atk_dmg']['Mid']} Low={prior['atk_dmg']['Low']}  |  H={prior['atk_entropy']:.3f} bits (Max {MAX_ENTROPY:.3f})")
    print(f"Prior Block Dist:  High={prior['blocks']['High']} Mid={prior['blocks']['Mid']} Low={prior['blocks']['Low']}  |  H={prior['blk_entropy']:.3f} bits")
    print()

    print("--- 1D Distance & Reach Analysis ---")
    header = f"{'Dist':<5}{'Reach':<7}{'%Ranged':<9}{'Atk H/M/L':<15}{'Blk H/M/L':<15}{'H(Blk)':<8}{'Blk InfoGain':<12}"
    print(header)
    print("-" * len(header))
    for r in prof["dist_spread"]:
        ad = r["atk_dmg"]
        bd = r["blk_counts"]
        atk_str = f"{ad['High']}/{ad['Mid']}/{ad['Low']}"
        blk_str = f"{bd['High']}/{bd['Mid']}/{bd['Low']}"
        print(f"{r['distance']:<5}{r['reachable_count']:<7}{r['pct_ranged']:<8.0f}% {atk_str:<15}{blk_str:<15}{r['blk_entropy']:<8.3f}{r['blk_info_gain']:<12.3f}")
    print()

    print("--- Elevation Shifts (Attacker delta relative to Defender) ---")
    for delta, label in [(-1, "Attacker Lower (melee shifts DOWN)"),
                         (0, "Level Ground"),
                         (1, "Attacker Higher (melee shifts UP)")]:
        ed = prof["elevation"][delta]
        dmg = ed["dmg"]
        print(f"  {label:<38}: H={dmg['High']:2d} M={dmg['Mid']:2d} L={dmg['Low']:2d}  |  H(Atk)={ed['entropy']:.3f} bits  |  InfoGain={ed['info_gain']:.3f} bits")
    print()


def print_fleet_summary(profiles: list[dict], markdown: bool = False) -> None:
    """Print multi-deck aggregate summary."""
    print("=" * 80)
    print("AGGREGATE ZONE PREDICTABILITY SUMMARY ACROSS ALL DECKS")
    print("=" * 80)

    # Average prior entropies
    avg_atk_ent = sum(p["prior"]["atk_entropy"] for p in profiles) / len(profiles)
    avg_blk_ent = sum(p["prior"]["blk_entropy"] for p in profiles) / len(profiles)

    print(f"Number of decks analyzed: {len(profiles)}")
    print(f"Theoretical Max Entropy (Pure RPS Uniform): {MAX_ENTROPY:.3f} bits")
    print(f"Average Deck Attack Prior Entropy:          {avg_atk_ent:.3f} bits (Uncertainty reduction: {MAX_ENTROPY - avg_atk_ent:.3f} bits)")
    print(f"Average Deck Block Prior Entropy:           {avg_blk_ent:.3f} bits (Uncertainty reduction: {MAX_ENTROPY - avg_blk_ent:.3f} bits)")
    print()

    print("Average Block Entropy vs 1D Engagement Distance:")
    print(f"{'Distance':<10}{'Avg % Ranged':<15}{'Avg H(Block)':<15}{'Avg Blk InfoGain':<18}{'Tactical Implication'}")
    print("-" * 80)
    for i in range(len(profiles[0]["dist_spread"])):
        dist = profiles[0]["dist_spread"][i]["distance"]
        pct_r = sum(p["dist_spread"][i]["pct_ranged"] for p in profiles) / len(profiles)
        h_blk = sum(p["dist_spread"][i]["blk_entropy"] for p in profiles) / len(profiles)
        ig = MAX_ENTROPY - h_blk
        if dist == 1:
            implication = "Full melee repertoire active; highest entropy"
        elif dist <= 3:
            implication = "Heavy melee (move -3/-4) pruned; fast melee dominates"
        elif dist <= 5:
            implication = "Only move +1 melee reaches; ranged attacks surge"
        else:
            implication = "Melee impossible; 100% ranged -> MID BLOCK LOCK"
        print(f"{dist:<10}{pct_r:>11.1f}%   {h_blk:>12.3f}   {ig:>14.3f} bits   {implication}")
    print()

    print("Average Attack Landing Distribution vs Elevation Advantage:")
    for delta, label in [(-1, "Attacker Lower (melee shifts DOWN)"),
                         (0, "Level Ground"),
                         (1, "Attacker Higher (melee shifts UP)")]:
        avg_ent = sum(p["elevation"][delta]["entropy"] for p in profiles) / len(profiles)
        tot_h = sum(p["elevation"][delta]["dmg"]["High"] for p in profiles)
        tot_m = sum(p["elevation"][delta]["dmg"]["Mid"] for p in profiles)
        tot_l = sum(p["elevation"][delta]["dmg"]["Low"] for p in profiles)
        tot = tot_h + tot_m + tot_l or 1
        print(f"  {label:<38}: High={100*tot_h/tot:4.1f}%  Mid={100*tot_m/tot:4.1f}%  Low={100*tot_l/tot:4.1f}%  |  Avg H={avg_ent:.3f} bits")
    print()

    print("Block Distribution by Attack Category (Design Rules & Heuristics):")
    print(f"{'Attack Category':<22}{'Cards':>7}{'Blk High':>10}{'Blk Mid':>10}{'Blk Low':>10}{'H(Block)':>10}{'InfoGain':>12}")
    print("-" * 80)
    for cat, label in [("heavy_melee", "Heavy Melee (3+ dmg)"),
                       ("ranged", "Ranged Attacks"),
                       ("light_melee", "Light Melee (<3 dmg)")]:
        cnt = sum(p["attack_types"][cat]["count"] for p in profiles)
        bh = sum(p["attack_types"][cat]["blocks"]["High"] for p in profiles)
        bm = sum(p["attack_types"][cat]["blocks"]["Mid"] for p in profiles)
        bl = sum(p["attack_types"][cat]["blocks"]["Low"] for p in profiles)
        tot_b = bh + bm + bl or 1
        h = entropy({"High": bh, "Mid": bm, "Low": bl})
        ig = MAX_ENTROPY - h
        print(f"{label:<22}{cnt:>7}{100*bh/tot_b:>9.1f}%{100*bm/tot_b:>9.1f}%{100*bl/tot_b:>9.1f}%{h:>10.3f}{ig:>11.3f} bits")
    print()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--real", action="store_true", help="Analyze real handmade decks from decks/deck_*.csv")
    parser.add_argument("--w6", action="store_true", help="Analyze 6-weapon archetype decks from simulation/decks/weapon6/")
    parser.add_argument("--base5w1p", action="store_true", help="Analyze 5w+1p archetype decks from simulation/decks/base5w1p/")
    parser.add_argument("--sim", action="store_true", help="Analyze balance test decks from simulation/decks/sim/")
    parser.add_argument("--all", action="store_true", help="Analyze all available decks")
    parser.add_argument("--deck", type=str, help="Specific deck path to analyze")
    parser.add_argument("--markdown", action="store_true", help="Format output as markdown")
    parser.add_argument("--distance-curve", action="store_true", help="Print distance vs entropy curve")
    args = parser.parse_args()

    cards = load_cards()

    # Collect deck paths
    paths = []
    if args.deck:
        paths.append(Path(args.deck))
    if args.real or args.all or (not args.w6 and not args.base5w1p and not args.sim and not args.deck):
        paths.extend(real_deck_paths())
    if args.w6 or args.all:
        paths.extend(sorted((ROOT / "simulation/decks/weapon6").glob("*.csv")))
    if args.base5w1p or args.all:
        paths.extend(sorted((ROOT / "simulation/decks/base5w1p").glob("*.csv")))
    if args.sim or args.all:
        paths.extend(sorted((ROOT / "simulation/decks/sim").glob("*.csv")))

    # Deduplicate paths
    unique_paths = []
    seen = set()
    for p in paths:
        if p.resolve() not in seen:
            seen.add(p.resolve())
            unique_paths.append(p)

    profiles = []
    for p in unique_paths:
        try:
            d = load_deck(p, cards)
            prof = analyze_deck_full(p.stem, d)
            profiles.append(prof)
            if not args.distance_curve:
                print_deck_summary(prof, args.markdown)
        except Exception as e:
            print(f"Error loading {p}: {e}", file=sys.stderr)

    if profiles:
        print_fleet_summary(profiles, args.markdown)


if __name__ == "__main__":
    main()
