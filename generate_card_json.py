#!/usr/bin/env python3
"""
generate_card_json.py

Writes json/cards.json: Tabletop Simulator metadata for all cards, tiles (tokens),
and figurines in one file.

The schema matches the TTS custom-object import format:

    {
      "Card": {
        "1": {
          "name":        "<Name>",
          "description": "<one clarifying line per keyword printed on the card>",
          "gm_notes":    "<every stat, then the card text, newline separated>",
          "tags":        {"1": "Card", "2": "Action", "3": "Weapon", "4": "Factionless"},
          "lua_script":  "",
          "face":        "<raw.githubusercontent URL of front image in AllCardImages>",
          "back":        "<raw.githubusercontent URL of card back in tts_assets>",
          "type":        "0",
          "sideways":    "false"
        }, ...
      },
      "Tile": {
        "1": {
          "name":        "<Name>",
          "description": "<description of token>",
          "gm_notes":    "<rules / stats>",
          "tags":        {"1": "Tile", "2": "Token", "3": "..."},
          "lua_script":  "",
          "face":        "<raw.githubusercontent URL of token image>",
          "back":        "<raw.githubusercontent URL of token image>",
          "type":        "2",
          "thickness":   "0.5",
          "stackable":   "true"
        }, ...
      },
      "Token": {},
      "Figurine": {
        "1": {
          "name":        "<Frame Name>",
          "description": "<description / keywords>",
          "gm_notes":    "<frame stats and abilities>",
          "tags":        {"1": "Figurine", "2": "Frame", "3": "<Faction>"},
          "lua_script":  "",
          "face":        "<raw.githubusercontent URL of transparent mech art in pictures/foreground/>",
          "back":        "<raw.githubusercontent URL of transparent mech art in pictures/foreground/>"
        }, ...
      }
    }

Sources:
    Weapon/Basic/Booster/Pilot/Drone actions.csv -> Card, Action, <the card type>
    Frames.csv                                   -> Card, Frame (and Figurine)
    Terrain_square.csv                           -> Card, Terrain / Objective
    tts_assets/*.png                             -> Tile (tokens)
    Drone actions.csv (art in pictures/)         -> Tile (drone tokens)

Usage:
    python generate_card_json.py
    python generate_card_json.py --output json/cards.json --quiet
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote

from generate_card_images import (
    ACTION_CSVS,
    FRAMES_CSV,
    TERRAIN_CSV,
    card_image_name,
    _print_id_nonzero,
)

WORKSPACE = Path(__file__).parent
IMAGE_DIR = WORKSPACE / "AllCardImages"
TTS_ASSETS_DIR = WORKSPACE / "tts_assets"
PICTURES_DIR = WORKSPACE / "pictures"

GITHUB_REPO_RAW = (
    "https://raw.githubusercontent.com/thesilencelies/mobileSuitGame/"
    "refs/heads/master/"
)
ALL_CARD_IMAGES_URL = GITHUB_REPO_RAW + "AllCardImages/"
TTS_ASSETS_URL = GITHUB_REPO_RAW + "tts_assets/"
PICTURES_URL = GITHUB_REPO_RAW + "pictures/"

NORMAL_CARD_BACK_URL = TTS_ASSETS_URL + "normal_back.png"
FRAMES_CARD_BACK_URL = TTS_ASSETS_URL + "frames_back.png"
TERRAIN_CARD_BACK_URL = TTS_ASSETS_URL + "terrain_back.png"

#: TTS object tags, in the order they are numbered: everything is a Card, then
#: Action or Frame or Terrain/Objective, then the card's own type, then its faction.
CARD_TYPE_TAGS = {
    "Weapon actions.csv": "Weapon",
    "Basic actions.csv": "Basic",
    "Booster actions.csv": "Booster",
    "Pilot actions.csv": "Pilot",
    "Drone actions.csv": "Drone",
}

FACTIONLESS_TAG = "Factionless"

ZONES = (("High", "H"), ("Mid", "M"), ("Low", "L"))

#: Known token metadata for token images in tts_assets/
TTS_ASSET_TOKENS: dict[str, dict] = {
    "Barricade.png": {
        "name": "Barricade",
        "description": "Impassable terrain marker placed by Engineer",
        "gm_notes": "impassable",
        "tags": ("Tile", "Token", "Engineer"),
    },
    "Cage.png": {
        "name": "Cage",
        "description": "Impassable cage marker placed by Bruiser Lockdown",
        "gm_notes": "impassable",
        "tags": ("Tile", "Token", "Bruiser"),
    },
    "Fugitive.png": {
        "name": "Fugitive",
        "description": "Carriable objective token from Extraction",
        "gm_notes": "carriable objective",
        "tags": ("Tile", "Token", "Objective"),
    },
    "Gangs.png": {
        "name": "Gangs",
        "description": "Mobile objective unit from Riverside",
        "gm_notes": "1hp 1mv 1init",
        "tags": ("Tile", "Token", "Objective"),
    },
    "GravityWell.png": {
        "name": "Gravity Well",
        "description": "Hazard marker placed by Engineer Gravity Well",
        "gm_notes": "hazard",
        "tags": ("Tile", "Token", "Engineer"),
    },
    "Illusion.png": {
        "name": "Illusion",
        "description": "Decoy marker for Ephemeral Images (removed when attacked or dealing damage)",
        "gm_notes": "decoy image",
        "tags": ("Tile", "Token", "Mystic"),
    },
    "Image.png": {
        "name": "Image",
        "description": "Decoy marker for Ephemeral Images",
        "gm_notes": "ephemeral image",
        "tags": ("Tile", "Token", "Mystic"),
    },
    "Portal.png": {
        "name": "Portal",
        "description": "Teleport portal placed by Wunderkid Portal",
        "gm_notes": "portal",
        "tags": ("Tile", "Token", "Wunderkid"),
    },
    "PowerPlant1.png": {
        "name": "Power Plant (1 HP)",
        "description": "Objective structure with 1 HP remaining",
        "gm_notes": "1hp",
        "tags": ("Tile", "Token", "Objective"),
    },
    "PowerPlant2.png": {
        "name": "Power Plant (2 HP)",
        "description": "Objective structure with 2 HP remaining",
        "gm_notes": "2hp",
        "tags": ("Tile", "Token", "Objective"),
    },
    "Real.png": {
        "name": "Real Frame",
        "description": "Real frame marker for Ephemeral Images",
        "gm_notes": "real frame",
        "tags": ("Tile", "Token", "Mystic"),
    },
    "Rebound.png": {
        "name": "Rebound",
        "description": "Rebound marker for line of sight and targeting",
        "gm_notes": "rebound",
        "tags": ("Tile", "Token", "Specialist"),
    },
    "Refugees.png": {
        "name": "Refugees",
        "description": "Mobile objective unit from Car Park",
        "gm_notes": "1hp 1mv 1init",
        "tags": ("Tile", "Token", "Objective"),
    },
    "Relic.png": {
        "name": "Relic",
        "description": "Carriable objective token from Lake Crosses",
        "gm_notes": "carriable objective",
        "tags": ("Tile", "Token", "Objective"),
    },
    "Shiny.png": {
        "name": "Shiny Thing",
        "description": "Carriable objective token from Shiny Thing",
        "gm_notes": "carriable objective",
        "tags": ("Tile", "Token", "Objective"),
    },
    "Storm.png": {
        "name": "Psychic Storm",
        "description": "Psychic storm hazard marker placed by Mystic",
        "gm_notes": "hazard:deals 1H energy at end of turn to units within 3",
        "tags": ("Tile", "Token", "Mystic"),
    },
    "Tower1.png": {
        "name": "The Tower (1 HP)",
        "description": "Objective structure with 1 HP remaining (damage reduction 1)",
        "gm_notes": "1hp damage_reduction:1",
        "tags": ("Tile", "Token", "Objective"),
    },
    "Tower2.png": {
        "name": "The Tower (2 HP)",
        "description": "Objective structure with 2 HP remaining (damage reduction 1)",
        "gm_notes": "2hp damage_reduction:1",
        "tags": ("Tile", "Token", "Objective"),
    },
    "Tower3.png": {
        "name": "The Tower (3 HP)",
        "description": "Objective structure with 3 HP remaining (damage reduction 1)",
        "gm_notes": "3hp damage_reduction:1",
        "tags": ("Tile", "Token", "Objective"),
    },
    "Tower4.png": {
        "name": "The Tower (4 HP)",
        "description": "Objective structure with 4 HP remaining (damage reduction 1)",
        "gm_notes": "4hp damage_reduction:1",
        "tags": ("Tile", "Token", "Objective"),
    },
}


def build_tags(*names: str) -> dict[str, str]:
    """TTS numbers its tags from "1"; a card/tile/figurine is tagged broadest-first."""
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
    """CSV cell -> int. Blank, missing or non-numeric all mean 0."""
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


def terrain_gm_notes(row: dict) -> str:
    lines = []
    defend = _int(row, "Defend Points")
    attack = _int(row, "Attack Points")
    tokens = _int(row, "Tokens")
    if defend > 0 or attack > 0:
        lines.append(f"defend:{defend}pts")
        lines.append(f"attack:{attack}pts")
    if tokens > 0:
        lines.append(f"tokens:{tokens}")
    rules = latex_to_text(_cell(row, "Rules"))
    if rules:
        lines.append(rules)
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

def read_rows(csv_name: str) -> list[dict]:
    path = WORKSPACE / csv_name
    if not path.is_file():
        sys.exit(f"Error: expected card CSV not found: {path}")
    with open(path, newline="", encoding="utf-8") as fh:
        return [row for row in csv.DictReader(fh) if _print_id_nonzero(row)]


def enumerate_cards() -> list[dict]:
    entries: list[dict] = []

    # 1. Action cards
    for csv_name in ACTION_CSVS:
        type_tag = CARD_TYPE_TAGS[csv_name]
        is_pilot = csv_name == "Pilot actions.csv"
        is_drone = csv_name == "Drone actions.csv"
        for row in read_rows(csv_name):
            gm_notes = action_gm_notes(row, is_pilot, is_drone)
            image_name = f"{_cell(row, 'Group')}_{_cell(row, 'Name')}.png"
            entries.append({
                "name": card_display_name(row),
                "description": description_for(gm_notes, row, is_pilot),
                "gm_notes": gm_notes,
                "tags": build_tags("Card", "Action", type_tag, faction_tag(row)),
                "lua_script": "",
                "face": ALL_CARD_IMAGES_URL + quote(image_name),
                "back": NORMAL_CARD_BACK_URL,
                "type": "0",
                "sideways": "false",
                "_local_image": IMAGE_DIR / image_name,
            })

    # 2. Frame cards
    for row in read_rows(FRAMES_CSV):
        gm_notes = frame_gm_notes(row)
        image_name = card_image_name(f"frame/{_cell(row, 'Name')}.tex")
        entries.append({
            "name": _cell(row, "Name"),
            "description": description_for(gm_notes, row, is_pilot=False),
            "gm_notes": gm_notes,
            "tags": build_tags("Card", "Frame", faction_tag(row)),
            "lua_script": "",
            "face": ALL_CARD_IMAGES_URL + quote(image_name),
            "back": FRAMES_CARD_BACK_URL,
            "type": "0",
            "sideways": "false",
            "_local_image": IMAGE_DIR / image_name,
        })

    # 3. Terrain / Objective cards
    for row in read_rows(TERRAIN_CSV):
        gm_notes = terrain_gm_notes(row)
        image_name = card_image_name(f"terrain/{_cell(row, 'Name')}.tex")
        is_obj = _int(row, "Defend Points") > 0 or _int(row, "Attack Points") > 0
        tags = build_tags("Card", "Objective" if is_obj else "Terrain")
        entries.append({
            "name": _cell(row, "Name"),
            "description": description_for(gm_notes, row, is_pilot=False),
            "gm_notes": gm_notes,
            "tags": tags,
            "lua_script": "",
            "face": ALL_CARD_IMAGES_URL + quote(image_name),
            "back": TERRAIN_CARD_BACK_URL,
            "type": "0",
            "sideways": "false",
            "_local_image": IMAGE_DIR / image_name,
        })

    if not entries:
        sys.exit("Error: no printable rows found across action, frame, and terrain CSVs.")
    return entries


def enumerate_tiles() -> list[dict]:
    """Enumerate game tokens as TTS Tiles: every image in tts_assets/ (excluding
    card backs) plus unique drone tokens from Drone actions.csv / pictures/."""
    tiles: list[dict] = []
    seen_images: set[str] = set()

    # 1. Images in tts_assets/ (tokens only; card backs excluded)
    if TTS_ASSETS_DIR.is_dir():
        for file_path in sorted(TTS_ASSETS_DIR.glob("*.png")):
            filename = file_path.name
            if filename.endswith("_back.png"):
                continue
            seen_images.add(filename)
            meta = TTS_ASSET_TOKENS.get(filename, {})
            name = meta.get("name") or file_path.stem.replace("_", " ")
            desc = meta.get("description", "")
            gm = meta.get("gm_notes", "")
            tag_names = meta.get("tags") or ("Tile", "Token")
            url = TTS_ASSETS_URL + quote(filename)
            tiles.append({
                "name": name,
                "description": desc,
                "gm_notes": gm,
                "tags": build_tags(*tag_names),
                "lua_script": "",
                "face": url,
                "back": url,
                "type": "2",
                "thickness": "0.5",
                "stackable": "true",
                "_local_image": file_path,
            })

    # 2. Unique Drone tokens from Drone actions.csv (art from pictures/)
    seen_drone_groups: set[str] = set()
    for row in read_rows("Drone actions.csv"):
        group = _cell(row, "Group")
        if group in seen_drone_groups:
            continue
        seen_drone_groups.add(group)
        card_img = _cell(row, "CardImg")  # e.g. "Swarm.png", "Gun Tower.png", "Attack Dog.png"
        if not card_img or card_img in seen_images:
            continue
        seen_images.add(card_img)

        hp = _int(row, "Drone_Health")
        mv = _cell(row, "Drone_MV") or "0"
        atk = attack_line(row)
        blk = block_line(row, is_pilot=False)
        faction = faction_tag(row)

        gm_parts = [f"drone:{hp}hp {mv}mv"]
        if atk:
            gm_parts.append(atk)
        if blk:
            gm_parts.append(blk)
        if _cell(row, "Faction"):
            gm_parts.append(f"faction:{faction}")
        gm = "\n".join(gm_parts)

        desc = f"{faction} drone unit summoned by {group} action"
        url = PICTURES_URL + quote(card_img)
        tiles.append({
            "name": group,
            "description": desc,
            "gm_notes": gm,
            "tags": build_tags("Tile", "Token", "Drone", faction),
            "lua_script": "",
            "face": url,
            "back": url,
            "type": "2",
            "thickness": "0.5",
            "stackable": "true",
            "_local_image": PICTURES_DIR / card_img,
        })

    return tiles


def enumerate_figurines() -> list[dict]:
    """Supply each frame's transparent mech artwork as the front and back of a Figurine."""
    figurines: list[dict] = []
    for row in read_rows(FRAMES_CSV):
        name = _cell(row, "Name")
        gm_notes = frame_gm_notes(row)
        mech_img = _cell(row, "CardImg")  # e.g. "foreground/aegis_percival.png"
        url = PICTURES_URL + quote(mech_img)
        figurines.append({
            "name": name,
            "description": description_for(gm_notes, row, is_pilot=False),
            "gm_notes": gm_notes,
            "tags": build_tags("Figurine", "Frame", faction_tag(row)),
            "lua_script": "",
            "face": url,
            "back": url,
            "_local_image": PICTURES_DIR / mech_img,
        })
    return figurines


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def to_indexed_dict(entries: list[dict]) -> dict[str, dict]:
    """Convert a list of items to a stringified 1-based indexed dict (1, 2, 3...).
    Strips internal helper keys (like `_local_image`)."""
    out: dict[str, dict] = {}
    for idx, entry in enumerate(entries, start=1):
        clean = {k: v for k, v in entry.items() if not k.startswith("_")}
        out[str(idx)] = clean
    return out


def main():
    parser = argparse.ArgumentParser(
        description="Write Tabletop Simulator metadata to json/cards.json."
    )
    parser.add_argument("--output", default="json/cards.json",
                        help="File to write (default: json/cards.json).")
    parser.add_argument("--quiet", action="store_true",
                        help="Only print the summary and any warnings.")
    args = parser.parse_args()

    _check_card_type_tags()
    _check_glossary_covers_keywords()

    output_path = WORKSPACE / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cards = enumerate_cards()
    tiles = enumerate_tiles()
    figurines = enumerate_figurines()

    doc = {
        "Card": to_indexed_dict(cards),
        "Tile": to_indexed_dict(tiles),
        "Token": {},
        "Figurine": to_indexed_dict(figurines),
    }

    json_text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    output_path.write_text(json_text, encoding="utf-8")

    if not args.quiet:
        print(f"Cards ({len(cards)}):")
        for c in cards:
            print(f"  {c['name']}  <- {c['face']}")
        print(f"\nTiles ({len(tiles)}):")
        for t in tiles:
            print(f"  {t['name']}  <- {t['face']}")
        print(f"\nFigurines ({len(figurines)}):")
        for f in figurines:
            print(f"  {f['name']}  <- {f['face']}")

    print(
        f"\nWrote {len(cards)} card(s), {len(tiles)} tile(s), and {len(figurines)} figurine(s) "
        f"to {output_path.relative_to(WORKSPACE)}"
    )

    # Check local image existence
    missing_cards = [c["name"] for c in cards if not c["_local_image"].is_file()]
    if missing_cards:
        print(f"\nWarning: {len(missing_cards)} card image(s) not found in {IMAGE_DIR.name}/:")
        for name in missing_cards:
            print(f"  {name}")

    missing_tiles = [t["name"] for t in tiles if not t["_local_image"].is_file()]
    if missing_tiles:
        print(f"\nWarning: {len(missing_tiles)} tile image(s) not found locally:")
        for name in missing_tiles:
            print(f"  {name}")

    missing_figurines = [f["name"] for f in figurines if not f["_local_image"].is_file()]
    if missing_figurines:
        print(f"\nWarning: {len(missing_figurines)} figurine image(s) not found locally:")
        for name in missing_figurines:
            print(f"  {name}")

    strays = sorted(p.name for p in output_path.parent.glob("*.json") if p != output_path)
    if strays:
        print(f"\nWarning: {len(strays)} other JSON file(s) sit alongside {output_path.name} "
              f"in {output_path.parent.name}/; every card is in {output_path.name} now:")
        for name in strays:
            print(f"  {name}")

    if _unknown_macros:
        print("\nWarning: these LaTeX macros are used in the CSVs but are not defined in "
              "generateCards.py, so they were expanded to their bare name:")
        for name in sorted(_unknown_macros):
            print(f"  \\{name}")


if __name__ == "__main__":
    main()
