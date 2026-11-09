#!/usr/bin/env python3
"""Unit tests for Zone Information and Tactical Combat Simulator."""

import math
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from simulation.simulate import Card, ZONES
from simulation.zone_information import (
    entropy,
    can_attack_at_distance,
    effective_attack_zones,
    MAX_ENTROPY,
)
from simulation.zone_tactics_sim import (
    TacticalFrame,
    can_reach_target,
    effective_melee_zones,
    score_hand_synergy,
    score_hand_heuristic,
)


class TestZoneInformation(unittest.TestCase):

    def test_entropy_bounds(self):
        # Uniform 3-way distribution has maximum entropy log2(3) ~ 1.58496 bits
        uniform = {"High": 10.0, "Mid": 10.0, "Low": 10.0}
        self.assertAlmostEqual(entropy(uniform), MAX_ENTROPY, places=4)

        # 100% concentrated on one zone has 0 entropy
        concentrated = {"High": 0.0, "Mid": 10.0, "Low": 0.0}
        self.assertAlmostEqual(entropy(concentrated), 0.0, places=4)

        # 50/50 on two zones has 1.0 bit
        two_zones = {"High": 5.0, "Mid": 5.0, "Low": 0.0}
        self.assertAlmostEqual(entropy(two_zones), 1.0, places=4)

    def test_melee_reach_at_distance(self):
        # Normal melee attack (move mod 0, base move 4 -> reach 4)
        c_normal = Card("test_m", "test_m", "test", 5, (1, 0, 0), frozenset(["High"]), movement=0)
        self.assertTrue(can_attack_at_distance(c_normal, dist=1, base_mv=4))
        self.assertTrue(can_attack_at_distance(c_normal, dist=5, base_mv=4))  # needed 4 <= 4
        self.assertFalse(can_attack_at_distance(c_normal, dist=6, base_mv=4))  # needed 5 > 4

        # Heavy melee attack (Chainsaw, move mod -4, base move 4 -> reach 0)
        c_heavy = Card("test_heavy", "test_heavy", "test", 3, (0, 4, 0), frozenset(["Mid"]), movement=-4)
        self.assertTrue(can_attack_at_distance(c_heavy, dist=1, base_mv=4))
        self.assertFalse(can_attack_at_distance(c_heavy, dist=2, base_mv=4))  # needed 1 > 0

        # Fast melee attack (Knife lunge, move mod +1, base move 4 -> reach 5)
        c_fast = Card("test_fast", "test_fast", "test", 7, (0, 1, 0), frozenset(["Mid"]), movement=1)
        self.assertTrue(can_attack_at_distance(c_fast, dist=6, base_mv=4))  # needed 5 <= 5
        self.assertFalse(can_attack_at_distance(c_fast, dist=7, base_mv=4))  # needed 6 > 5

    def test_ranged_reach_at_distance(self):
        # Ranged card (range 7, move mod -2, base move 4 -> reach 2, max range 7 -> total 9)
        c_ranged = Card(
            "test_r", "test_r", "test", 4, (0, 2, 0), frozenset(["Mid"]),
            ranged=True, movement=-2, ranges=(0, 7, 0)
        )
        self.assertTrue(can_attack_at_distance(c_ranged, dist=2, base_mv=4))
        self.assertTrue(can_attack_at_distance(c_ranged, dist=7, base_mv=4))
        self.assertTrue(can_attack_at_distance(c_ranged, dist=9, base_mv=4))
        self.assertFalse(can_attack_at_distance(c_ranged, dist=10, base_mv=4))

    def test_elevation_shift(self):
        # Cleave: 2 High, 2 Mid, 0 Low
        c_cleave = Card("cleave", "cleave", "test", 4, (2, 2, 0), frozenset(["High"]))

        # Attacker higher (+1): Low -> Mid, Mid -> High, High -> Whiff
        higher = effective_attack_zones(c_cleave, delta_elev=1)
        self.assertEqual(higher["High"], 2)  # Mid shifted to High
        self.assertEqual(higher["Mid"], 0)   # Low shifted to Mid (0)
        self.assertEqual(higher["Low"], 0)

        # Attacker lower (-1): High -> Mid, Mid -> Low, Low -> Whiff
        lower = effective_attack_zones(c_cleave, delta_elev=-1)
        self.assertEqual(lower["High"], 0)   # High shifted down to Mid!
        self.assertEqual(lower["Mid"], 2)    # High shifted to Mid
        self.assertEqual(lower["Low"], 2)    # Mid shifted to Low


class TestTacticalPolicies(unittest.TestCase):

    def test_heuristic_reach_awareness(self):
        # Frame A at pos 0, Frame B at pos 6 (distance 6)
        fa = TacticalFrame("fa", "A", None, base_movement=4, pos=0)
        fb = TacticalFrame("fb", "B", None, base_movement=4, pos=6)

        c_slow_melee = Card("slow", "slow", "test", 3, (0, 3, 0), frozenset(["Mid"]), movement=-2)
        c_ranged = Card("rifle", "rifle", "test", 4, (0, 2, 0), frozenset(["Mid"]), ranged=True, ranges=(0, 7, 0), movement=-1)

        # Heuristic scores reachable ranged attack much higher than unreachable slow melee
        score_melee = score_hand_heuristic([c_slow_melee], fa, fb)
        score_ranged = score_hand_heuristic([c_ranged], fa, fb)
        self.assertGreater(score_ranged, score_melee)

        # Synergy ignores distance, so it evaluates solely on raw stats (3 dmg > 2 dmg)
        syn_melee = score_hand_synergy([c_slow_melee], fa)
        syn_ranged = score_hand_synergy([c_ranged], fa)
        self.assertGreater(syn_melee, syn_ranged)

    def test_heuristic_elevation_awareness(self):
        # fa is at elevation 1 (higher than fb at 0)
        # fb's melee cannot hit High (shifts down to Mid/Low)
        fa = TacticalFrame("fa", "A", None, pos=1, elevation=1)
        fb = TacticalFrame("fb", "B", None, pos=2, elevation=0)

        # Block High vs Block Mid
        c_block_high = Card("bh", "bh", "test", 5, (0, 0, 0), frozenset(["High"]))
        c_block_mid = Card("bm", "bm", "test", 5, (0, 0, 0), frozenset(["Mid"]))

        score_h = score_hand_heuristic([c_block_high], fa, fb)
        score_m = score_hand_heuristic([c_block_mid], fa, fb)

        # Mid block is far more valuable because incoming melee threat on High is near 0
        self.assertGreater(score_m, score_h)

    def test_heuristic_stripped_zone_lethal(self):
        fa = TacticalFrame("fa", "A", None, pos=1)
        fb = TacticalFrame("fb", "B", None, pos=2)
        # fb's Mid zone is stripped (damage = 4)
        fb.damage["Mid"] = 4

        c_atk_mid = Card("atkm", "atkm", "test", 5, (0, 2, 0), frozenset())
        c_atk_low = Card("atkl", "atkl", "test", 5, (0, 0, 2), frozenset())

        score_mid = score_hand_heuristic([c_atk_mid], fa, fb)
        score_low = score_hand_heuristic([c_atk_low], fa, fb)

        # Attacking the stripped Mid zone should receive a massive finishing blow bonus
        self.assertGreater(score_mid, score_low + 10.0)

    def test_oracle_exact_cardpool_knowledge(self):
        fa = TacticalFrame("fa", "A", None, pos=1)
        fb = TacticalFrame("fb", "B", None, pos=2)

        # fb has a deck that blocks ONLY High (0 Mid, 0 Low)
        opp_deck = [
            Card("p1", "p1", "test", 5, (1, 0, 0), frozenset(["High"])),
            Card("p2", "p2", "test", 5, (1, 0, 0), frozenset(["High"])),
        ]
        fa.opp_profile = {"deck_cards": opp_deck, "block_freq": {"High": 1.0, "Mid": 0.0, "Low": 0.0}}

        c_atk_high = Card("ah", "ah", "test", 5, (2, 0, 0), frozenset())
        c_atk_low = Card("al", "al", "test", 5, (0, 0, 2), frozenset())

        # Oracle knows opponent blocks High 100% and Low 0%, so it heavily favors Low attack
        from simulation.zone_tactics_sim import score_hand_oracle
        s_high = score_hand_oracle([c_atk_high], fa, fb)
        s_low = score_hand_oracle([c_atk_low], fa, fb)
        self.assertGreater(s_low, s_high)


if __name__ == "__main__":
    unittest.main()
