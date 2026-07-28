#!/usr/bin/env python3
"""Behavioural tests for the combat model.

These use synthetic cards so the initiative / zone interactions can be set up
exactly. Decks are lists of identical cards, so each game is deterministic and
the outcome is 100% one way -- we assert that outcome.

Run:  python simulation/test_simulate.py
"""

import random

from simulate import Card, Frame, Pile, ZONES, play_game

GAMES = 200
HEALTH = 4
HAND = 2
MAX_ROUNDS = 100


def card(name, init, atk=(0, 0, 0), blk=()):
    return Card(name, name, "test", init, tuple(atk), frozenset(blk))


def frame(name, team, a_card, *, target):
    rng = random.Random(0)
    return Frame(name=name, team=team, pile=Pile([a_card] * 6, rng),
                 is_target=target)


def outcome(a_card, b_card, *, seed=0):
    """Play GAMES games of one A-card deck vs one B-card deck; return win tallies."""
    rng = random.Random(seed)
    wins = {"A": 0, "B": 0, "draw": 0}
    for _ in range(GAMES):
        ta = frame("A", "A", a_card, target=True)
        tb = frame("B", "B", b_card, target=True)
        w, _ = play_game([ta], [tb], ta, tb, HAND, HEALTH, MAX_ROUNDS, rng)
        wins[w] += 1
    return wins


def expect(label, wins, key):
    total = sum(wins.values())
    ok = wins[key] == total
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {dict(wins)} (expect all {key})")
    assert ok, f"{label}: expected all {key}, got {wins}"


def main():
    print("mobileSuitGame combat-model tests")
    print("-" * 60)

    # 1. All mid attacks vs all (pure) mid blocks at LOWER initiative -> draw.
    #    The block cancels the attack even though it resolves later and has no
    #    attack of its own to lose, so nobody ever takes damage.
    mid_atk = card("mid_atk", init=7, atk=(0, 2, 0))
    mid_block = card("mid_block", init=3, blk=("Mid",))
    expect("mid attacks vs slower pure mid blocks",
           outcome(mid_atk, mid_block), "draw")

    # 2. Mid-attack+mid-block  vs  high-attack+mid-block.
    #    Whoever has the LOWER initiative must spend cards (losing their attack)
    #    to block the enemy's mid attacks; the higher-initiative side attacks
    #    first, then its already-resolved cards block for free.
    m_card = card("mid", init=7, atk=(0, 2, 0), blk=("Mid",))   # mid attacks faster
    h_card = card("high", init=5, atk=(2, 0, 0), blk=("Mid",))
    # High can never block the mid attacks (it only blocks mid) but its OWN high
    # attacks are unblockable by a mid block... yet mid resolves first here, so
    # high must sacrifice its attacks to survive -> draw.
    expect("mid(fast)+midblock vs high(slow)+midblock",
           outcome(m_card, h_card), "draw")

    m_slow = card("mid", init=5, atk=(0, 2, 0), blk=("Mid",))   # high attacks faster
    h_fast = card("high", init=7, atk=(2, 0, 0), blk=("Mid",))
    # High resolves first (unblockable, since mid can't block high), then its
    # resolved cards block the mid attacks for free -> High wins.
    expect("high(fast)+midblock vs mid(slow)+midblock",
           outcome(h_fast, m_slow), "A")  # team A holds the high deck here

    # 3a. Multi-zone attack, one zone lines up -> whole attack blocked.
    hi_mid_atk = card("hi_mid", init=7, atk=(2, 2, 0))          # attacks High+Mid
    expect("High+Mid attack vs pure Mid block (overlap -> blocked)",
           outcome(hi_mid_atk, mid_block), "draw")

    # 3b. Multi-zone attack, no zone lines up -> gets through.
    hi_low_atk = card("hi_low", init=7, atk=(2, 0, 2))          # attacks High+Low
    expect("High+Low attack vs pure Mid block (no overlap -> lands)",
           outcome(hi_low_atk, mid_block), "A")

    # 3c. A single-zone block cancels the entire multi-zone attack it overlaps,
    #     and is consumed doing so (one block, one attack).
    low_block = card("low_block", init=3, blk=("Low",))
    expect("High+Low attack vs pure Low block (overlap -> blocked)",
           outcome(hi_low_atk, low_block), "draw")

    print("-" * 60)
    print("all tests passed")


if __name__ == "__main__":
    main()
