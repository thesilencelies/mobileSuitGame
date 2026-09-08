#!/usr/bin/env python3
"""Generate seeded opening-hand CSVs (7 cards each) for every faction deck.

Writes decks/hands/<deckname>_hand.csv, one bare `Group_Name` per row, matching
the naming culture of the parent decks/ folder (parent deck name + `_hand`).

The hand is a *baseline* the designer edits by hand afterwards. Seeding rules:
  - 2 booster cards
  - 2 ranged attack cards (prefer longest range; if the deck has no ranged
    attacks, use the highest-movement attack cards instead)
  - 3 setup cards, preferring drone creators, then pilot self/ally buffs, then
    other token (and shield) creators — taken round-robin so the three slots
    mix rather than being three of a kind
  - no blocker: defensive cards are the *filler* now, not a seeded slot

Every card in the hand comes out of the parent deck — there are no universal
fallbacks. If the deck cannot fill a category (no boosters, say), the empty
slots go to the deck's best blocker, then to the next-best card from the
*other* categories, and failing that to a random card the deck still has left,
so the hand is always 7 cards (or the whole deck, if it is smaller). A deck
listing a card twice may draw it twice.

Run: python generate_opening_hands.py
"""
import csv
import os
import re
import glob
import random

REPO = os.path.dirname(os.path.abspath(__file__))
DECKS_DIR = os.path.join(REPO, "decks")
HANDS_DIR = os.path.join(DECKS_DIR, "hands")

HAND_SIZE = 7

# The card CSVs are the single source of truth for what counts as a pilot or a
# drone card: whichever file a row came out of decides, so adding a new drone
# group to Drone actions.csv needs no change here.
PILOT_CSV = "Pilot actions.csv"
DRONE_CSV = "Drone actions.csv"
CARD_CSVS = [
    "Weapon actions.csv",
    "Booster actions.csv",
    PILOT_CSV,
    "Basic actions.csv",
    DRONE_CSV,
]

# decks/ also holds the terrain and objective decks, which list tiles rather
# than action cards and so get no opening hand.
SKIP_DECK_PREFIXES = ("deck_terrain_", "deck_objective_")

# Keywords marking a pilot/card as a self/ally buff (setup), case-insensitive.
# (No "repair" — healing is useless on turn 1, so repair cards are not setup.)
BUFF_KEYWORDS = [
    "boosted", "stimmed", "lucid", "extra action", "extra card",
    "allied", "ally", "allies", "this frame gets", "resolve twice",
    "guard break", "gain ", "chooses cards", "reflex",
]


def _int(val):
    """Parse a movement/stat cell that may be '+5', '-2', '8,3', '' -> int."""
    if val is None:
        return 0
    tok = val.split(",")[0].strip().replace("+", "")
    m = re.match(r"-?\d+", tok)
    return int(m.group()) if m else 0


def load_cards():
    db = {}
    for fn in CARD_CSVS:
        path = os.path.join(REPO, fn)
        if not os.path.exists(path):
            continue
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                name = (row.get("Name") or "").strip()
                group = (row.get("Group") or "").strip()
                if not name or not group:
                    continue
                atk = [_int(row.get(z + "Attack")) for z in ("High", "Mid", "Low")]
                rng = [_int(row.get(z + "Range")) for z in ("High", "Mid", "Low")]
                blk = [_int(row.get(z + "Block")) for z in ("High", "Mid", "Low")]
                card = {
                    "key": f"{group}_{name}",
                    "name": name,
                    "group": group,
                    "movement": _int(row.get("Movement")),
                    "text": (row.get("Text") or ""),
                    "atk": atk,
                    "rng": rng,
                    "blk": blk,
                    "max_range": max(rng),
                    "total_atk": sum(atk),
                    "max_block": max(blk),
                    "total_block": sum(blk),
                    "is_attack": sum(atk) > 0,
                    "is_pilot": fn == PILOT_CSV,
                    "is_drone": fn == DRONE_CSV,
                }
                db[card["key"]] = card
    return db


def is_shield_setup(c):
    # A card only creates a shield if its effect *text* says so (e.g. "Add a
    # shield"). The Shield weapon group blocks but creates no shield.
    return "shield" in c["text"].lower()


def is_drone_setup(c):
    t = c["text"].lower()
    return c["is_drone"] or "summon" in t or "drone" in t


def is_token_setup(c):
    """Anything else a card puts on the board: tokens, and shields."""
    return "token" in c["text"].lower() or is_shield_setup(c)


def is_pilot_buff(c):
    if not c["is_pilot"]:
        return False
    t = c["text"].lower()
    if "repair" in t:  # healing does nothing on turn 1
        return False
    return any(k in t for k in BUFF_KEYWORDS)


# Each category ranks the cards the deck still holds, best first. Every list is
# sorted with the deck position as the final tiebreak so runs are reproducible.
def booster_candidates(avail):
    """Boosters, most movement first."""
    return sorted((e for e in avail if e[1]["group"] == "Booster"),
                  key=lambda e: (-e[1]["movement"], e[0]))


def ranged_candidates(avail):
    """Ranged attacks by reach, then melee attacks by how far they close."""
    ranged = sorted((e for e in avail if e[1]["max_range"] > 0),
                    key=lambda e: (-e[1]["max_range"], -e[1]["total_atk"], e[0]))
    melee = sorted((e for e in avail
                    if e[1]["max_range"] == 0 and e[1]["is_attack"]),
                   key=lambda e: (-e[1]["movement"], -e[1]["total_atk"], e[0]))
    return ranged + melee


def _round_robin(tiers):
    """Flatten ranked tiers one entry at a time, so the result mixes tiers.

    Tier order is still the preference — the best drone comes before the best
    buff — but a deck deep in one tier no longer spends every slot on it.
    """
    out = []
    for i in range(max((len(t) for t in tiers), default=0)):
        for tier in tiers:
            if i < len(tier):
                out.append(tier[i])
    return out


def setup_candidates(avail):
    """Drone creators, then pilot buffs, then other token creators, mixed.

    Each card lands in the first tier it matches, the three tiers are taken
    round-robin (so three setup slots prefer one of each over three drones),
    and any pilot card the tiers missed backs the whole list up.
    """
    tiers = []
    seen = set()
    for pred in (is_drone_setup, is_pilot_buff, is_token_setup,
                 lambda c: c["is_pilot"]):
        tier = [e for e in avail if e[0] not in seen and pred(e[1])]
        seen.update(e[0] for e in tier)
        tiers.append(tier)
    # The trailing "any pilot" tier is a last resort, not part of the mix.
    return _round_robin(tiers[:-1]) + tiers[-1]


def blocker_candidates(avail):
    """Anything that blocks, biggest block first (2+ is the one we want)."""
    return sorted((e for e in avail if e[1]["max_block"] > 0),
                  key=lambda e: (-e[1]["max_block"], -e[1]["total_block"], e[0]))


CATEGORIES = [
    ("booster", 2, booster_candidates),
    ("ranged", 2, ranged_candidates),
    ("setup", 3, setup_candidates),
    # Blockers are no longer seeded outright (quota 0): they are what a slot
    # falls back to when one of the categories above runs dry.
    ("block", 0, blocker_candidates),
]

# Order the leftover slots are filled in — a defensive card first, then the
# next-best card from any other category.
FILL_ORDER = ["block", "booster", "ranged", "setup"]


def pick_hand(deck_keys, db, rng):
    """Return an ordered list of up to 7 card keys drawn from this deck only.

    Cards are held as (deck position, card) pairs so a deck that lists a card
    twice can play it twice, while one listing stays one copy in the hand.
    """
    entries = [(i, db[k]) for i, k in enumerate(deck_keys) if k in db]
    taken = set()
    chosen = []

    def avail():
        return [e for e in entries if e[0] not in taken]

    def take(entry):
        if entry is None or entry[0] in taken:
            return False
        taken.add(entry[0])
        chosen.append(entry[1]["key"])
        return True

    hand_size = min(HAND_SIZE, len(entries))

    for _name, quota, candidates in CATEGORIES:
        filled = 0
        for entry in candidates(avail()):
            if filled >= quota or len(chosen) >= hand_size:
                break
            if take(entry):
                filled += 1

    # Slots the deck could not fill in their own category go to a blocker, then
    # to the next-best card from another category, and to a random leftover if
    # none of them match either.
    ranker = {name: candidates for name, _quota, candidates in CATEGORIES}
    while len(chosen) < hand_size:
        rest = avail()
        for name in FILL_ORDER:
            pool = ranker[name](rest)
            if pool and take(pool[0]):
                break
        else:
            take(rng.choice(rest))

    return chosen


def load_deck(path):
    keys = []
    with open(path) as f:
        for line in f:
            k = line.strip()
            if k:
                keys.append(k)
    return keys


def main():
    os.makedirs(HANDS_DIR, exist_ok=True)
    db = load_cards()
    deck_paths = sorted(glob.glob(os.path.join(DECKS_DIR, "deck_*.csv")))
    for path in deck_paths:
        base = os.path.basename(path)
        if base.startswith(SKIP_DECK_PREFIXES):
            continue
        deck_keys = load_deck(path)
        unknown = sorted({k for k in deck_keys if k not in db})
        if unknown:
            print(f"{base:32s} !! not defined in any card CSV: {', '.join(unknown)}")
        # Seed per deck so re-running leaves the other hands byte-identical.
        hand = pick_hand(deck_keys, db, random.Random(base))
        out = os.path.join(HANDS_DIR, base.replace(".csv", "_hand.csv"))
        with open(out, "w", newline="") as f:
            for k in hand:
                f.write(k + "\n")
        print(f"{base:32s} -> {len(hand)} cards")
        for k in hand:
            print(f"    {k}")


if __name__ == "__main__":
    main()
