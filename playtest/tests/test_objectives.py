"""The scripted objectives, their scoring and their latching rules.

Scoring timing is an engine assumption (SPEC.md): the objectives in `LATCHING`
lock in the moment their condition is met; the rest are settled once, after
turn 5.
"""

from __future__ import annotations

import pytest

from playtest.engine import objectives as O
from playtest.engine.objectives import (
    LATCHING,
    OBJECTIVE_NAMES,
    OBJECTIVE_TOKENS,
    create_objective,
    latch_objectives,
    objective_score,
)
from playtest.engine.state import damage_token, deal_damage, victory_points
from playtest.engine.terrain import load_terrain_cards
from playtest.engine.types import Pos

from ._helpers import add_frame, give, make_state, run_attack

#: Points as printed on the terrain cards, read from `Terrain_square.csv`
#: itself: a balance edit to a card must not need a second edit here to agree.
POINTS = {
    name: (card.defend_points, card.attack_points)
    for name, card in load_terrain_cards().items()
    if card.is_objective
}


def setup_objective(state, name, owner=0, tiles=(), spawns=()):
    defend, attack = POINTS[name]
    return create_objective(
        state, name, owner, defend=defend, attack=attack, tiles=tiles, spawns=spawns
    )


def finish(state):
    state.phase = "finished"
    latch_objectives(state)
    return state


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------


def test_every_scoring_terrain_card_is_an_objective_the_engine_plays():
    """The CSV is the source of truth: a card with points must be scriptable."""
    assert set(O.SCORERS) == set(OBJECTIVE_NAMES)
    assert set(OBJECTIVE_NAMES) == set(POINTS), (
        "every terrain card carrying points needs a scorer, and vice versa"
    )
    assert "Helpcard" not in OBJECTIVE_NAMES, "the Helpcard is a legend, not real"


def test_latching_is_only_for_conditions_that_cannot_be_undone():
    assert LATCHING == {
        "Power Reactors", "The Tower", "Fugitive", "The Egg",
        "Riverside", "Car Park", "Dome Campus",
    }
    assert not (LATCHING & {"Shiny Thing", "Lake Crosses", "Solar Farm"}), (
        "a carried token and a charge count can still change hands"
    )


def test_creating_an_objective_spawns_its_tokens():
    state = make_state()
    obj = setup_objective(
        state, "Power Reactors", owner=0,
        tiles=[Pos(1, 1)], spawns=[Pos(1, 1), Pos(2, 2), Pos(3, 3), Pos(4, 4)],
    )
    assert len(obj.token_ids) == 4
    tokens = O.tokens_of(state, obj)
    assert all(t.hp == 2 and t.max_hp == 2 and t.kind == "reactor" for t in tokens)
    assert {t.pos for t in tokens} == {Pos(1, 1), Pos(2, 2), Pos(3, 3), Pos(4, 4)}


def test_objectives_without_tokens_spawn_none():
    state = make_state()
    obj = setup_objective(state, "Triangle", owner=0, tiles=[Pos(1, 1)])
    assert obj.token_ids == ()
    assert "Triangle" not in OBJECTIVE_TOKENS


def test_the_defender_is_whoever_brought_the_card():
    state = make_state()
    obj = setup_objective(state, "Triangle", owner=1, tiles=[Pos(3, 3)])
    add_frame(state, 1, "Kuwagata", Pos(3, 3))
    finish(state)
    seat, value = objective_score(state, obj)
    assert (seat, value) == (1, obj.defend), "the owner scores the green number"


# --------------------------------------------------------------------------
# Power Reactors -- 4 tokens, 2 HP each, attacker scores on 3+ destroyed
# --------------------------------------------------------------------------


def test_power_reactors_go_to_the_attacker_once_three_are_destroyed():
    state = make_state()
    obj = setup_objective(
        state, "Power Reactors", owner=0, tiles=[Pos(1, 1)],
        spawns=[Pos(1, 1), Pos(2, 1), Pos(3, 1), Pos(4, 1)],
    )
    tokens = O.tokens_of(state, obj)
    for token in tokens[:2]:
        damage_token(state, token, 2)
    latch_objectives(state)
    assert obj.latched is None and objective_score(state, obj) == (None, 0)

    damage_token(state, tokens[2], 2)
    latch_objectives(state)
    assert obj.latched == 1, "the attacker latches it at three"
    assert objective_score(state, obj) == (1, obj.attack)


def test_power_reactors_stay_with_the_defender_if_fewer_than_three_die():
    state = make_state()
    obj = setup_objective(
        state, "Power Reactors", owner=0, tiles=[Pos(1, 1)],
        spawns=[Pos(1, 1), Pos(2, 1), Pos(3, 1), Pos(4, 1)],
    )
    damage_token(state, O.tokens_of(state, obj)[0], 2)
    finish(state)
    assert objective_score(state, obj) == (0, obj.defend)


def test_a_reactor_takes_two_hits_and_ignores_which_zone_hit_it():
    state = make_state()
    obj = setup_objective(
        state, "Power Reactors", owner=1, tiles=[Pos(2, 1)], spawns=[Pos(2, 1)],
    )
    token = O.tokens_of(state, obj)[0]
    attacker = add_frame(state, 0, "Kuwagata", Pos(1, 1))
    add_frame(state, 1, "Adam", Pos(9, 9))
    uid = give(state, attacker, "Basic_Kick")             # Low 1
    from playtest.engine import combat
    attack = combat.declare_attack(
        state, attacker, uid, target_kind="token", target_id=token.id
    )
    combat.finish_target(state, attack)
    assert token.hp == 1 and token.alive


# --------------------------------------------------------------------------
# The Tower -- 4 HP, attacker scores if destroyed
# --------------------------------------------------------------------------


def test_the_tower_latches_for_the_attacker_when_destroyed():
    state = make_state()
    obj = setup_objective(state, "The Tower", owner=0, tiles=[Pos(5, 5)],
                          spawns=[Pos(5, 5)])
    token = O.tokens_of(state, obj)[0]
    assert token.hp == 4
    damage_token(state, token, 4)
    latch_objectives(state)
    assert obj.latched == 1
    assert objective_score(state, obj) == (1, obj.attack)


def test_a_standing_tower_scores_for_the_defender_at_the_end():
    state = make_state()
    obj = setup_objective(state, "The Tower", owner=0, tiles=[Pos(5, 5)],
                          spawns=[Pos(5, 5)])
    damage_token(state, O.tokens_of(state, obj)[0], 3)
    assert objective_score(state, obj) == (None, 0), "not settled mid-game"
    finish(state)
    assert objective_score(state, obj) == (0, obj.defend)


# --------------------------------------------------------------------------
# Shiny Thing -- carried, dropped on damage, held at the end
# --------------------------------------------------------------------------


def test_the_shiny_thing_is_picked_up_on_contact():
    state = make_state()
    obj = setup_objective(state, "Shiny Thing", owner=0, spawns=[Pos(4, 4)])
    token = O.tokens_of(state, obj)[0]
    frame = add_frame(state, 1, "Kuwagata", Pos(3, 4))

    O.on_move(state, frame, Pos(3, 4))
    assert token.carrier is None, "adjacent is not contact"

    frame.pos = Pos(4, 4)
    O.on_move(state, frame, Pos(3, 4))
    assert token.carrier == frame.id


def test_the_shiny_thing_travels_with_its_carrier():
    state = make_state()
    obj = setup_objective(state, "Shiny Thing", owner=0, spawns=[Pos(4, 4)])
    token = O.tokens_of(state, obj)[0]
    frame = add_frame(state, 1, "Kuwagata", Pos(4, 4))
    O.on_move(state, frame, Pos(4, 4))
    frame.pos = Pos(6, 6)
    O.on_move(state, frame, Pos(4, 4))
    assert token.pos == Pos(6, 6)


def test_damage_drops_the_shiny_thing_toward_the_damage_source():
    state = make_state()
    obj = setup_objective(state, "Shiny Thing", owner=0, spawns=[Pos(5, 5)])
    token = O.tokens_of(state, obj)[0]
    carrier = add_frame(state, 1, "Kuwagata", Pos(5, 5))
    attacker = add_frame(state, 0, "Kuwagata", Pos(1, 5))
    O.on_move(state, carrier, Pos(5, 5))
    assert token.carrier == carrier.id

    deal_damage(state, carrier, "Mid", 1, source=attacker)
    assert token.carrier is None
    assert token.pos == Pos(4, 5), "the adjacent tile nearest the source"


def test_a_melee_hit_knocks_the_token_into_the_attackers_own_tile():
    """"so a melee hit transfers ownership" -- the rulebook's own note.

    The drop lands in the tile nearest the damage, which for a melee attack is
    the tile the attacker is standing in, and whoever is standing on a loose
    token has it.
    """
    state = make_state()
    obj = setup_objective(state, "Shiny Thing", owner=0, spawns=[Pos(5, 5)])
    token = O.tokens_of(state, obj)[0]
    carrier = add_frame(state, 1, "Kuwagata", Pos(5, 5))
    attacker = add_frame(state, 0, "Kuwagata", Pos(4, 5))
    O.on_move(state, carrier, Pos(5, 5))
    assert token.carrier == carrier.id

    deal_damage(state, carrier, "Mid", 1, source=attacker)
    assert token.pos == attacker.pos
    assert token.carrier == attacker.id, "it lands at the attacker's feet"


def test_a_token_dropped_at_nobodys_feet_stays_loose():
    state = make_state()
    obj = setup_objective(state, "Shiny Thing", owner=0, spawns=[Pos(5, 5)])
    token = O.tokens_of(state, obj)[0]
    carrier = add_frame(state, 1, "Kuwagata", Pos(5, 5))
    sniper = add_frame(state, 0, "Kuwagata", Pos(1, 5))
    O.on_move(state, carrier, Pos(5, 5))

    deal_damage(state, carrier, "Mid", 1, source=sniper)
    assert token.pos == Pos(4, 5)
    assert token.carrier is None


def test_the_team_holding_the_shiny_thing_at_the_end_scores_it():
    state = make_state()
    obj = setup_objective(state, "Shiny Thing", owner=0, spawns=[Pos(5, 5)])
    token = O.tokens_of(state, obj)[0]
    thief = add_frame(state, 1, "Kuwagata", Pos(5, 5))
    O.on_move(state, thief, Pos(5, 5))
    finish(state)
    assert objective_score(state, obj) == (1, obj.attack)


def test_nobody_scores_an_unheld_shiny_thing():
    state = make_state()
    obj = setup_objective(state, "Shiny Thing", owner=0, spawns=[Pos(5, 5)])
    finish(state)
    assert objective_score(state, obj) == (None, 0)


# --------------------------------------------------------------------------
# Triangle and Church -- "only one team" objectives
# --------------------------------------------------------------------------


def test_the_triangle_scores_only_when_one_team_stands_on_it():
    state = make_state()
    tiles = [Pos(4, 4), Pos(5, 4)]
    obj = setup_objective(state, "Triangle", owner=0, tiles=tiles)
    mine = add_frame(state, 0, "Kuwagata", Pos(4, 4))
    finish(state)
    assert objective_score(state, obj) == (0, obj.defend)

    add_frame(state, 1, "Adam", Pos(5, 4))
    assert objective_score(state, obj) == (None, 0), "contested scores nothing"


def test_the_triangle_is_not_settled_before_the_end_of_the_game():
    state = make_state()
    obj = setup_objective(state, "Triangle", owner=0, tiles=[Pos(4, 4)])
    add_frame(state, 0, "Kuwagata", Pos(4, 4))
    assert objective_score(state, obj) == (None, 0)


def test_dead_frames_do_not_hold_the_triangle():
    state = make_state()
    obj = setup_objective(state, "Triangle", owner=0, tiles=[Pos(4, 4)])
    frame = add_frame(state, 0, "Kuwagata", Pos(4, 4))
    frame.alive = False
    finish(state)
    assert objective_score(state, obj) == (None, 0)


def test_the_church_uses_a_range_of_two():
    state = make_state()
    obj = setup_objective(state, "Church", owner=1, tiles=[Pos(5, 5)])
    near = add_frame(state, 1, "Kuwagata", Pos(7, 5))     # exactly 2 away
    far = add_frame(state, 0, "Adam", Pos(8, 5))          # 3 away
    finish(state)
    assert objective_score(state, obj) == (1, obj.defend)

    far.pos = Pos(7, 4)
    assert objective_score(state, obj) == (None, 0), "now both teams are within 2"


# --------------------------------------------------------------------------
# Holo Spires
# --------------------------------------------------------------------------


def test_holo_spires_go_to_the_attacker_if_any_attacker_stands_on_one():
    state = make_state()
    tiles = [Pos(3, 3), Pos(4, 3)]
    obj = setup_objective(state, "Holo Spires", owner=0, tiles=tiles)
    add_frame(state, 1, "Adam", Pos(4, 3))
    finish(state)
    assert objective_score(state, obj) == (1, obj.attack)


def test_holo_spires_default_to_the_defender():
    state = make_state()
    obj = setup_objective(state, "Holo Spires", owner=0, tiles=[Pos(3, 3)])
    add_frame(state, 0, "Kuwagata", Pos(3, 3))
    finish(state)
    assert objective_score(state, obj) == (0, obj.defend)


# --------------------------------------------------------------------------
# Fugitive
# --------------------------------------------------------------------------


def drop_token(state, obj, pos):
    """Put an objective's token on the board the way setup would."""
    token = O.tokens_of(state, obj)[0]
    O.place_token(state, token, pos)
    return token


def test_the_fugitive_is_picked_up_by_a_defender_that_walks_onto_it():
    """Held, not towed: "if a frame enters the tile the token is in" (826)."""
    state = make_state()
    obj = setup_objective(state, "Fugitive", owner=0, tiles=[Pos(9, 9)])
    token = drop_token(state, obj, Pos(2, 2))
    escort = add_frame(state, 0, "Kuwagata", Pos(3, 2))
    O.on_move(state, escort, Pos(3, 2))
    assert token.carrier is None, "standing next to it is not holding it"

    escort.pos = Pos(2, 2)
    O.on_move(state, escort, Pos(3, 2))
    assert token.carrier == escort.id

    escort.pos = Pos(5, 5)
    O.on_move(state, escort, Pos(2, 2))
    assert token.pos == Pos(5, 5), "it comes along once held"


def test_the_enemy_cannot_hold_the_fugitive():
    """"The fugitive token can be held by allies" -- only by allies."""
    state = make_state()
    obj = setup_objective(state, "Fugitive", owner=0, tiles=[Pos(9, 9)])
    token = drop_token(state, obj, Pos(2, 2))
    enemy = add_frame(state, 1, "Adam", Pos(2, 2))
    O.on_move(state, enemy, Pos(2, 3))
    assert token.carrier is None


def test_a_damaged_escort_drops_the_fugitive():
    state = make_state()
    obj = setup_objective(state, "Fugitive", owner=0, tiles=[Pos(9, 9)])
    token = drop_token(state, obj, Pos(5, 5))
    escort = add_frame(state, 0, "Kuwagata", Pos(5, 5))
    O.on_move(state, escort, Pos(5, 4))
    assert token.carrier == escort.id

    shooter = add_frame(state, 1, "Adam", Pos(5, 8))
    deal_damage(state, escort, "Mid", 1, source=shooter)
    assert token.carrier is None
    assert token.pos == Pos(5, 6), "dropped toward whatever hit them"


def test_the_fugitive_latches_for_the_defender_at_the_objective_point():
    state = make_state()
    obj = setup_objective(state, "Fugitive", owner=0, tiles=[Pos(9, 9)])
    token = drop_token(state, obj, Pos(8, 8))
    escort = add_frame(state, 0, "Kuwagata", Pos(8, 8))
    O.on_move(state, escort, Pos(8, 9))
    escort.pos = Pos(9, 9)
    O.on_move(state, escort, Pos(8, 8))
    assert obj.latched == 0
    assert objective_score(state, obj) == (0, obj.defend)
    # Extraction: it reached the point, so it is off the board -- nothing left
    # to shoot, to carry, or to take back.
    assert not token.alive and token.pos is None and token.carrier is None
    assert token.id not in {t.id for t in O.tokens_of(state, obj) if t.alive}


def test_the_view_says_enough_to_tell_extraction_from_destruction():
    """The client draws "got away" rather than "destroyed" off these three
    facts, and nothing else on the board produces the combination: only an
    extraction settles an objective *for the side that brought it* and takes
    its token off the board."""
    from playtest.engine.serialize import view_for

    state = make_state()
    obj = setup_objective(state, "Fugitive", owner=0, tiles=[Pos(9, 9)])
    drop_token(state, obj, Pos(8, 8))
    escort = add_frame(state, 0, "Kuwagata", Pos(8, 8))
    O.on_move(state, escort, Pos(8, 9))
    escort.pos = Pos(9, 9)
    O.on_move(state, escort, Pos(8, 8))

    view = view_for(state, 0)
    entry = next(o for o in view["board"]["objectives"]
                 if o["name"] == "Fugitive")
    assert entry["settled"] is True
    assert entry["scorer"] == entry["owner"] == 0
    assert not [t for t in view["tokens"]
                if t["objective"] == "Fugitive" and t["alive"]]


def test_a_fugitive_the_attacker_stops_stays_on_the_board():
    """The other way it scores is the clock running out, and then it is still
    sitting wherever it was stopped."""
    state = make_state()
    obj = setup_objective(state, "Fugitive", owner=0, tiles=[Pos(9, 9)])
    token = drop_token(state, obj, Pos(4, 4))
    finish(state)
    O.latch_objectives(state)
    assert objective_score(state, obj)[0] == 1, "the attackers score"
    assert token.alive and token.pos == Pos(4, 4)


def test_a_fugitive_that_never_arrives_scores_for_the_attacker():
    state = make_state()
    obj = setup_objective(state, "Fugitive", owner=0, tiles=[Pos(9, 9)])
    drop_token(state, obj, Pos(2, 2))
    finish(state)
    assert objective_score(state, obj) == (1, obj.attack)


# --------------------------------------------------------------------------
# The Egg
# --------------------------------------------------------------------------


def test_the_egg_needs_two_consecutive_turns_standing_on_it():
    state = make_state()
    obj = setup_objective(state, "The Egg", owner=1, tiles=[Pos(6, 6)])
    frame = add_frame(state, 0, "Kuwagata", Pos(6, 6))

    O.end_of_turn(state)
    assert obj.latched is None, "one turn is not enough"
    O.end_of_turn(state)
    assert obj.latched == 0
    assert objective_score(state, obj) == (0, obj.attack)


def test_leaving_the_egg_resets_the_streak():
    state = make_state()
    obj = setup_objective(state, "The Egg", owner=1, tiles=[Pos(6, 6)])
    frame = add_frame(state, 0, "Kuwagata", Pos(6, 6))
    O.end_of_turn(state)
    frame.pos = Pos(1, 1)
    O.end_of_turn(state)
    assert obj.latched is None
    frame.pos = Pos(6, 6)
    O.end_of_turn(state)
    assert obj.latched is None, "the streak restarted"
    O.end_of_turn(state)
    assert obj.latched == 0


def test_the_egg_stays_latched_once_scored():
    state = make_state()
    obj = setup_objective(state, "The Egg", owner=1, tiles=[Pos(6, 6)])
    frame = add_frame(state, 0, "Kuwagata", Pos(6, 6))
    O.end_of_turn(state)
    O.end_of_turn(state)
    assert obj.latched == 0
    frame.pos = Pos(0, 0)
    O.end_of_turn(state)
    finish(state)
    assert objective_score(state, obj) == (0, obj.attack), "latched means latched"


# --------------------------------------------------------------------------
# Victory points
# --------------------------------------------------------------------------


def test_victory_points_are_kills_plus_objectives():
    state = make_state()
    victim = add_frame(state, 1, "Kuwagata", Pos(1, 1))
    killer = add_frame(state, 0, "Kuwagata", Pos(2, 1))
    victim.damage["Mid"] = 4
    from playtest.engine.state import check_destruction
    check_destruction(state, victim, killer=killer)

    obj = setup_objective(state, "The Tower", owner=1, tiles=[Pos(5, 5)],
                          spawns=[Pos(5, 5)])
    damage_token(state, O.tokens_of(state, obj)[0], 4)
    latch_objectives(state)

    points = victory_points(state)
    assert points[0] == 1 + obj.attack
    assert points[1] == 0


def test_the_view_itemises_the_score_into_kills_and_objectives():
    """The total alone cannot be checked against the board.

    "1 point per opposing frame defeated" is credited once, when the frame
    dies, so it cannot be recounted afterwards -- the view has to carry it,
    and each objective has to say which seat its points went to.
    """
    from playtest.engine.serialize import view_for
    from playtest.engine.state import check_destruction

    state = make_state()
    victim = add_frame(state, 1, "Kuwagata", Pos(1, 1))
    killer = add_frame(state, 0, "Kuwagata", Pos(2, 1))
    victim.damage["Mid"] = 4
    check_destruction(state, victim, killer=killer)

    obj = setup_objective(state, "The Tower", owner=1, tiles=[Pos(5, 5)],
                          spawns=[Pos(5, 5)])
    damage_token(state, O.tokens_of(state, obj)[0], 4)
    latch_objectives(state)

    view = view_for(state, 0)
    assert view["kills"] == {"0": 1, "1": 0}
    scored = [o for o in view["board"]["objectives"] if o["value"]]
    assert [o["scorer"] for o in scored] == [0]
    # The halves add up to the total the header shows.
    assert view["vp"]["0"] == view["kills"]["0"] + sum(
        o["value"] for o in scored if o["scorer"] == 0)


def test_unsettled_objectives_are_worth_nothing_mid_game():
    state = make_state()
    add_frame(state, 0, "Kuwagata", Pos(4, 4))
    setup_objective(state, "Triangle", owner=1, tiles=[Pos(4, 4)])
    assert victory_points(state) == {0: 0, 1: 0}


# --------------------------------------------------------------------------
# Instances, not names
# --------------------------------------------------------------------------


def test_both_seats_can_bring_the_same_objective_card():
    """Each objective deck holds 5 of the 8, so the same card can be brought
    by both players. Objective state is keyed by instance, never by name."""
    state = make_state()
    mine = setup_objective(state, "The Egg", owner=0, tiles=[Pos(2, 2)])
    theirs = setup_objective(state, "The Egg", owner=1, tiles=[Pos(7, 7)])
    assert mine is not theirs and mine.memo is not theirs.memo

    holder = add_frame(state, 1, "Kuwagata", Pos(2, 2))   # stands on *my* Egg
    O.end_of_turn(state)
    O.end_of_turn(state)
    assert mine.latched == 1, "the attacker took the defender's Egg"
    assert theirs.latched is None, "the other copy is untouched"
    assert objective_score(state, mine) == (1, mine.attack)


def test_two_copies_of_a_token_objective_keep_separate_tokens():
    state = make_state()
    a = setup_objective(state, "The Tower", owner=0, tiles=[Pos(2, 2)],
                        spawns=[Pos(2, 2)])
    b = setup_objective(state, "The Tower", owner=1, tiles=[Pos(8, 8)],
                        spawns=[Pos(8, 8)])
    assert set(a.token_ids).isdisjoint(b.token_ids)
    damage_token(state, O.tokens_of(state, a)[0], 4)
    latch_objectives(state)
    assert a.latched == 1 and b.latched is None


def test_ownership_is_provenance_not_board_position():
    """A player places their objectives in their own rows, but what makes
    them the defender is having brought the card."""
    state = make_state()
    obj = setup_objective(state, "Holo Spires", owner=1, tiles=[Pos(0, 0)])
    assert obj.owner == 1
    assert O.other_seat(state, obj.owner) == 0
    add_frame(state, 0, "Kuwagata", Pos(0, 0))            # attacker on the spire
    finish(state)
    assert objective_score(state, obj) == (0, obj.attack)


# --------------------------------------------------------------------------
# The Tower -- and damage reduction, which is per zone
# --------------------------------------------------------------------------


def test_the_tower_takes_one_less_from_every_zone_of_an_attack():
    """The worked example in rules.tex "Damage reduction and increases".

    Spear Impale is 1 High and 2 Low. Against -1 per zone that is 0 and 1,
    for a total of 1 -- not the 2 that taking it off the total would give.
    """
    state = make_state()
    obj = setup_objective(state, "The Tower", owner=1, tiles=[Pos(2, 1)],
                          spawns=[Pos(2, 1)])
    token = O.tokens_of(state, obj)[0]
    assert token.damage_reduction == 1
    attacker = add_frame(state, 0, "Kuwagata", Pos(1, 1))
    add_frame(state, 1, "Adam", Pos(9, 9))
    run_attack(state, attacker, give(state, attacker, "Spear_Impale"),
               token, target_kind="token")
    assert token.hp == 3, "1 High and 2 Low, each reduced by 1"


def test_an_attack_that_cannot_beat_the_reduction_does_nothing():
    state = make_state()
    obj = setup_objective(state, "The Tower", owner=1, tiles=[Pos(2, 1)],
                          spawns=[Pos(2, 1)])
    token = O.tokens_of(state, obj)[0]
    attacker = add_frame(state, 0, "Kuwagata", Pos(1, 1))
    add_frame(state, 1, "Adam", Pos(9, 9))
    run_attack(state, attacker, give(state, attacker, "Basic_Kick"),   # Low 1
               token, target_kind="token")
    assert token.hp == 4 and token.alive


# --------------------------------------------------------------------------
# Riverside -- the attacker's gangs, which the defender must clear out
# --------------------------------------------------------------------------


def test_the_gangs_belong_to_the_attacker_who_places_and_moves_them():
    state = make_state()
    obj = setup_objective(state, "Riverside", owner=0)
    tokens = O.tokens_of(state, obj)
    assert len(tokens) == 3
    assert all(t.owner == 1 for t in tokens), "created by the other side"
    assert all(t.pos is None for t in tokens), "not placed until deployment"
    assert all(t.hp == 1 and t.movement == 1 and t.initiative == 1 for t in tokens)
    assert O.creator_seat(state, obj) == 1


def test_riverside_latches_for_the_defender_once_every_gang_is_dead():
    state = make_state()
    obj = setup_objective(state, "Riverside", owner=0)
    tokens = O.tokens_of(state, obj)
    for token in tokens[:2]:
        damage_token(state, token, 1)
    latch_objectives(state)
    assert obj.latched is None, "two of three is not all of them"

    damage_token(state, tokens[2], 1)
    latch_objectives(state)
    assert obj.latched == 0
    assert objective_score(state, obj) == (0, obj.defend)


def test_a_surviving_gang_scores_for_the_attacker():
    state = make_state()
    obj = setup_objective(state, "Riverside", owner=0)
    damage_token(state, O.tokens_of(state, obj)[0], 1)
    finish(state)
    assert objective_score(state, obj) == (1, obj.attack)


# --------------------------------------------------------------------------
# Car Park -- the defender's refugees, which the attacker must kill
# --------------------------------------------------------------------------


def test_the_refugees_belong_to_the_defender_who_brought_the_card():
    state = make_state()
    obj = setup_objective(state, "Car Park", owner=1)
    tokens = O.tokens_of(state, obj)
    assert len(tokens) == 3 and all(t.owner == 1 for t in tokens)
    assert O.creator_seat(state, obj) == 1


def test_car_park_latches_for_the_attacker_only_when_all_three_are_dead():
    state = make_state()
    obj = setup_objective(state, "Car Park", owner=1)
    tokens = O.tokens_of(state, obj)
    for token in tokens[:2]:
        damage_token(state, token, 1)
    latch_objectives(state)
    assert obj.latched is None

    damage_token(state, tokens[2], 1)
    latch_objectives(state)
    assert obj.latched == 0, "the attacker of a seat-1 card is seat 0"
    assert objective_score(state, obj) == (0, obj.attack)


def test_one_refugee_left_alive_scores_for_the_defender():
    state = make_state()
    obj = setup_objective(state, "Car Park", owner=1)
    damage_token(state, O.tokens_of(state, obj)[0], 1)
    finish(state)
    assert objective_score(state, obj) == (1, obj.defend)


# --------------------------------------------------------------------------
# Solar Farm -- a charge per frame per turn, most charge scores
# --------------------------------------------------------------------------


def test_the_farm_banks_one_charge_per_frame_per_turn():
    state = make_state()
    obj = setup_objective(state, "Solar Farm", owner=0,
                          tiles=[Pos(4, 4), Pos(5, 4)])
    mine = add_frame(state, 0, "Kuwagata", Pos(4, 4))
    add_frame(state, 0, "Adam", Pos(5, 4))
    theirs = add_frame(state, 1, "Fenrir", Pos(9, 9))
    O.end_of_turn(state)
    assert obj.memo["charge"] == {0: 2}

    mine.pos = Pos(9, 1)
    theirs.pos = Pos(5, 4)
    O.end_of_turn(state)
    assert obj.memo["charge"] == {0: 3, 1: 1}
    finish(state)
    assert objective_score(state, obj) == (0, obj.defend)


def test_a_farm_nobody_stood_on_scores_for_nobody():
    state = make_state()
    obj = setup_objective(state, "Solar Farm", owner=0, tiles=[Pos(4, 4)])
    O.end_of_turn(state)
    finish(state)
    assert objective_score(state, obj) == (None, 0)


def test_a_tied_farm_scores_for_nobody():
    state = make_state()
    obj = setup_objective(state, "Solar Farm", owner=0, tiles=[Pos(4, 4), Pos(5, 4)])
    add_frame(state, 0, "Kuwagata", Pos(4, 4))
    add_frame(state, 1, "Fenrir", Pos(5, 4))
    O.end_of_turn(state)
    finish(state)
    assert objective_score(state, obj) == (None, 0)


# --------------------------------------------------------------------------
# Lake Crosses -- both platforms, then a relic to hold on to
# --------------------------------------------------------------------------


def lake(state, owner=0):
    return setup_objective(state, "Lake Crosses", owner=owner,
                           tiles=[Pos(3, 3), Pos(6, 3)])


def test_the_relic_starts_off_the_board_and_needs_both_platforms():
    state = make_state()
    obj = lake(state)
    relic = O.tokens_of(state, obj)[0]
    assert relic.pos is None and relic.carrier is None

    one = add_frame(state, 0, "Kuwagata", Pos(3, 3))
    O.end_of_turn(state)
    assert O.cleanup_decision(state) is None, "one platform is half a ritual"
    assert relic.carrier is None

    other = add_frame(state, 0, "Adam", Pos(6, 3))
    O.end_of_turn(state)
    decision = O.cleanup_decision(state)
    assert decision is not None and decision.seat == 0, (
        "'held by one of those frames' -- the winner says which"
    )
    assert {o["frame"] for o in decision.options} == {one.id, other.id}
    assert relic.carrier is None, "not until they have said"

    O.take_relic(state, other)
    assert relic.carrier == other.id
    assert relic.pos == other.pos
    assert O.cleanup_decision(state) is None, "asked once"
    finish(state)
    assert objective_score(state, obj) == (0, obj.defend)


def test_the_ritual_hands_the_relic_over_with_no_question_when_there_is_one_frame():
    """A frame on each platform is the ritual; one frame on both is not, so a
    single candidate only happens when the second has since been destroyed."""
    from playtest.engine.state import destroy_frame

    state = make_state()
    obj = lake(state)
    relic = O.tokens_of(state, obj)[0]
    one = add_frame(state, 0, "Kuwagata", Pos(3, 3))
    other = add_frame(state, 0, "Adam", Pos(6, 3))
    O.end_of_turn(state)
    destroy_frame(state, other)
    assert O.cleanup_decision(state) is None, "nothing left to choose between"
    assert relic.carrier == one.id


def test_the_relic_is_dropped_if_the_whole_ritual_party_dies():
    from playtest.engine.state import destroy_frame

    state = make_state()
    obj = lake(state)
    relic = O.tokens_of(state, obj)[0]
    for name, spot in (("Kuwagata", Pos(3, 3)), ("Adam", Pos(6, 3))):
        add_frame(state, 0, name, spot)
    O.end_of_turn(state)
    for frame in list(state.frames.values()):
        destroy_frame(state, frame)
    assert O.cleanup_decision(state) is None
    assert relic.carrier is None and relic.pos is None


def test_both_teams_completing_the_ritual_at_once_gives_it_to_neither():
    state = make_state()
    obj = lake(state)
    relic = O.tokens_of(state, obj)[0]
    add_frame(state, 0, "Kuwagata", Pos(3, 3))
    add_frame(state, 0, "Adam", Pos(6, 3))
    add_frame(state, 1, "Fenrir", Pos(3, 3))
    add_frame(state, 1, "Elemiah", Pos(6, 3))
    O.end_of_turn(state)
    assert O.cleanup_decision(state) is None
    assert relic.carrier is None, "there is no first, so nobody takes it"


def test_the_relic_changes_hands_like_any_carried_token():
    state = make_state()
    obj = lake(state)
    relic = O.tokens_of(state, obj)[0]
    O.place_token(state, relic, Pos(5, 5))
    thief = add_frame(state, 1, "Fenrir", Pos(5, 5))
    O.on_move(state, thief, Pos(5, 6))
    assert relic.carrier == thief.id, "either side can hold it"
    finish(state)
    assert objective_score(state, obj) == (1, obj.attack)


def test_a_relic_lying_on_the_ground_scores_for_nobody():
    state = make_state()
    obj = lake(state)
    O.place_token(state, O.tokens_of(state, obj)[0], Pos(5, 5))
    finish(state)
    assert objective_score(state, obj) == (None, 0)


# --------------------------------------------------------------------------
# Dome Campus -- the bomb carrier
# --------------------------------------------------------------------------


def test_the_bomb_latches_for_the_attacker_when_its_carrier_reaches_the_site():
    state = make_state()
    obj = setup_objective(state, "Dome Campus", owner=0, tiles=[Pos(2, 2)])
    runner = add_frame(state, 1, "Fenrir", Pos(8, 8))
    add_frame(state, 1, "Elemiah", Pos(2, 2))     # not the carrier
    O.set_bomb_carrier(state, obj, runner.id)
    O.end_of_turn(state)
    assert obj.latched is None, "an ally standing there is not the bomb"

    runner.pos = Pos(2, 2)
    O.end_of_turn(state)
    assert obj.latched == 1
    assert objective_score(state, obj) == (1, obj.attack)


def test_a_bomb_that_never_arrives_scores_for_the_defender():
    state = make_state()
    obj = setup_objective(state, "Dome Campus", owner=0, tiles=[Pos(2, 2)])
    runner = add_frame(state, 1, "Fenrir", Pos(8, 8))
    O.set_bomb_carrier(state, obj, runner.id)
    O.end_of_turn(state)
    finish(state)
    assert objective_score(state, obj) == (0, obj.defend)


# --------------------------------------------------------------------------
# Where the tokens go down, and who moves them afterwards
# --------------------------------------------------------------------------


def test_the_fugitive_may_only_be_hidden_in_the_enemy_back_row():
    state = make_state()
    obj = setup_objective(state, "Fugitive", owner=0, tiles=[Pos(9, 9)])
    tiles = O.placement_tiles(state, obj)
    assert tiles, "there is somewhere to put it"
    assert {p.y for p in tiles} == {0}, "seat 0 hides it on seat 1's own row"


def test_the_gangs_go_anywhere_off_their_creators_back_row_of_cards():
    """"outside their back row of cards" -- a card row is four tiles deep."""
    state = make_state()
    obj = setup_objective(state, "Riverside", owner=0)
    rows = {p.y for p in O.placement_tiles(state, obj)}
    # Created by seat 1, whose own back row is the top four tile rows.
    assert rows == set(range(4, state.board.height))


def test_the_refugees_go_in_the_enemy_half():
    state = make_state()
    obj = setup_objective(state, "Car Park", owner=0)
    rows = {p.y for p in O.placement_tiles(state, obj)}
    # Created by seat 0 (the bottom), so "the enemy half" is the top half.
    assert rows == set(range(state.board.height // 2))


def test_a_tile_with_a_frame_or_a_token_on_it_is_not_offered():
    state = make_state()
    obj = setup_objective(state, "Car Park", owner=0)
    add_frame(state, 1, "Adam", Pos(3, 2))
    taken = O.tokens_of(state, obj)[0]
    O.place_token(state, taken, Pos(4, 2))
    tiles = O.placement_tiles(state, obj)
    assert Pos(3, 2) not in tiles and Pos(4, 2) not in tiles


def test_setup_asks_the_creator_where_each_token_goes():
    state = make_state()
    obj = setup_objective(state, "Riverside", owner=0)
    decision = O.setup_decision(state)
    assert decision is not None
    assert decision.kind == "place_objective"
    assert decision.seat == 1, "the side that creates them places them"
    assert decision.pick_kind == "place"
    assert decision.pick_min == decision.pick_max == 3, "all three at once"
    assert "3 left" in decision.prompt

    token = state.tokens[str(decision.options[0]["token"])]
    O.place_token(state, token, Pos(5, 5))
    again = O.setup_decision(state)
    assert again is not None and again.pick_max == 2, "two still to place"
    assert str(again.options[0]["token"]) != token.id


def test_the_bomb_carrier_is_chosen_by_the_attacker():
    state = make_state()
    obj = setup_objective(state, "Dome Campus", owner=0, tiles=[Pos(2, 2)])
    add_frame(state, 0, "Kuwagata", Pos(5, 9))
    theirs = [add_frame(state, 1, "Adam", Pos(1, 0)),
              add_frame(state, 1, "Fenrir", Pos(2, 0))]
    decision = O.setup_decision(state)
    assert decision is not None and decision.kind == "choose_frame"
    assert decision.seat == 1
    assert {o["frame"] for o in decision.options} == {f.id for f in theirs}

    O.set_bomb_carrier(state, obj, theirs[0].id)
    assert O.setup_decision(state) is None, "asked once"


def test_a_gang_moves_at_its_own_initiative_and_only_once_a_turn():
    state = make_state()
    state.turn = 2
    obj = setup_objective(state, "Riverside", owner=0)
    token = O.tokens_of(state, obj)[0]
    O.place_token(state, token, Pos(5, 5))

    assert O.token_decision(state, 4) is None, "cards at 4 go first"
    decision = O.token_decision(state, 1)
    assert decision is not None
    assert decision.kind == "move_token" and decision.seat == 1
    assert decision.pick_kind == "move"
    spots = {(o["x"], o["y"]) for o in decision.options}
    assert (5, 5) in spots and (6, 6) in spots, "one tile in any direction"
    assert (7, 5) not in spots, "and no further"

    O.move_token(state, token, Pos(6, 6))
    assert token.pos == Pos(6, 6)
    assert O.token_decision(state, 1) is None, "it has had its move this turn"
    state.turn = 3
    assert O.token_decision(state, 1) is not None, "and gets another next turn"


def test_a_token_that_cannot_move_is_never_asked_about():
    state = make_state()
    obj = setup_objective(state, "Shiny Thing", owner=0, spawns=[Pos(4, 4)])
    assert O.tokens_of(state, obj)[0].movement == 0
    assert O.mobile_tokens(state) == []
    assert O.token_decision(state, None) is None


def test_the_solar_farm_says_what_each_side_has_banked():
    """A count nobody can read off the board goes in the log and the view."""
    from playtest.engine import objectives as O
    from playtest.engine.serialize import view_for

    state = make_state(width=12, height=12)
    obj = O.ObjectiveState(
        name="Solar Farm", owner=0, defend=2, attack=2,
        tiles=[Pos(4, 4), Pos(5, 4)],
    )
    state.objectives.append(obj)
    mine = add_frame(state, 0, "Kamikiri", Pos(4, 4))
    add_frame(state, 0, "Hector MkI", Pos(5, 4))
    theirs = add_frame(state, 1, "Fenrir", Pos(9, 9))

    O.end_of_turn(state)
    assert O.tally(state, obj) == ("charge", {0: 2, 1: 0})
    assert any("banks 2 charge" in e["text"] for e in state.log), (
        "the banking has to be in the log: it cannot be read off the board"
    )

    # A side that walks away keeps what it banked; the other side starts adding.
    for frame in state.frames.values():
        frame.pos = Pos(0, 0) if frame.seat == 0 else Pos(5, 4)
    O.end_of_turn(state)
    assert O.tally(state, obj) == ("charge", {0: 2, 1: 1})

    seen = next(o for o in view_for(state, 0)["board"]["objectives"]
                if o["name"] == "Solar Farm")
    assert seen["tallyLabel"] == "charge"
    assert seen["tally"] == {"0": 2, "1": 1}
