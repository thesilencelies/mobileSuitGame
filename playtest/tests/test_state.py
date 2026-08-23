"""GameState: durability, statuses, shields, piles, cloning and VP."""

from __future__ import annotations

import pytest

from playtest.engine.state import (
    ObjectiveState,
    TokenState,
    add_shield,
    apply_status,
    check_destruction,
    damage_token,
    deal_damage,
    destroy_frame,
    discard_card,
    draw,
    move_card,
    repair,
    reshuffle,
    tick_statuses,
    victory_points,
    zone_at_last_hit,
    zone_destroyed,
)
from playtest.engine.types import (
    ARMOUR_KILLS_AT,
    BASE_DRAW,
    STATUS_MAGNITUDE,
    Pos,
    ZONES,
)

from ._helpers import add_frame, give, make_state


# --------------------------------------------------------------------------
# Durability -- the open rules question (SPEC.md), behind ARMOUR_KILLS_AT
# --------------------------------------------------------------------------


def test_durability_follows_the_worked_example_at_rules_591():
    """A Kuwagata (armour 4/4/4) on 3/3/1 draws 6 and is at -1 initiative,
    and *one more hit* High or Mid destroys it."""
    assert ARMOUR_KILLS_AT == "kill_at_armour"
    state = make_state()
    frame = add_frame(state, 0, "Kuwagata", Pos(1, 1))
    frame.damage.update({"High": 3, "Mid": 3, "Low": 1})

    assert frame.zone_last_hit("High") and frame.zone_last_hit("Mid")
    assert not frame.zone_last_hit("Low")
    assert frame.draw_count == BASE_DRAW - 1 == 6
    assert frame.initiative_mod == -1
    assert not frame.is_destroyed

    deal_damage(state, frame, "High", 1)
    assert frame.is_destroyed is False or not frame.alive
    assert not frame.alive, "damage == armour destroys (rules.tex:591)"


def test_an_armour_four_zone_takes_three_hits_and_dies_on_the_fourth():
    """Asserted as literal behaviour, not read off ARMOUR_KILLS_AT."""
    state = make_state()
    frame = add_frame(state, 0, "Kuwagata", Pos(1, 1))   # armour 4/4/4
    for hit in range(1, 4):
        deal_damage(state, frame, "Mid", 1)
        assert frame.alive, f"hit {hit} of 4 should not be lethal"
    assert frame.zone_last_hit("Mid"), "at 3 damage it is one hit from death"
    deal_damage(state, frame, "Mid", 1)
    assert not frame.alive, "the fourth hit destroys it"


def test_zone_helpers_at_the_boundary():
    assert not zone_destroyed(3, 4)
    assert zone_destroyed(4, 4)
    assert zone_destroyed(5, 4)
    assert zone_at_last_hit(3, 4)
    assert not zone_at_last_hit(2, 4)
    assert not zone_at_last_hit(4, 4)


def test_low_zone_last_hit_costs_a_point_of_movement():
    state = make_state()
    frame = add_frame(state, 0, "Kuwagata", Pos(1, 1))
    assert frame.base_movement == 4
    frame.damage["Low"] = 3
    assert frame.base_movement == 3


# --------------------------------------------------------------------------
# Statuses
# --------------------------------------------------------------------------


def test_opposite_statuses_annihilate_leaving_the_difference():
    state = make_state()
    frame = add_frame(state, 0, "Kuwagata", Pos(1, 1))
    apply_status(state, frame, "slowed", 1)
    apply_status(state, frame, "boosted", 2)
    assert frame.statuses["slowed"] == 0
    assert frame.statuses["boosted"] == 1


def test_status_magnitude_is_fixed_however_deep_the_stack():
    state = make_state()
    frame = add_frame(state, 0, "Kuwagata", Pos(1, 1))
    apply_status(state, frame, "boosted", 1)
    one = frame.base_movement
    apply_status(state, frame, "boosted", 5)
    assert frame.statuses["boosted"] == 6
    assert frame.base_movement == one, "counters are duration, not magnitude"
    assert one == frame.spec.movement + STATUS_MAGNITUDE


def test_statuses_push_initiative_cards_and_movement():
    state = make_state()
    frame = add_frame(state, 0, "Kuwagata", Pos(1, 1))
    apply_status(state, frame, "stunned", 1)
    apply_status(state, frame, "dazed", 1)
    apply_status(state, frame, "slowed", 1)
    assert frame.initiative_mod == -STATUS_MAGNITUDE
    assert frame.draw_count == BASE_DRAW - STATUS_MAGNITUDE
    assert frame.base_movement == frame.spec.movement - STATUS_MAGNITUDE


def test_tick_removes_exactly_one_counter_of_each_type():
    state = make_state()
    frame = add_frame(state, 0, "Kuwagata", Pos(1, 1))
    apply_status(state, frame, "stunned", 3)
    apply_status(state, frame, "revealed", 1)
    tick_statuses(frame)
    assert frame.statuses["stunned"] == 2
    assert frame.statuses["revealed"] == 0
    tick_statuses(frame)
    assert frame.statuses["stunned"] == 1
    assert frame.statuses["revealed"] == 0, "never goes negative"


# --------------------------------------------------------------------------
# Shields
# --------------------------------------------------------------------------


def test_a_shield_counter_absorbs_a_whole_damage_instance():
    state = make_state()
    frame = add_frame(state, 0, "Elemiah", Pos(1, 1))
    assert frame.shields == 2
    assert deal_damage(state, frame, "Mid", 3) == 0
    assert frame.shields == 1 and frame.damage["Mid"] == 0
    assert deal_damage(state, frame, "Mid", 1) == 0
    assert frame.shields == 0
    assert deal_damage(state, frame, "Mid", 1) == 1
    assert frame.damage["Mid"] == 1


def test_shield_counters_cap_at_one_above_the_initial_value():
    state = make_state()
    frame = add_frame(state, 0, "Hannael", Pos(1, 1))   # Shield 1
    add_shield(state, frame, 5)
    assert frame.shields == 2


# --------------------------------------------------------------------------
# Repair and destruction
# --------------------------------------------------------------------------


def test_repair_takes_the_most_damaged_zone_each_point():
    """The rules never say how "Repair N" is allocated across zones; the
    engine always takes the zone closest to destroying the frame."""
    state = make_state()
    frame = add_frame(state, 0, "Kuwagata", Pos(1, 1))
    frame.damage.update({"High": 3, "Mid": 1, "Low": 0})
    repair(state, frame, 2)
    assert frame.damage == {"High": 1, "Mid": 1, "Low": 0}


def test_repair_stops_when_there_is_nothing_left_to_fix():
    state = make_state()
    frame = add_frame(state, 0, "Kuwagata", Pos(1, 1))
    frame.damage["Mid"] = 1
    repair(state, frame, 3)
    assert frame.damage == {"High": 0, "Mid": 0, "Low": 0}


def test_destroying_a_frame_scores_a_kill_and_clears_its_cards():
    state = make_state()
    victim = add_frame(state, 1, "Kuwagata", Pos(1, 1))
    killer = add_frame(state, 0, "Adam", Pos(2, 2))
    give(state, victim, "Basic_Block")
    victim.damage["Mid"] = 4
    check_destruction(state, victim, killer=killer)
    assert not victim.alive and victim.pos is None
    assert victim.committed == [] and len(victim.discard) == 1
    assert state.kills[0] == 1
    assert victory_points(state)[0] == 1


def test_deathstrike_frames_keep_fighting_when_destroyed():
    state = make_state()
    frame = add_frame(state, 0, "Flamekin", Pos(1, 1))
    frame.damage["Mid"] = 2                      # armour 2 -> destroyed
    assert check_destruction(state, frame) is False
    assert frame.alive and frame.deathstrike_until == state.turn + 1


def test_repairing_a_deathstruck_frame_saves_it():
    state = make_state()
    frame = add_frame(state, 0, "Flamekin", Pos(1, 1))
    frame.damage["Mid"] = 2
    check_destruction(state, frame)
    repair(state, frame, 1)
    assert frame.deathstrike_until is None and frame.alive


# --------------------------------------------------------------------------
# Piles
# --------------------------------------------------------------------------


def test_drawing_from_an_empty_deck_reshuffles_the_discard_pile():
    state = make_state(seed=3)
    frame = add_frame(state, 0, "Kuwagata", Pos(1, 1))
    for _ in range(4):
        give(state, frame, "Basic_Punch", location="discard")
    assert frame.deck == []
    drawn = draw(state, frame, 3)
    assert len(drawn) == 3
    assert len(frame.hand) == 3 and len(frame.deck) == 1 and frame.discard == []


def test_draw_stops_when_there_is_nothing_left_anywhere():
    state = make_state()
    frame = add_frame(state, 0, "Kuwagata", Pos(1, 1))
    give(state, frame, "Basic_Punch", location="deck")
    assert len(draw(state, frame, 7)) == 1


def test_move_card_finds_the_card_wherever_it_is():
    """An Echo sits in a *surviving* frame's row but belongs to the dead
    frame's deck, so pile moves must search every frame."""
    state = make_state()
    dead = add_frame(state, 0, "Kuwagata", Pos(1, 1))
    host = add_frame(state, 0, "Adam", Pos(2, 2))
    uid = give(state, dead, "Basic_Block", location="deck")
    dead.deck.remove(uid)
    host.committed.append(uid)
    state.cards[uid].location = "committed"
    state.cards[uid].is_echo = True

    discard_card(state, uid)
    assert uid not in host.committed
    assert uid in dead.discard, "an echo returns to its own frame's discard"


# --------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------


def test_tokens_take_damage_from_any_zone_the_same_way():
    state = make_state()
    token = TokenState(id="t0", kind="tower", pos=Pos(3, 3), hp=4, max_hp=4)
    state.tokens["t0"] = token
    damage_token(state, token, 3)
    assert token.alive and token.hp == 1
    damage_token(state, token, 2)
    assert not token.alive and token.hp == 0


def test_tokens_without_a_health_stat_are_not_attackable():
    shiny = TokenState(id="t1", kind="shiny", pos=Pos(1, 1), hp=0, max_hp=0)
    assert not shiny.attackable


# --------------------------------------------------------------------------
# Cloning and seat order
# --------------------------------------------------------------------------


def test_clone_is_deep_but_shares_board_and_catalogue():
    state = make_state()
    frame = add_frame(state, 0, "Kuwagata", Pos(1, 1))
    give(state, frame, "Basic_Block")
    twin = state.clone()

    assert twin.board is state.board, "the board is immutable and shared"
    assert twin.catalogue is state.catalogue
    twin.frames[frame.id].damage["Mid"] = 3
    twin.log.append({"turn": 1, "text": "x"})
    assert state.frames[frame.id].damage["Mid"] == 0
    assert state.log == []


def test_cloning_carries_the_rng_position():
    state = make_state(seed=11)
    first = [state.rng.random() for _ in range(3)]
    state2 = make_state(seed=11)
    twin = state2.clone()
    assert [twin.rng.random() for _ in range(3)] == first


def test_priority_marker_moves_one_step_anticlockwise():
    state = make_state()
    assert state.priority == 0
    state.rotate_priority()
    assert state.priority == 1
    state.rotate_priority()
    assert state.priority == 0


def test_seat_cycle_starts_at_the_priority_marker():
    state = make_state()
    assert state.seat_cycle() == (0, 1)
    state.priority = 1
    assert state.seat_cycle() == (1, 0)


def test_occupied_includes_frames_and_barricades_only():
    state = make_state()
    add_frame(state, 0, "Kuwagata", Pos(1, 1))
    state.tokens["t"] = TokenState(id="t", kind="shiny", pos=Pos(5, 5))
    state.tokens["b"] = TokenState(id="b", kind="barricade", pos=Pos(6, 6))
    assert state.occupied() == frozenset({Pos(1, 1), Pos(6, 6)})
