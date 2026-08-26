"""Card and deck loading, and deck-construction legality (rules.tex:812)."""

from __future__ import annotations

import pytest

from playtest.engine import cards as cardlib
from playtest.engine.cards import (
    parse_initiative,
    parse_keywords,
    parse_knockback,
    parse_persistence,
    validate_deck,
    weapon_slots_used,
)
from playtest.engine.types import ZONES

from ._helpers import CATALOGUE, FRAMES


# --------------------------------------------------------------------------
# Cell parsing
# --------------------------------------------------------------------------


def test_initiative_is_a_tuple_and_quick_step_has_two_values():
    assert parse_initiative("7") == (7,)
    assert parse_initiative("8,3") == (8, 3)
    assert CATALOGUE["Booster_Quick Step"].initiative == (8, 3)
    assert CATALOGUE["Spear_Thrust"].initiative == (7,)


def test_movement_parses_explicit_plus_and_blank_columns():
    assert CATALOGUE["Booster_Full speed ahead"].movement == 5
    assert CATALOGUE["Chainsaw_Rip"].movement == -4
    assert CATALOGUE["Spear_Thrust"].movement == 0


def test_persistence_zero_integer_and_infinity():
    assert parse_persistence("0") == 0
    assert parse_persistence("") == 0
    assert parse_persistence("3") == 3
    assert parse_persistence(r"\infty") is None
    assert CATALOGUE["Cannon_Fullbore"].persistence is None
    assert CATALOGUE["Specialist_Master duelist"].persistence == 3
    assert CATALOGUE["Spear_Thrust"].persistence == 0


def test_keywords_come_from_either_latex_form():
    assert "feint" in parse_keywords(r"\fullfeint")
    assert "feint" in parse_keywords(r"\feint")
    assert "guardbreak" in parse_keywords(r"\guardbreak \\ \fullreload")
    assert "reload" in parse_keywords(r"\guardbreak \\ \fullreload")
    assert parse_knockback(r"\fullknockback{2}") == 2
    assert parse_knockback(r"\knockback{2} \\ \guardbreak") == 2
    assert parse_knockback("no knockback here") == 0


def test_card_keywords_on_real_cards():
    assert CATALOGUE["Sword_Feint"].keywords >= {"feint"}
    assert CATALOGUE["Knife_Lunge"].keywords >= {"closequarters", "committed"}
    assert CATALOGUE["Kinetic Hammer_Batter"].knockback == 2
    assert "onhit" in CATALOGUE["Stun Baton_Shock"].keywords
    assert "reload" in CATALOGUE["Cannon_Fullbore"].keywords


def test_key_is_group_underscore_name():
    card = CATALOGUE["Assault Rifle_Aimed Fire"]
    assert card.key == f"{card.group}_{card.name}"
    # Two groups share the name "Thrust"; the key keeps them apart.
    assert CATALOGUE["Spear_Thrust"].group == "Spear"
    assert CATALOGUE["Halberd_Thrust"].group == "Halberd"


# --------------------------------------------------------------------------
# Card types and the CSV quirks
# --------------------------------------------------------------------------


def test_card_types_split_basics_from_faction_frame_cards():
    assert CATALOGUE["Basic_Punch"].card_type == "basic"
    assert CATALOGUE["Frame_Bio-regen"].card_type == "frame"
    assert CATALOGUE["Frame_Bio-regen"].faction == "Collective"
    assert CATALOGUE["Booster_Accelerate"].card_type == "booster"
    assert CATALOGUE["Bruiser_Intimidate"].card_type == "pilot"
    assert CATALOGUE["Swarm_Swarm"].card_type == "drone"
    assert CATALOGUE["Spear_Thrust"].card_type == "weapon"


def test_pilot_cards_get_the_implicit_high_block():
    """Not printed in `Pilot actions.csv`, but every pilot card blocks High."""
    pilot = CATALOGUE["Bruiser_Intimidate"]
    assert pilot.blocks["High"] == 1
    assert pilot.blocks["Mid"] == 0 and pilot.blocks["Low"] == 0
    assert pilot.block_zones == {"High"}
    assert pilot.super_block_zones == frozenset()


def test_blank_columns_read_as_zero_not_none():
    pilot = CATALOGUE["Mystic_Teleport"]
    assert all(pilot.attacks[z] == 0 for z in ZONES)
    assert all(pilot.ranges[z] == 0 for z in ZONES)
    assert not pilot.is_attack


def test_drone_columns():
    swarm = CATALOGUE["Swarm_Swarm"]
    assert swarm.drone_health == 3 and swarm.drone_movement == 3
    assert swarm.persistence is None


def test_super_block_is_block_value_two_or_more():
    assert CATALOGUE["Sword_Parry"].blocks["Mid"] == 2
    assert CATALOGUE["Sword_Parry"].super_block_zones == {"Mid"}
    assert CATALOGUE["Basic_Block"].super_block_zones == frozenset()
    assert CATALOGUE["Basic_Block"].block_zones == {"High", "Mid", "Low"}


def test_is_ranged_only_counts_zones_that_actually_attack():
    # Blocks Mid but its only attack zone is melee -> not a ranged attack.
    assert not CATALOGUE["Chainsaw_Rip"].is_ranged
    assert CATALOGUE["Cannon_Fullbore"].is_ranged
    assert CATALOGUE["Basic_Throw"].is_ranged
    # A card with no attack at all is not ranged.
    assert not CATALOGUE["Shield_Full Guard"].is_ranged


# --------------------------------------------------------------------------
# Frames
# --------------------------------------------------------------------------


def test_frame_armour_maps_top_side_low_to_high_mid_low():
    kuwagata = FRAMES["Kuwagata"]
    assert kuwagata.armour == {"High": 4, "Mid": 4, "Low": 4}
    assert kuwagata.deck_size == 21
    hector = FRAMES["Hector MkI"]
    assert hector.armour["High"] == 5 and hector.armour["Mid"] == 4


def test_frame_keywords_and_shield_value():
    assert FRAMES["Hannael"].keywords == {"flying", "shield"}
    assert FRAMES["Hannael"].shield == 1
    assert FRAMES["Elemiah"].shield == 2
    assert "deathstrike" in FRAMES["Flamekin"].keywords
    assert FRAMES["Percival MkIV"].keywords == frozenset()


def test_every_frame_row_loads():
    assert len(FRAMES) == 12


# --------------------------------------------------------------------------
# Decks
# --------------------------------------------------------------------------


def test_load_deck_resolves_short_names():
    deck = cardlib.load_deck("aegis_hector", CATALOGUE)
    assert len(deck) == FRAMES["Hector MkI"].deck_size
    assert all(card.key in CATALOGUE for card in deck)


def test_frame_for_deck_handles_loose_filenames():
    assert cardlib.frame_for_deck("deck_revolution_ripper", FRAMES).name == "RipperSmasher"
    assert cardlib.frame_for_deck("deck_guild_salaryman", FRAMES).name == "J7R-Salaryman"
    assert cardlib.frame_for_deck("deck_aegis_hector", FRAMES).name == "Hector MkI"


def test_weapon_slots_counted_by_worst_duplicated_card():
    """One slot per copy of the most-duplicated card in a weapon group."""
    single = [CATALOGUE["Spear_Thrust"], CATALOGUE["Spear_Jab"]]
    assert weapon_slots_used(single) == {"Spear": 1}
    doubled = single + [CATALOGUE["Spear_Thrust"]]
    assert weapon_slots_used(doubled) == {"Spear": 2}
    # Drones take a weapon slot too.
    assert weapon_slots_used([CATALOGUE["Swarm_Swarm"]]) == {"Swarm": 1}


# --------------------------------------------------------------------------
# Deck legality
# --------------------------------------------------------------------------


def _deck(*keys):
    return [CATALOGUE[k] for k in keys]


def test_deck_must_be_exactly_the_frame_deck_size():
    report = validate_deck(_deck("Basic_Punch"), FRAMES["Kuwagata"])
    assert not report.legal
    assert any("deck size" in e for e in report.errors)


def test_at_most_four_pilot_cards_from_one_pilot_with_no_duplicates():
    spec = FRAMES["Adam"]
    too_many = _deck(
        "Bruiser_Relentless Assault", "Bruiser_Intimidate",
        "Bruiser_Net Strength", "Bruiser_Lockdown", "Mystic_Teleport",
    )
    errors = validate_deck(too_many, spec).errors
    assert any("pilot cards (max 4)" in e for e in errors)
    assert any("several pilots" in e for e in errors)

    dupes = _deck("Bruiser_Intimidate", "Bruiser_Intimidate")
    assert any("duplicate pilot" in e for e in validate_deck(dupes, spec).errors)


def test_booster_count_is_capped_but_duplicates_are_allowed():
    spec = FRAMES["Adam"]                  # 0 booster slots
    errors = validate_deck(_deck("Booster_Accelerate"), spec).errors
    assert any("booster cards (max 0)" in e for e in errors)

    hector = FRAMES["Hector MkI"]          # 2 booster slots, duplicates fine
    twice = _deck("Booster_Accelerate", "Booster_Accelerate")
    assert not any("booster" in e for e in validate_deck(twice, hector).errors)


def test_faction_locked_cards_only_go_in_matching_decks():
    errors = validate_deck(_deck("Frame_Bio-regen"), FRAMES["Kuwagata"]).errors
    assert any("Collective-only" in e for e in errors)
    ok = validate_deck(_deck("Frame_Bio-regen"), FRAMES["Adam"]).errors
    assert not any("only" in e for e in ok)


def test_weapon_slot_cap():
    spec = FRAMES["Adam"]                  # 2 weapon slots
    three_groups = _deck("Spear_Thrust", "Sword_Lunge", "Knife_Stab")
    errors = validate_deck(three_groups, spec).errors
    assert any("weapon slots used (max 2)" in e for e in errors)


def test_duplicate_frame_cards_are_illegal():
    errors = validate_deck(
        _deck("Frame_Bio-regen", "Frame_Bio-regen"), FRAMES["Adam"]
    ).errors
    assert any("duplicate frame cards" in e for e in errors)


# --------------------------------------------------------------------------
# The shipped decks
# --------------------------------------------------------------------------


def test_every_shipped_deck_is_legal():
    """`decks/deck_*.csv` all satisfy deck construction (rules.tex:812).

    `deck_aegis_percival` used to fail here with four weapon groups against
    Percival's three slots; Stun Baton was dropped and the deck backfilled
    with basics.
    """
    reports = {r.deck: r for r in cardlib.validate_all_decks()}
    assert len(reports) == 12
    failures = {name: r.errors for name, r in reports.items() if not r.legal}
    assert failures == {}


def test_percival_fills_its_weapon_slots():
    """A drone takes a weapon slot like a weapon does.

    Percival's fourth slot is the Attack Dog pair -- which is why the frame
    reads 4 rather than 3. The slot count is asserted against the frame rather
    than a literal so a balance edit to either only has to be made once.
    """
    deck = cardlib.load_deck("deck_aegis_percival", CATALOGUE)
    spec = FRAMES["Percival MkIV"]
    slots = weapon_slots_used(deck)
    assert sum(slots.values()) == spec.weapon_slots
    assert set(slots) == {"Shield", "Greatsword", "Assault Rifle", "Attack Dog"}
    assert len(deck) == spec.deck_size


@pytest.mark.parametrize("name", sorted(cardlib.available_decks()))
def test_every_shipped_deck_loads_without_unknown_cards(name):
    deck = cardlib.load_deck(name, CATALOGUE)
    assert deck, f"{name} is empty"
    assert cardlib.frame_for_deck(name, FRAMES) is not None
