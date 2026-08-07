#!/usr/bin/env python3
"""Generate seeded opening-hand CSVs (7 cards each) for every faction deck.

Writes decks/hands/<deckname>_hand.csv, one bare `Group_Name` per row, matching
the naming culture of the parent decks/ folder (parent deck name + `_hand`).

The hand is a *baseline* the designer edits by hand afterwards. Seeding rules:
  - 2 booster cards (fall back to Basic_Sprint if the deck lacks 2 boosters)
  - 2 ranged attack cards (prefer longest range; if the deck has no ranged
    attacks, use the highest-movement attack cards instead)
  - 2 setup cards (pilot self/ally buffs, shield creators, drone/token creators)
  - 1 decent blocker (highest block; fall back to Basic_Dodge)

Cards are only drawn from the parent deck, except the universal Basic fallbacks
(Sprint, Dodge) named by the rules above.  Run: python generate_opening_hands.py
"""
import csv
import os
import re
import glob

REPO = os.path.dirname(os.path.abspath(__file__))
DECKS_DIR = os.path.join(REPO, "decks")
HANDS_DIR = os.path.join(DECKS_DIR, "hands")

CARD_CSVS = [
    "Weapon actions.csv",
    "Booster actions.csv",
    "Pilot actions.csv",
    "Basic actions.csv",
    "Drone actions.csv",
]

PILOT_GROUPS = {"Bruiser", "Mystic", "Tactician", "Wunderkid", "Engineer", "Specialist"}
DRONE_GROUPS = {"Swarm"}

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
                    "is_pilot": group in PILOT_GROUPS,
                    "is_drone": group in DRONE_GROUPS,
                }
                db[card["key"]] = card
    return db


def is_shield_setup(c):
    # A card only creates a shield if its effect *text* says so (e.g. "Add a
    # shield"). The Shield weapon group blocks but creates no shield.
    return "shield" in c["text"].lower()


def is_drone_setup(c):
    t = c["text"].lower()
    return c["is_drone"] or "summon" in t or "drone" in t or "token" in t


def is_pilot_buff(c):
    if not c["is_pilot"]:
        return False
    t = c["text"].lower()
    if "repair" in t:  # healing does nothing on turn 1
        return False
    return any(k in t for k in BUFF_KEYWORDS)


def pick_hand(deck_keys, db):
    """Return an ordered list of 7 card keys for the opening hand."""
    cards = [db[k] for k in deck_keys if k in db]
    chosen = []
    seen = set()

    def take(card):
        if card and card["key"] not in seen:
            chosen.append(card["key"])
            seen.add(card["key"])
            return True
        return False

    avail = lambda pool: [c for c in pool if c["key"] not in seen]

    # 1) Two boosters, filling short slots with Sprint. Only duplicate Sprint if
    # the deck itself lists two Sprints (it normally lists none -> cap of 1);
    # any still-empty booster slot is filled later by the top-up-to-7 pass.
    boosters = sorted((c for c in cards if c["group"] == "Booster"),
                      key=lambda c: -c["movement"])
    for c in boosters[:2]:
        take(c)
    sprint_cap = max(1, sum(1 for k in deck_keys if k == "Basic_Sprint"))
    sprint_used = 0
    while (sprint_used < sprint_cap
           and sum(1 for k in chosen if db.get(k, {}).get("group") == "Booster"
                   or k == "Basic_Sprint") < 2):
        chosen.append("Basic_Sprint")
        sprint_used += 1

    # 2) Two ranged attacks (prefer longest range), else highest-movement attacks.
    ranged = sorted((c for c in avail(cards) if c["max_range"] > 0),
                    key=lambda c: (-c["max_range"], -c["total_atk"]))
    ranged_taken = 0
    for c in ranged:
        if ranged_taken >= 2:
            break
        if take(c):
            ranged_taken += 1
    if ranged_taken < 2:
        mv_attacks = sorted((c for c in avail(cards) if c["is_attack"]),
                            key=lambda c: (-c["movement"], -c["total_atk"]))
        for c in mv_attacks:
            if ranged_taken >= 2:
                break
            if take(c):
                ranged_taken += 1

    # 3) Two setup cards: drone creators, then shield creators, then pilot buffs.
    setup_pool = []
    for pred in (is_drone_setup, is_shield_setup, is_pilot_buff):
        setup_pool += [c for c in avail(cards) if pred(c) and c not in setup_pool]
    setup_taken = 0
    for c in setup_pool:
        if setup_taken >= 2:
            break
        if take(c):
            setup_taken += 1
    # top up setup from any remaining pilot if short
    if setup_taken < 2:
        for c in sorted(avail(cards), key=lambda c: (not c["is_pilot"],)):
            if setup_taken >= 2:
                break
            if take(c):
                setup_taken += 1

    # 4) One decent blocker (max block >= 2 preferred), else Dodge.
    blockers = sorted(avail(cards), key=lambda c: (-c["max_block"], -c["total_block"]))
    if blockers and blockers[0]["max_block"] >= 2:
        take(blockers[0])
    else:
        chosen.append("Basic_Dodge")
        seen.add("Basic_Dodge")

    # Top up to 7 from the best remaining deck cards if any category underfilled.
    if len(chosen) < 7:
        rest = sorted(avail(cards),
                      key=lambda c: -(c["total_atk"] + c["total_block"] + c["max_range"]))
        for c in rest:
            if len(chosen) >= 7:
                break
            take(c)

    return chosen[:7]


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
        if "terrain" in base:
            continue
        deck_keys = load_deck(path)
        hand = pick_hand(deck_keys, db)
        out = os.path.join(HANDS_DIR, base.replace(".csv", "_hand.csv"))
        with open(out, "w", newline="") as f:
            for k in hand:
                f.write(k + "\n")
        print(f"{base:32s} -> {len(hand)} cards")
        for k in hand:
            print(f"    {k}")


if __name__ == "__main__":
    main()
