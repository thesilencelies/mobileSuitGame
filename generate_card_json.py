#!/usr/bin/env python3
"""
generate_card_json.py

Writes one Tabletop Simulator card-metadata JSON file per card into json/.

The schema is fixed by TTS, so no fields can be added:

    {
      "<raw.githubusercontent URL of the card's AllCardImages PNG>": {
        "name":        "<Group> <Name>",
        "description": "<one clarifying line per keyword printed on the card>",
        "gm_notes":    "<every stat, then the card text, newline separated>",
        "tags":        {"1": "Card", "2": "Action", "3": "Weapon", "4": "Aegis"}
      }
    }

Sources, all read straight from the repo-root CSVs (PrintID == 0 rows skipped,
the same "not part of the game" convention the rest of the pipeline uses):

    Weapon/Basic/Booster/Pilot/Drone actions.csv -> Card, Action, <the card type>
    Frames.csv                                   -> Card, Frame

with the card's faction as the last tag on every card ("Factionless" for the
majority of cards, which have no Faction cell).

The image URL points at AllCardImages/, whose filenames `generate_card_images.py
--all` produces; this script reuses that module's `card_image_name()` so the two
cannot drift, and URL-encodes the result (many card images have spaces in them).

Card text is LaTeX. The keyword macros (`\\fulldazed` -> "Dazed (-2 card)") are
expanded from the *same* dictionaries `generateCards.py` builds card_macros.tex
from, so a keyword edited there flows through to the JSON. Only the longer
`description` sentences are held here, in GLOSSARY -- and a startup check fails
loudly if a keyword exists in generateCards.py without a GLOSSARY entry.

Usage:
    python generate_card_json.py
    python generate_card_json.py --output-dir json --quiet
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote

from generate_card_images import ACTION_CSVS, FRAMES_CSV, card_image_name, _print_id_nonzero

WORKSPACE = Path(__file__).parent
IMAGE_DIR = WORKSPACE / "AllCardImages"

GITHUB_RAW_BASE = (
    "https://raw.githubusercontent.com/thesilencelies/mobileSuitGame/"
    "refs/heads/master/AllCardImages/"
)

#: TTS object tags, in the order they are numbered: everything is a Card, then
#: Action or Frame, then the card's own type, then its faction.
#: source CSV -> the type tag its cards carry. The two Basic actions.csv groups
#: (the "Basic" moves and the faction "Frame" abilities) are both Basic actions.
CARD_TYPE_TAGS = {
    "Weapon actions.csv": "Weapon",
    "Basic actions.csv": "Basic",
    "Booster actions.csv": "Booster",
    "Pilot actions.csv": "Pilot",
    "Drone actions.csv": "Drone",
}

FACTIONLESS_TAG = "Factionless"

ZONES = (("High", "H"), ("Mid", "M"), ("Low", "L"))


def build_tags(*names: str) -> dict[str, str]:
    """TTS numbers its tags from "1"; a card is tagged broadest-first."""
    return {str(number): name for number, name in enumerate(names, start=1)}


# --------------------------------------------------------------------------
# generateCards.py's keyword tables
# --------------------------------------------------------------------------

def _import_generate_cards():
    """generateCards.py creates its build/ subfolders at import time, relative to
    the process's cwd, so import it with the repo root as the cwd rather than
    scattering empty build/ trees wherever this script was run from."""
    previous = os.getcwd()
    os.chdir(WORKSPACE)
    try:
        import generateCards
        return generateCards
    finally:
        os.chdir(previous)


_gc = _import_generate_cards()


def _cmd(keyword: str) -> str:
    """"Guard Break" -> "guardbreak", the macro name generateCards.py emits."""
    return keyword.lower().replace(" ", "")


def _build_macro_table() -> dict[str, Callable[[Optional[str]], str]]:
    """Macro name -> expansion, mirroring createMacros() in generateCards.py.

    Every keyword has a bare form (`\\dazed` -> "Dazed") and a `full` form
    (`\\fulldazed` -> "Dazed (-2 card)") that spells the effect out on the card;
    the JSON keeps whichever form the card itself printed.
    """
    table: dict[str, Callable[[Optional[str]], str]] = {}

    for ability, desc in _gc.ability_dict.items():
        table[_cmd(ability)] = lambda arg, a=ability: a
        table["full" + _cmd(ability)] = lambda arg, a=ability, d=desc: f"{a} ({d})"

    for ability, desc in _gc.numbered_ability_dict.items():
        # the description carries LaTeX's "#1" placeholder for the argument
        table[_cmd(ability)] = lambda arg, a=ability: f"{a} {arg}".strip()
        table["full" + _cmd(ability)] = (
            lambda arg, a=ability, d=desc: f"{a} {arg} ({d.replace('#1', arg or '')})".strip()
        )

    for status, (desc, _img) in _gc.status_dict.items():
        table[_cmd(status)] = lambda arg, s=status: s
        table["full" + _cmd(status)] = lambda arg, s=status, d=desc: f"{s} ({d})"

    for rule, desc in _gc.rules_dict.items():
        table[rule + "text"] = lambda arg, d=desc: f"({d})"

    for dtype in _gc.damage_type_dict:
        # icon macros: \cut / \smallcut are one hit, \smallcutx{2} is a run of N
        table[dtype] = lambda arg, t=dtype: t
        table["small" + dtype] = lambda arg, t=dtype: t
        table["small" + dtype + "x"] = lambda arg, t=dtype: f"{arg} {t}".strip()

    table["emph"] = lambda arg: arg or ""

    return table


MACROS = _build_macro_table()


# --------------------------------------------------------------------------
# Keyword glossary (the `description` field)
# --------------------------------------------------------------------------

#: keyword -> (substrings that indicate it in the *expanded* lowercased text,
#: the clarifying sentence). Wording follows the Keywords section of
#: rules/rules.tex; order here is only a tie-break, entries are emitted in the
#: order they appear on the card.
GLOSSARY: dict[str, tuple[tuple[str, ...], str]] = {
    "On Hit": (("on hit",),
               "On Hit: the text after the colon only applies if the attack hits "
               "(that is, is not blocked)"),
    "On Block": (("on block",),
                 "On Block: the text after the colon only applies if this card blocks an attack"),
    "Guard Break": (("guard break", "guardbreak"),
                    "Guard Break: this attack consumes one block per zone; zones that are not "
                    "blocked still deal damage"),
    "Committed": (("committed",),
                  "Committed: this attack is discarded after it resolves"),
    "Feint": (("feint",),
              "Feint: if this attack is not blocked it deals no damage (blocks are compulsory)"),
    "Close Quarters": (("close quarters", "closequarters"),
                       "Close Quarters: this attack cannot be blocked by attacks that have "
                       "already resolved"),
    "Knockback": (("knockback",),
                  "Knockback X: move the target frame X steps in any direction away from the "
                  "source (it cannot move up elevation)"),
    "Reload": (("reload",),
               "Reload: this card persists until this frame next resolves an attack from this "
               "weapon; that card deals no damage and triggers no abilities, then both are "
               "discarded"),
    "Flying": (("flying",),
               "Flying: this frame spends no movement to cross obstacles or change floor, and "
               "obstacles do not block line of sight to or from it"),
    "Shield": (("shield",),
               "Shield X: put X shield counters on the frame; a counter is removed instead of "
               "taking any damage, and one counter stops a whole attack however many zones it "
               "landed in"),
    "Deathstrike": (("deathstrike",),
                    "Deathstrike: when this frame would be destroyed it fights on until the end "
                    "of the next turn, and is only removed if it is still dead"),
    "Drone": (("drone", "summon"),
              "Drone: a token that takes this card's action every turn, using the movement and "
              "health printed beside it, until it is destroyed"),
    "Stunned": (("stunned",), "Stunned: while stunned a frame gets -2 init"),
    "Slowed": (("slowed",), "Slowed: while slowed a frame gets -2 mv"),
    "Dazed": (("dazed",), "Dazed: while dazed a frame gets -2 cards"),
    "Stimmed": (("stimmed",), "Stimmed: while stimmed a frame gets +2 init"),
    "Boosted": (("boosted",), "Boosted: while boosted a frame gets +2 mv"),
    "Lucid": (("lucid",), "Lucid: while lucid a frame gets +2 cards"),
    "Revealed": (("revealed",),
                 "Revealed: while revealed a frame's chosen actions are turned face up"),
}

#: not printed as a word, so it is detected from the stats instead. The
#: persistence mark is deliberately *not* described here -- it is a stat, and
#: gm_notes already carries it as a "persist:" line.
SUPER_BLOCK_NOTE = ("Super block: a block value above 1; a zone blocked by a super block is not "
                    "discarded, the card stays on the field revealed")


def _check_card_type_tags() -> None:
    """generate_card_images.py owns the list of action CSVs; fail loudly if one
    is added there without a type tag here rather than crashing mid-run."""
    missing = [name for name in ACTION_CSVS if name not in CARD_TYPE_TAGS]
    if missing:
        sys.exit("Error: no CARD_TYPE_TAGS entry for: " + ", ".join(missing))


def _check_glossary_covers_keywords() -> None:
    """Fail loudly if generateCards.py grew a keyword this script cannot explain,
    rather than silently writing a card with an unexplained keyword on it."""
    defined = {*_gc.ability_dict, *_gc.numbered_ability_dict, *_gc.status_dict}
    missing = sorted(k for k in defined if k not in GLOSSARY)
    if missing:
        sys.exit(
            "Error: these keywords are defined in generateCards.py but have no GLOSSARY entry "
            f"in {Path(__file__).name}:\n  " + "\n  ".join(missing) +
            "\nAdd a one-line clarification for each (see rules/rules.tex's Keywords section)."
        )


# --------------------------------------------------------------------------
# LaTeX card text -> plain text
# --------------------------------------------------------------------------

_ARG_MACRO_RE = re.compile(r"\\([a-zA-Z]+)\s*\{([^{}]*)\}")
_BARE_MACRO_RE = re.compile(r"\\([a-zA-Z]+)")

#: macro names met in the CSVs that MACROS could not expand, reported once at the end
_unknown_macros: set[str] = set()


def _expand(name: str, arg: Optional[str]) -> str:
    expand = MACROS.get(name)
    if expand is None:
        _unknown_macros.add(name)
        # keep the word rather than deleting it -- most macros *are* the keyword
        return name.capitalize() + (f" {arg}" if arg else "")
    return expand(arg)


def latex_to_text(raw: str) -> str:
    """The Text/Abilities columns are LaTeX. Expand the keyword macros to the
    words they print, `\\\\` to a line break, and drop the remaining markup."""
    text = (raw or "").replace("\\\\", "\n")
    text = _ARG_MACRO_RE.sub(lambda m: _expand(m.group(1), m.group(2)), text)
    text = _BARE_MACRO_RE.sub(lambda m: _expand(m.group(1), None), text)
    text = text.replace("{", "").replace("}", "").replace("~", " ")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


# --------------------------------------------------------------------------
# Cell parsing
# --------------------------------------------------------------------------

def _int(row: dict, column: str) -> int:
    """CSV cell -> int. Blank, missing (pilots have no attack columns) or
    non-numeric all mean 0."""
    raw = (row.get(column) or "").strip().replace("+", "")
    try:
        return int(raw)
    except ValueError:
        return 0


def _cell(row: dict, column: str) -> str:
    return (row.get(column) or "").strip()


def card_display_name(row: dict) -> str:
    """"Axe" + "Hook" -> "Axe Hook". A card already carrying its group's name
    ("Swarm"/"Swarm", "Missile Rack"/"Missile Rack 1") does not say it twice."""
    group, name = _cell(row, "Group"), _cell(row, "Name")
    if not group or name == group or name.startswith(group + " "):
        return name
    return f"{group} {name}"


def faction_tag(row: dict) -> str:
    """Most weapons, boosters, pilots and basics are shared kit with no Faction
    cell; they are tagged so they can still be filtered on in TTS."""
    return _cell(row, "Faction") or FACTIONLESS_TAG


def persistence_line(row: dict) -> Optional[str]:
    """`0`/blank -> no mark; `\\infty` -> permanent; else a turn count."""
    raw = _cell(row, "Persistence")
    if not raw or raw == "0":
        return None
    if "infty" in raw:
        return "persist:permanent"
    return f"persist:{raw} turn" + ("" if raw == "1" else "s")


# --------------------------------------------------------------------------
# gm_notes
# --------------------------------------------------------------------------

def attack_line(row: dict) -> Optional[str]:
    """"atk:2H@12,2M@12 projectile" -- value + zone, `@range` for ranged zones,
    and the damage type (once if the zones agree, else per zone)."""
    entries = []
    for zone, letter in ZONES:
        attack = _int(row, f"{zone}Attack")
        if attack <= 0:
            continue
        entry = f"{attack}{letter}"
        zone_range = _int(row, f"{zone}Range")
        if zone_range > 0:
            entry += f"@{zone_range}"
        entries.append((entry, _cell(row, f"{zone}DType")))
    if not entries:
        return None

    types = {dtype for _entry, dtype in entries if dtype}
    if len(types) == 1:
        return "atk:" + ",".join(entry for entry, _ in entries) + f" {types.pop()}"
    return "atk:" + ",".join(f"{entry} {dtype}".strip() for entry, dtype in entries)


def block_line(row: dict, is_pilot: bool) -> Optional[str]:
    """"Blk:1H" -- a value above 1 is a super block. Pilot cards print a High
    block that is not in the CSV (generateCards.py draws it unconditionally)."""
    entries = []
    for zone, letter in ZONES:
        block = 1 if (is_pilot and zone == "High") else _int(row, f"{zone}Block")
        if block > 0:
            entries.append(f"{block}{letter}")
    return "Blk:" + ",".join(entries) if entries else None


def action_gm_notes(row: dict, is_pilot: bool, is_drone: bool) -> str:
    lines = [f"{_cell(row, 'Initiative')}init", f"{_cell(row, 'Movement') or '0'}mv"]
    lines += [line for line in (attack_line(row), block_line(row, is_pilot)) if line]
    if is_drone:
        lines.append(f"drone:{_int(row, 'Drone_Health')}hp {_cell(row, 'Drone_MV')}mv")
    if _cell(row, "Faction"):
        lines.append(f"faction:{_cell(row, 'Faction')}")
    persistence = persistence_line(row)
    if persistence:
        lines.append(persistence)
    text = latex_to_text(_cell(row, "Text"))
    if text:
        lines.append(text)
    return "\n".join(lines)


def frame_gm_notes(row: dict) -> str:
    lines = [
        f"{_cell(row, 'Movement')}mv",
        f"armour:{_int(row, 'Top armour')}H/{_int(row, 'Side armour')}M/{_int(row, 'Low armour')}L",
        f"weapons:{_cell(row, 'Weapon Slots')}",
        f"boosters:{_cell(row, 'Boosters')}",
        f"deck:{_cell(row, 'Deck size')}",
    ]
    if _cell(row, "Faction"):
        lines.append(f"faction:{_cell(row, 'Faction')}")
    abilities = latex_to_text(_cell(row, "Abilities"))
    if abilities:
        lines.append(abilities)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# description
# --------------------------------------------------------------------------

def description_for(text: str, row: dict, is_pilot: bool) -> str:
    """One clarifying line per keyword the card prints, in the order they appear
    on it, followed by the super block, which is drawn rather than written."""
    lowered = text.lower()
    found = []
    for _keyword, (markers, sentence) in GLOSSARY.items():
        positions = [lowered.find(marker) for marker in markers]
        positions = [p for p in positions if p >= 0]
        if positions:
            found.append((min(positions), sentence))
    notes = [sentence for _pos, sentence in sorted(found, key=lambda pair: pair[0])]

    has_super_block = any(
        (0 if (is_pilot and zone == "High") else _int(row, f"{zone}Block")) > 1
        for zone, _letter in ZONES
    )
    if has_super_block:
        notes.append(SUPER_BLOCK_NOTE)

    return "\n".join(notes)


# --------------------------------------------------------------------------
# Card enumeration
# --------------------------------------------------------------------------

class CardEntry:
    """One card's finished JSON payload plus the filenames it needs."""

    def __init__(self, image_name: str, name: str, description: str, gm_notes: str, tags: dict):
        self.image_name = image_name
        self.name = name
        self.description = description
        self.gm_notes = gm_notes
        self.tags = tags

    @property
    def json_name(self) -> str:
        """Sanitised like the PNG so json/<x>.json sits beside AllCardImages/<x>.png."""
        return card_image_name(self.image_name).replace(".png", ".json")

    @property
    def url(self) -> str:
        # AllCardImages filenames keep the spaces and apostrophes of the card
        # names ("Sniper Rifle_Patience's Reward.png"), so the URL must escape them
        return GITHUB_RAW_BASE + quote(self.image_name)

    def payload(self) -> dict:
        return {self.url: {"name": self.name, "description": self.description,
                           "gm_notes": self.gm_notes, "tags": self.tags}}


def read_rows(csv_name: str) -> list[dict]:
    path = WORKSPACE / csv_name
    if not path.is_file():
        sys.exit(f"Error: expected card CSV not found: {path}")
    with open(path, newline="", encoding="utf-8") as fh:
        return [row for row in csv.DictReader(fh) if _print_id_nonzero(row)]


def enumerate_cards() -> list[CardEntry]:
    entries: list[CardEntry] = []

    for csv_name in ACTION_CSVS:
        type_tag = CARD_TYPE_TAGS[csv_name]
        is_pilot = csv_name == "Pilot actions.csv"
        is_drone = csv_name == "Drone actions.csv"
        for row in read_rows(csv_name):
            gm_notes = action_gm_notes(row, is_pilot, is_drone)
            entries.append(CardEntry(
                # build/card/*.tex and AllCardImages/*.png are both the literal
                # "{Group}_{Name}" engine key
                image_name=f"{_cell(row, 'Group')}_{_cell(row, 'Name')}.png",
                name=card_display_name(row),
                description=description_for(gm_notes, row, is_pilot),
                gm_notes=gm_notes,
                tags=build_tags("Card", "Action", type_tag, faction_tag(row)),
            ))

    for row in read_rows(FRAMES_CSV):
        gm_notes = frame_gm_notes(row)
        entries.append(CardEntry(
            image_name=card_image_name(f"frame/{_cell(row, 'Name')}.tex"),
            name=_cell(row, "Name"),
            description=description_for(gm_notes, row, is_pilot=False),
            gm_notes=gm_notes,
            tags=build_tags("Card", "Frame", faction_tag(row)),
        ))

    if not entries:
        sys.exit("Error: no printable rows found across the action and frame CSVs.")
    return entries


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def json_text(entry: CardEntry) -> str:
    """Written by hand rather than json.dump so the file keeps the layout of the
    TTS exports it has to sit alongside (the URL key on its own line)."""
    def esc(value: str) -> str:
        return (value.replace("\\", "\\\\").replace('"', '\\"')
                .replace("\n", "\\n").replace("\t", "\\t"))

    tags = ",\n".join(f'      "{key}": "{esc(value)}"' for key, value in entry.tags.items())
    return (
        "{\n"
        f'  "{esc(entry.url)}":\n'
        "  {\n"
        f'    "name": "{esc(entry.name)}",\n'
        f'    "description": "{esc(entry.description)}",\n'
        f'    "gm_notes": "{esc(entry.gm_notes)}",\n'
        '    "tags":\n'
        "    {\n"
        f"{tags}\n"
        "    }\n"
        "  }\n"
        "}\n"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Write one Tabletop Simulator metadata JSON per card into json/."
    )
    parser.add_argument("--output-dir", default="json",
                        help="Directory to write the JSON files to (default: json).")
    parser.add_argument("--quiet", action="store_true",
                        help="Only print the summary and any warnings.")
    args = parser.parse_args()

    _check_card_type_tags()
    _check_glossary_covers_keywords()

    output_dir = WORKSPACE / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    entries = enumerate_cards()

    seen: dict[str, str] = {}
    for entry in entries:
        clash = seen.get(entry.json_name)
        if clash:
            sys.exit(f"Error: '{entry.name}' and '{clash}' both write {entry.json_name}")
        seen[entry.json_name] = entry.name

    for entry in entries:
        (output_dir / entry.json_name).write_text(json_text(entry), encoding="utf-8")
        if not args.quiet:
            print(f"  {entry.json_name}  <- {entry.name}")

    print(f"\nWrote {len(entries)} card JSON file(s) to {output_dir}/")

    missing = [entry.image_name for entry in entries
               if not (IMAGE_DIR / entry.image_name).is_file()]
    if missing:
        print(f"\nWarning: {len(missing)} card image(s) are not in {IMAGE_DIR.name}/, so their "
              "URLs will 404 until\n`python generate_card_images.py --all` is re-run:")
        for name in missing:
            print(f"  {name}")

    orphans = sorted(p.name for p in output_dir.glob("*.json") if p.name not in seen)
    if orphans:
        print(f"\nWarning: {len(orphans)} JSON file(s) in {output_dir.name}/ match no current "
              "card (renamed or removed?):")
        for name in orphans:
            print(f"  {name}")

    if _unknown_macros:
        print("\nWarning: these LaTeX macros are used in the CSVs but are not defined in "
              "generateCards.py, so they were expanded to their bare name:")
        for name in sorted(_unknown_macros):
            print(f"  \\{name}")


if __name__ == "__main__":
    main()
