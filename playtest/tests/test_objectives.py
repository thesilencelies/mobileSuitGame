"""The eight scripted objectives, their scoring and their latching rules.

Scoring timing is an engine assumption (SPEC.md): Power Reactors, The Tower,
Fugitive and The Egg *latch* the moment their condition is met; Shiny Thing,
Triangle, Holo Spires and Church are settled once, after turn 5.
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
from playtest.engine.types import Pos

from ._helpers import add_frame, give, make_state, run_attack

# Points as printed on the terrain cards (Terrain_square.csv).
POINTS = {
    "Power Reactors": (2, 2), "Shiny Thing": (1, 2), "Triangle": (1, 1),
    "Fugitive": (2, 1), "Holo Spires": (1, 1), "Church": (1, 1),
    "The Tower": (2, 2), "The Egg": (1, 2),
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


def test_all_eight_objectives_have_a_scorer():
    assert len(OBJECTIVE_NAMES) == 8
    assert set(O.SCORERS) == set(OBJECTIVE_NAMES)
    assert "Helpcard" not in OBJECTIVE_NAMES, "the Helpcard is a legend, not real"


def test_only_four_objectives_latch():
    assert LATCHING == {"Power Reactors", "The Tower", "Fugitive", "The Egg"}


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


def test_the_fugitive_is_towed_by_an_adjacent_ally_of_the_defender():
    state = make_state()
    obj = setup_objective(state, "Fugitive", owner=0, tiles=[Pos(9, 9)],
                          spawns=[Pos(2, 2)])
    token = O.tokens_of(state, obj)[0]
    escort = add_frame(state, 0, "Kuwagata", Pos(3, 2))
    O.on_move(state, escort, Pos(3, 2))
    assert token.carrier == escort.id, "adjacent is enough to take it in tow"

    escort.pos = Pos(5, 5)
    O.on_move(state, escort, Pos(3, 2))
    assert token.pos == Pos(5, 5)


def test_the_enemy_cannot_tow_the_fugitive():
    state = make_state()
    obj = setup_objective(state, "Fugitive", owner=0, tiles=[Pos(9, 9)],
                          spawns=[Pos(2, 2)])
    token = O.tokens_of(state, obj)[0]
    enemy = add_frame(state, 1, "Adam", Pos(2, 3))
    O.on_move(state, enemy, Pos(2, 3))
    assert token.carrier is None


def test_the_fugitive_latches_for_the_defender_at_the_objective_point():
    state = make_state()
    obj = setup_objective(state, "Fugitive", owner=0, tiles=[Pos(9, 9)],
                          spawns=[Pos(8, 8)])
    token = O.tokens_of(state, obj)[0]
    escort = add_frame(state, 0, "Kuwagata", Pos(8, 9))
    O.on_move(state, escort, Pos(8, 9))
    escort.pos = Pos(9, 9)
    O.on_move(state, escort, Pos(8, 9))
    assert token.pos == Pos(9, 9)
    assert obj.latched == 0
    assert objective_score(state, obj) == (0, obj.defend)


def test_a_fugitive_that_never_arrives_scores_for_the_attacker():
    state = make_state()
    obj = setup_objective(state, "Fugitive", owner=0, tiles=[Pos(9, 9)],
                          spawns=[Pos(2, 2)])
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
