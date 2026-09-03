"""Attack resolution: range, elevation shift, compulsory blocking, damage.

Every trap from SPEC.md "Rules subtleties that are easy to get wrong" that
belongs to the attack pipeline has a test here.
"""

from __future__ import annotations

import pytest

from playtest.engine import combat
from playtest.engine.combat import (
    attack_zones_against,
    block_options,
    can_target,
    elevation_shift,
    legal_targets,
    next_block_decision,
    remaining_cards,
    zones_in_range,
)
from playtest.engine.types import Pos, ZONES

from ._helpers import CATALOGUE, add_frame, give, make_state, run_attack


def _duel(attacker_frame="Kuwagata", defender_frame="Kuwagata",
          a=Pos(1, 1), b=Pos(2, 1), seed=0):
    state = make_state(seed=seed)
    atk = add_frame(state, 0, attacker_frame, a)
    dfn = add_frame(state, 1, defender_frame, b)
    return state, atk, dfn


# --------------------------------------------------------------------------
# Elevation shift -- the two worked examples (rules.tex:566-568)
# --------------------------------------------------------------------------


def test_elevation_shift_worked_example_attacker_lower():
    """"Frame A at elevation 1 attacks Frame B at elevation 3 with cleave,
    which has 2 high and 2 mid. [...] the attacks are moved 2 stages down.
    The 2 high end up 2 low, and the 2 mid are out of range." """
    cleave = CATALOGUE["Greatsword_Cleave"]
    assert cleave.attacks["High"] == 2 and cleave.attacks["Mid"] == 2

    shifted = elevation_shift({"High": 2, "Mid": 2}, 1 - 3)
    assert shifted == {"Low": 2}, "High -> Low, Mid pushed below Low is lost"


def test_elevation_shift_worked_example_attacker_higher():
    """"if Frame C at elevation 2 were to attack Frame A [at elevation 1]
    with Thrust (pierce mid), it would be moved one stage up, resulting in
    pierce high. This would be blocked by a high block, not a mid block." """
    thrust = CATALOGUE["Spear_Thrust"]
    assert thrust.attacks["Mid"] == 1 and thrust.attacks["High"] == 0

    shifted = elevation_shift({"Mid": 1}, 2 - 1)
    assert shifted == {"High": 1}


def test_elevation_shift_end_to_end_and_the_block_zone_moves_with_it():
    state, atk, dfn = _duel()
    state.board.set_tile(atk.pos, elevation=2)
    state.board.set_tile(dfn.pos, elevation=1)
    uid = give(state, atk, "Spear_Thrust")
    zones = attack_zones_against(
        state, atk, CATALOGUE["Spear_Thrust"], dfn.pos, dfn
    )
    assert zones == {"High": 1}

    # A Mid block no longer helps; only a High block does.
    mid_only = give(state, dfn, "Basic_Punch")          # blocks Mid
    attack = combat.declare_attack(
        state, atk, uid, target_kind="frame", target_id=dfn.id
    )
    assert next_block_decision(state, attack) is None
    assert block_options(state, dfn, attack, ["High"]) == []
    give(state, dfn, "Sword_Slice")                     # blocks High and Low
    assert len(block_options(state, dfn, attack, ["High"])) == 1


def test_out_of_range_shift_means_no_attack_at_all():
    state, atk, dfn = _duel()
    state.board.set_tile(dfn.pos, elevation=3)          # attacker 3 lower
    zones = attack_zones_against(
        state, atk, CATALOGUE["Spear_Thrust"], dfn.pos, dfn
    )
    assert zones == {}
    assert legal_targets(state, atk, CATALOGUE["Spear_Thrust"]) == []


def test_ranged_attacks_are_not_shifted_by_elevation():
    state, atk, dfn = _duel(b=Pos(6, 1))
    state.board.set_tile(atk.pos, elevation=3)
    card = CATALOGUE["Assault Rifle_Aimed Fire"]        # High 2, range 9
    zones = attack_zones_against(state, atk, card, dfn.pos, dfn)
    assert zones == {"High": 2}, "ranged attacks ignore elevation"


def test_zero_elevation_difference_leaves_the_attack_alone():
    assert elevation_shift({"High": 1, "Mid": 2, "Low": 3}, 0) == {
        "High": 1, "Mid": 2, "Low": 3
    }


def test_two_zones_can_shift_onto_the_same_zone():
    assert elevation_shift({"High": 1, "Mid": 2}, -1) == {"Mid": 1, "Low": 2}
    assert elevation_shift({"Mid": 1, "Low": 2}, -1) == {"Low": 1}


# --------------------------------------------------------------------------
# Range and targeting
# --------------------------------------------------------------------------


def test_melee_needs_an_adjacent_target():
    state, atk, dfn = _duel(b=Pos(3, 1))
    card = CATALOGUE["Spear_Thrust"]
    assert not can_target(state, atk, card, dfn.pos)
    dfn.pos = Pos(2, 2)                                  # diagonals are adjacent
    assert can_target(state, atk, card, dfn.pos)


def test_ranged_attacks_may_not_target_an_adjacent_frame():
    state, atk, dfn = _duel(b=Pos(2, 1))
    card = CATALOGUE["Assault Rifle_Aimed Fire"]
    assert not can_target(state, atk, card, dfn.pos, dfn)
    dfn.pos = Pos(4, 1)
    assert can_target(state, atk, card, dfn.pos, dfn)


def test_only_zones_actually_in_range_count_on_a_multi_range_attack():
    """`Stun Bow_Rapid fire` is High at range 6 and Mid at range 10."""
    card = CATALOGUE["Stun Bow_Rapid fire"]
    assert card.ranges["High"] == 6 and card.ranges["Mid"] == 10
    state, atk, dfn = _duel(a=Pos(0, 0), b=Pos(8, 0))
    assert zones_in_range(state, atk, card, dfn.pos, dfn) == {"Mid": 1}
    dfn.pos = Pos(5, 0)
    assert zones_in_range(state, atk, card, dfn.pos, dfn) == {"High": 1, "Mid": 1}


def test_line_of_sight_is_asked_of_the_board_and_respected():
    state, atk, dfn = _duel(b=Pos(5, 1))
    card = CATALOGUE["Assault Rifle_Aimed Fire"]
    assert can_target(state, atk, card, dfn.pos, dfn)
    state.board.los = False
    assert not can_target(state, atk, card, dfn.pos, dfn)


def test_allies_and_own_tokens_are_never_targets():
    state, atk, dfn = _duel()
    ally = add_frame(state, 0, "Adam", Pos(1, 2))
    options = legal_targets(state, atk, CATALOGUE["Spear_Thrust"])
    assert [o["id"] for o in options] == [dfn.id]
    assert ally.id not in [o["id"] for o in options]


# --------------------------------------------------------------------------
# Compulsory blocking (rules.tex:551)
# --------------------------------------------------------------------------


def test_blocking_is_compulsory_when_any_remaining_card_matches():
    state, atk, dfn = _duel()
    uid = give(state, atk, "Chainsaw_Disembowel")        # Mid 4
    give(state, dfn, "Basic_Punch")                      # blocks Mid
    attack = combat.declare_attack(
        state, atk, uid, target_kind="frame", target_id=dfn.id
    )
    decision = next_block_decision(state, attack)
    assert decision is not None, "a matching block makes blocking compulsory"
    zones, candidates = decision
    assert zones == ["Mid"] and len(candidates) == 1


def test_one_matching_zone_stops_the_whole_attack():
    """`Greatsword_Cleave` hits High 2 and Mid 2; a High block stops both."""
    state, atk, dfn = _duel()
    uid = give(state, atk, "Greatsword_Cleave")
    give(state, dfn, "Sword_Slice")                      # blocks High and Low
    run_attack(state, atk, uid, dfn)
    assert dfn.damage == {"High": 0, "Mid": 0, "Low": 0}


def test_an_unblocked_attack_lands_for_one_damage_per_mark():
    state, atk, dfn = _duel()
    uid = give(state, atk, "Greatsword_Cleave")
    give(state, dfn, "Basic_Kick")                       # blocks Low only
    run_attack(state, atk, uid, dfn)
    assert dfn.damage["High"] == 2 and dfn.damage["Mid"] == 2


def test_the_blocking_card_is_discarded():
    state, atk, dfn = _duel()
    uid = give(state, atk, "Chainsaw_Disembowel")
    blocker = give(state, dfn, "Basic_Punch")
    run_attack(state, atk, uid, dfn)
    assert state.cards[blocker].location == "discard"
    assert blocker in dfn.discard and blocker not in dfn.committed


def test_an_unresolved_blocker_forfeits_its_own_action():
    """"if the blocking card has not yet resolved it's action is forfeit"."""
    state, atk, dfn = _duel()
    uid = give(state, atk, "Chainsaw_Disembowel")
    blocker = give(state, dfn, "Basic_Punch")            # face down, unresolved
    assert state.cards[blocker].resolved is False
    run_attack(state, atk, uid, dfn)
    assert state.cards[blocker].location == "discard"
    assert blocker not in remaining_cards(state, dfn), "it can never act now"


def test_a_super_block_stops_the_attack_and_is_kept():
    """`Sword_Parry` has a Mid block of 2 -- it blocks and stays on the field."""
    state, atk, dfn = _duel()
    assert CATALOGUE["Sword_Parry"].blocks["Mid"] == 2
    uid = give(state, atk, "Chainsaw_Disembowel")
    blocker = give(state, dfn, "Sword_Parry")
    run_attack(state, atk, uid, dfn)
    assert dfn.damage["Mid"] == 0
    assert state.cards[blocker].location == "committed"
    assert blocker in remaining_cards(state, dfn), "a super block can block again"


def test_already_resolved_cards_still_block():
    """"Remaining" includes cards that have already taken their action."""
    state, atk, dfn = _duel()
    uid = give(state, atk, "Chainsaw_Disembowel")
    spent = give(state, dfn, "Basic_Punch", resolved=True, face_down=False)
    attack = combat.declare_attack(
        state, atk, uid, target_kind="frame", target_id=dfn.id
    )
    zones, candidates = next_block_decision(state, attack)
    assert candidates == [spent]


def test_persistent_set_aside_cards_neither_resolve_nor_block():
    state, atk, dfn = _duel()
    uid = give(state, atk, "Chainsaw_Disembowel")
    give(state, dfn, "Basic_Punch", location="aside", resolved=True)
    attack = combat.declare_attack(
        state, atk, uid, target_kind="frame", target_id=dfn.id
    )
    assert next_block_decision(state, attack) is None


def test_close_quarters_cannot_be_blocked_by_a_resolved_card():
    state, atk, dfn = _duel()
    assert "closequarters" in CATALOGUE["Knife_Cut"].keywords
    uid = give(state, atk, "Knife_Cut")                  # Mid 1, close quarters
    give(state, dfn, "Basic_Punch", resolved=True, face_down=False)
    attack = combat.declare_attack(
        state, atk, uid, target_kind="frame", target_id=dfn.id
    )
    assert next_block_decision(state, attack) is None
    run_attack(state, atk, uid, dfn)
    assert dfn.damage["Mid"] == 1


def test_close_quarters_is_still_blocked_by_a_face_down_card():
    state, atk, dfn = _duel()
    uid = give(state, atk, "Knife_Cut")
    give(state, dfn, "Basic_Punch")                      # unresolved
    run_attack(state, atk, uid, dfn)
    assert dfn.damage["Mid"] == 0


# --------------------------------------------------------------------------
# Guard Break and Feint
# --------------------------------------------------------------------------


def _gunner_duel(distance=5):
    """A ranged duel, for the three-zone Guard Break attacks."""
    return _duel(a=Pos(1, 1), b=Pos(1 + distance, 1))


def test_guard_break_needs_a_block_for_each_zone():
    """`Great Axe_Split` has Guard Break with Mid 2 and Low 2.

    Neither of these one-zone cards spans both attacked zones, so both are
    spent -- one block per zone.
    """
    card = CATALOGUE["Great Axe_Split"]
    assert "guardbreak" in card.keywords
    state, atk, dfn = _duel()
    uid = give(state, atk, "Great Axe_Split")
    mid = give(state, dfn, "Basic_Punch")                # Mid
    low = give(state, dfn, "Basic_Kick")                 # Low
    run_attack(state, atk, uid, dfn)
    assert dfn.damage == {"High": 0, "Mid": 0, "Low": 0}
    assert state.cards[mid].location == "discard"
    assert state.cards[low].location == "discard", "both blocks are eaten"


def test_guard_break_zones_without_a_block_still_land():
    state, atk, dfn = _duel()
    uid = give(state, atk, "Great Axe_Split")            # Mid 2, Low 2
    give(state, dfn, "Basic_Punch")                      # Mid only
    run_attack(state, atk, uid, dfn)
    assert dfn.damage["Mid"] == 0
    assert dfn.damage["Low"] == 2, "the unblocked zone deals its damage"


def test_one_card_blocks_every_guard_break_zone_it_covers():
    """"The same card can block multiple zones if it has them"
    (rules.tex:956). `Cannon_Airburst` breaks guard in all three zones; a
    single High+Mid card covers two of them by itself."""
    state, atk, dfn = _gunner_duel()
    airburst = CATALOGUE["Cannon_Airburst"]
    blocker_card = CATALOGUE["Greatsword_Rising Cut"]    # blocks High 1, Mid 1
    assert blocker_card.block_zones == {"High", "Mid"}
    assert not blocker_card.super_block_zones

    uid = give(state, atk, "Cannon_Airburst")
    blocker = give(state, dfn, "Greatsword_Rising Cut")
    run_attack(state, atk, uid, dfn)

    assert dfn.damage["High"] == 0 and dfn.damage["Mid"] == 0
    assert dfn.damage["Low"] == airburst.attacks["Low"], (
        "only the uncovered zone gets through"
    )
    assert state.cards[blocker].location == "discard"


def test_a_card_spanning_every_zone_stops_a_guard_break_attack_alone():
    state, atk, dfn = _gunner_duel()
    uid = give(state, atk, "Cannon_Airburst")            # High/Mid/Low
    blocker = give(state, dfn, "Basic_Block")            # blocks all three
    spare = give(state, dfn, "Basic_Kick")
    run_attack(state, atk, uid, dfn)

    assert dfn.damage == {"High": 0, "Mid": 0, "Low": 0}
    assert state.cards[blocker].location == "discard"
    assert state.cards[spare].location == "committed", "spent once, not per zone"


def test_a_super_block_covers_several_guard_break_zones_and_is_still_kept():
    state, atk, dfn = _gunner_duel()
    blocker_card = CATALOGUE["Shield_Lock down"]         # High 2 (super), Mid 1
    assert blocker_card.block_zones == {"High", "Mid"}
    assert blocker_card.super_block_zones == {"High"}

    uid = give(state, atk, "Cannon_Airburst")
    blocker = give(state, dfn, "Shield_Lock down")
    run_attack(state, atk, uid, dfn)

    assert dfn.damage["High"] == 0 and dfn.damage["Mid"] == 0
    assert dfn.damage["Low"] == CATALOGUE["Cannon_Airburst"].attacks["Low"]
    assert state.cards[blocker].location == "committed", "a super block is kept"


def test_guard_break_keeps_asking_while_a_card_still_covers_an_open_zone():
    """The engine's reading of the multi-defender case.

    The rulebook does not say who blocks what when several cards overlap. This
    engine treats it as: blocking stays compulsory while *any* remaining card
    covers *any* still-unblocked zone, and the defender picks which card each
    time. So the order is a real decision -- covering wide first spends one
    card, covering narrow first can cost a second.
    """
    def play(prefer_wide):
        state, atk, dfn = _gunner_duel()
        uid = give(state, atk, "Cannon_Airburst")        # High/Mid/Low
        wide = give(state, dfn, "Basic_Block")           # all three
        narrow = give(state, dfn, "Basic_Kick")          # Low only

        def chooser(zones, candidates):
            want = wide if prefer_wide else narrow
            return want if want in candidates else candidates[0]

        run_attack(state, atk, uid, dfn, chooser=chooser)
        spent = [u for u in (wide, narrow)
                 if state.cards[u].location == "discard"]
        return dfn.damage, spent

    damage, spent = play(prefer_wide=True)
    assert damage == {"High": 0, "Mid": 0, "Low": 0}
    assert len(spent) == 1, "the wide card covers everything on its own"

    damage, spent = play(prefer_wide=False)
    assert damage == {"High": 0, "Mid": 0, "Low": 0}
    assert len(spent) == 2, "blocking Low first leaves High/Mid still compulsory"


def test_guard_break_is_the_only_difference_from_an_ordinary_attack():
    """Same blocker, same attacked zones -- only the keyword differs.

    Ordinary: one matching zone stops the whole attack. Guard Break: the card
    covers only the zones it blocks and the rest lands.
    """
    blocker_key = "Sword_Slice"                          # blocks High and Low
    assert CATALOGUE[blocker_key].block_zones == {"High", "Low"}

    # Ordinary attack on High and Mid.
    state, atk, dfn = _duel()
    assert "guardbreak" not in CATALOGUE["Greatsword_Cleave"].keywords
    give(state, dfn, blocker_key)
    run_attack(state, atk, give(state, atk, "Greatsword_Cleave"), dfn)
    assert dfn.damage == {"High": 0, "Mid": 0, "Low": 0}, "one zone stops it all"

    # Guard Break attack on High and Mid.
    state2, atk2, dfn2 = _gunner_duel()
    breaker = CATALOGUE["Missile Rack_Missile Rack 1"]   # High 2, Mid 2
    assert "guardbreak" in breaker.keywords
    give(state2, dfn2, blocker_key)
    run_attack(state2, atk2, give(state2, atk2, "Missile Rack_Missile Rack 1"),
               dfn2)
    assert dfn2.damage["High"] == 0, "the card's High block still covers High"
    assert dfn2.damage["Mid"] == 2, "Mid is not covered, so it lands"


def test_a_feint_deals_no_damage_but_still_forces_a_block():
    state, atk, dfn = _duel()
    assert "feint" in CATALOGUE["Sword_Feint"].keywords
    uid = give(state, atk, "Sword_Feint")                # High 1, Mid 1
    blocker = give(state, dfn, "Basic_Punch")
    attack = combat.declare_attack(
        state, atk, uid, target_kind="frame", target_id=dfn.id
    )
    assert next_block_decision(state, attack) is not None, "blocks are compulsory"

    state2, atk2, dfn2 = _duel()
    uid2 = give(state2, atk2, "Sword_Feint")
    run_attack(state2, atk2, uid2, dfn2)                 # nothing blocks
    assert dfn2.damage == {"High": 0, "Mid": 0, "Low": 0}


# --------------------------------------------------------------------------
# Damage, destruction and splash
# --------------------------------------------------------------------------


def test_damage_accumulates_and_destroys_at_armour():
    state, atk, dfn = _duel(defender_frame="Flamekin")   # armour 2/2/2
    dfn.damage["Mid"] = 1
    uid = give(state, atk, "Basic_Punch")
    run_attack(state, atk, uid, dfn)
    assert dfn.damage["Mid"] == 2
    assert dfn.deathstrike_until is not None, "Flamekin has Deathstrike"


def test_on_hit_status_only_lands_on_an_unblocked_attack():
    state, atk, dfn = _duel()
    uid = give(state, atk, "Stun Baton_Shock")           # On Hit: 2 stunned
    run_attack(state, atk, uid, dfn)
    assert dfn.statuses["stunned"] == 2

    state2, atk2, dfn2 = _duel()
    uid2 = give(state2, atk2, "Stun Baton_Shock")
    give(state2, dfn2, "Basic_Punch")                    # blocks Mid
    run_attack(state2, atk2, uid2, dfn2)
    assert dfn2.statuses["stunned"] == 0


def test_splash_text_hits_every_adjacent_enemy():
    state, atk, dfn = _duel()                            # attacker at (1,1)
    other = add_frame(state, 1, "Kuwagata", Pos(1, 2))
    uid = give(state, atk, "Great Axe_Whirl")            # hits all adjacent
    attack = combat.declare_attack(
        state, atk, uid, target_kind="frame", target_id=dfn.id
    )
    assert {t.id for t in attack.targets} == {dfn.id, other.id}


def test_adjacent_splash_does_not_care_what_the_attack_was_declared_at():
    """"Hits all adjacent enemies" measures from the attacker.

    So shooting the barricade in front of you still catches the frame beside
    you: what the swing was declared at never enters into it.
    """
    from playtest.engine.state import TokenState

    state, atk, _dfn = _duel(b=Pos(9, 9))               # attacker at (1,1)
    beside = add_frame(state, 1, "Kuwagata", Pos(1, 2))
    state.tokens["t0"] = TokenState(
        id="t0", kind="tower", pos=Pos(2, 1), hp=4, max_hp=4, owner=1
    )
    uid = give(state, atk, "Greatsword_Grand Sweep")
    attack = combat.declare_attack(
        state, atk, uid, target_kind="token", target_id="t0"
    )
    assert [(t.kind, t.id) for t in attack.targets] == [
        ("token", "t0"), ("frame", beside.id)
    ]


def test_splash_reaches_past_the_weapon_when_it_names_its_own_shape():
    """`Kinetic Hammer_Slam`: "Also hits any enemies adjacent to the target".

    The target is what the melee weapon could reach; the card then says the
    swing carries on into whatever is beside it. Measuring the weapon's own
    reach again would delete exactly the enemies the card was printed to
    catch -- the far side of the target is two tiles from the attacker.
    """
    from playtest.engine.state import TokenState

    slam = CATALOGUE["Kinetic Hammer_Slam"]
    assert not slam.is_ranged, "the whole point is that it reaches past melee"

    state, atk, dfn = _duel()                           # (1,1) hits (2,1)
    behind = add_frame(state, 1, "Kuwagata", Pos(3, 1))  # 2 from atk, 1 from dfn
    state.tokens["beside"] = TokenState(
        id="beside", kind="tower", pos=Pos(3, 2), hp=4, max_hp=4, owner=1
    )
    state.tokens["beyond"] = TokenState(
        id="beyond", kind="tower", pos=Pos(5, 1), hp=4, max_hp=4, owner=1
    )
    uid = give(state, atk, "Kinetic Hammer_Slam")
    attack = combat.declare_attack(
        state, atk, uid, target_kind="frame", target_id=dfn.id
    )
    assert {(t.kind, t.id) for t in attack.targets} == {
        ("frame", dfn.id), ("frame", behind.id), ("token", "beside")
    }, "everything beside the target, and nothing two tiles from it"
    for target in attack.targets:
        assert target.zones == {"Low": slam.attacks["Low"]}


def test_splash_past_the_weapon_is_still_shifted_by_elevation():
    """Reach is what the splash overrides -- the ground still counts."""
    state, atk, dfn = _duel()
    behind = add_frame(state, 1, "Kuwagata", Pos(3, 1))
    state.board.set_tile(behind.pos, elevation=1)
    state.board.set_tile(atk.pos, elevation=2)          # attacker 1 higher

    uid = give(state, atk, "Kinetic Hammer_Slam")       # Low 2
    attack = combat.declare_attack(
        state, atk, uid, target_kind="frame", target_id=dfn.id
    )
    caught = next(t for t in attack.targets if t.id == behind.id)
    assert caught.zones == {"Mid": 2}, "Low shifted up one toward High"


def test_adjacent_splash_catches_tokens_as_well_as_frames():
    """An enemy is anything the attack could have been aimed at.

    A barricade or a gun tower standing beside you is as much in the way of a
    wide swing as a mech is, so "adjacent enemies" is read as every adjacent
    thing this attacker may target -- the neutral pieces included, since they
    can be attacked too. Only the attacker's own tokens are spared.
    """
    from playtest.engine.state import TokenState

    state, atk, dfn = _duel()                           # attacker at (1,1)
    state.tokens["theirs"] = TokenState(
        id="theirs", kind="barricade", pos=Pos(2, 2), hp=2, max_hp=2, owner=1
    )
    state.tokens["neutral"] = TokenState(
        id="neutral", kind="tower", pos=Pos(1, 2), hp=4, max_hp=4, owner=None
    )
    state.tokens["mine"] = TokenState(
        id="mine", kind="barricade", pos=Pos(0, 1), hp=2, max_hp=2, owner=0
    )
    state.tokens["far"] = TokenState(
        id="far", kind="tower", pos=Pos(6, 6), hp=4, max_hp=4, owner=1
    )
    uid = give(state, atk, "Greatsword_Grand Sweep")
    attack = combat.declare_attack(
        state, atk, uid, target_kind="frame", target_id=dfn.id
    )
    assert {(t.kind, t.id) for t in attack.targets} == {
        ("frame", dfn.id), ("token", "theirs"), ("token", "neutral")
    }


def test_adjacent_splash_damages_the_tokens_it_catches():
    from playtest.engine.state import TokenState

    state, atk, dfn = _duel()
    sweep = CATALOGUE["Greatsword_Grand Sweep"]
    state.tokens["t0"] = TokenState(
        id="t0", kind="tower", pos=Pos(2, 2), hp=4, max_hp=4, owner=1
    )
    run_attack(state, atk, give(state, atk, "Greatsword_Grand Sweep"), dfn)
    assert state.tokens["t0"].hp == 4 - sum(sweep.attacks.values())


def test_hits_all_targets_in_range_catches_everything_it_could_have_declared():
    """`Chain_Tangle` prints "Hits all targets in range".

    Not a radius of its own: the card does not choose, so it hits everything
    the attack could have been declared against -- tokens as much as frames,
    the same enemies the adjacent sweeps catch.
    """
    from playtest.engine.state import TokenState

    tangle = CATALOGUE["Chain_Tangle"]
    assert tangle.ranges["Low"] == 3 and tangle.is_ranged

    state, atk, dfn = _duel(a=Pos(1, 1), b=Pos(4, 1))    # three apart
    near = add_frame(state, 1, "Kuwagata", Pos(1, 4))    # also three
    far = add_frame(state, 1, "Kuwagata", Pos(9, 9))     # out of range
    state.tokens["t0"] = TokenState(
        id="t0", kind="tower", pos=Pos(3, 3), hp=4, max_hp=4, owner=1
    )

    uid = give(state, atk, "Chain_Tangle")
    attack = combat.declare_attack(
        state, atk, uid, target_kind="frame", target_id=dfn.id
    )
    assert {t.id for t in attack.targets} == {dfn.id, near.id, "t0"}
    assert far.id not in {t.id for t in attack.targets}
    assert attack.targets[0].id == dfn.id, "the declared target is still first"


def test_hits_all_targets_in_range_does_not_double_up_on_the_declared_target():
    """Whichever of them was named, the card hits each of them once."""
    from playtest.engine.state import TokenState

    state, atk, dfn = _duel(a=Pos(1, 1), b=Pos(4, 1))
    state.tokens["t0"] = TokenState(
        id="t0", kind="tower", pos=Pos(3, 3), hp=4, max_hp=4, owner=1
    )
    uid = give(state, atk, "Chain_Tangle")
    attack = combat.declare_attack(
        state, atk, uid, target_kind="token", target_id="t0"
    )
    ids = [t.id for t in attack.targets]
    assert ids[0] == "t0", "the declared target is still first"
    assert sorted(ids) == sorted({dfn.id, "t0"})


def test_each_splash_target_gets_its_own_block_decision():
    state, atk, dfn = _duel()
    other = add_frame(state, 1, "Kuwagata", Pos(1, 2))
    whirl = CATALOGUE["Great Axe_Whirl"]                 # Low only
    uid = give(state, atk, "Great Axe_Whirl")
    give(state, dfn, "Basic_Kick")                       # dfn blocks Low, other cannot
    run_attack(state, atk, uid, dfn)
    assert dfn.damage["Low"] == 0
    assert other.damage["Low"] == whirl.attacks["Low"]


def test_tokens_are_attackable_and_never_block():
    from playtest.engine.state import TokenState

    state, atk, _ = _duel(b=Pos(9, 9))
    state.tokens["t0"] = TokenState(
        id="t0", kind="tower", pos=Pos(2, 1), hp=4, max_hp=4, owner=1
    )
    card = CATALOGUE["Chainsaw_Disembowel"]              # Mid 4, melee
    options = legal_targets(state, atk, card)
    assert {o["kind"] for o in options} == {"token"}

    uid = give(state, atk, "Chainsaw_Disembowel")
    attack = combat.declare_attack(
        state, atk, uid, target_kind="token", target_id="t0"
    )
    assert next_block_decision(state, attack) is None, "tokens never block"
    combat.finish_target(state, attack)
    assert not state.tokens["t0"].alive


# --------------------------------------------------------------------------
# Shields absorb the attack, not the zone
# --------------------------------------------------------------------------


def test_one_shield_counter_absorbs_a_whole_guard_break_attack():
    """"A shield takes the full brunt of damage across all zones even with
    guard break" -- the unit a counter cancels is the attack, not the zone."""
    state, atk, dfn = _duel(defender_frame="Hannael", a=Pos(1, 1), b=Pos(6, 1))
    dfn.shields = 1
    uid = give(state, atk, "Cannon_Airburst")            # High 2, Mid 2, Low 1
    run_attack(state, atk, uid, dfn)
    assert dfn.damage == {"High": 0, "Mid": 0, "Low": 0}
    assert dfn.shields == 0, "exactly one counter, not one per zone"


def test_a_second_shield_counter_survives_a_three_zone_attack():
    state, atk, dfn = _duel(defender_frame="Elemiah", a=Pos(1, 1), b=Pos(6, 1))
    assert dfn.shields == 2                              # Elemiah has Shield 2
    run_attack(state, atk, give(state, atk, "Cannon_Airburst"), dfn)
    assert dfn.shields == 1, "the three-zone attack cost one counter"
    assert dfn.damage == {"High": 0, "Mid": 0, "Low": 0}

    run_attack(state, atk, give(state, atk, "Cannon_Airburst"), dfn)
    assert dfn.shields == 0
    assert dfn.damage == {"High": 0, "Mid": 0, "Low": 0}

    run_attack(state, atk, give(state, atk, "Cannon_Airburst"), dfn)
    assert dfn.damage == dict(CATALOGUE["Cannon_Airburst"].attacks), (
        "no counters left"
    )


def test_an_ordinary_multi_zone_attack_also_costs_only_one_counter():
    """Not just Guard Break -- any attack landing in several zones."""
    state, atk, dfn = _duel(defender_frame="Hannael")
    plain = CATALOGUE["Greatsword_Cleave"]               # High 2, Mid 2
    assert "guardbreak" not in plain.keywords
    dfn.shields = 1
    run_attack(state, atk, give(state, atk, "Greatsword_Cleave"), dfn)
    assert dfn.damage == {"High": 0, "Mid": 0, "Low": 0}
    assert dfn.shields == 0


def test_a_shield_gained_mid_turn_absorbs_the_next_attack():
    from playtest.engine.state import add_shield

    state, atk, dfn = _duel(defender_frame="Hannael")
    dfn.shields = 0
    run_attack(state, atk, give(state, atk, "Greatsword_Cleave"), dfn)
    assert dfn.damage["High"] == 2, "no shield, so it lands"

    add_shield(state, dfn, 1)
    run_attack(state, atk, give(state, atk, "Greatsword_Cleave"), dfn)
    assert dfn.damage["High"] == 2, "the new counter absorbs the next attack"
    assert dfn.shields == 0


def test_a_shielded_attack_does_not_destroy_or_trigger_deathstrike():
    state, atk, dfn = _duel(defender_frame="Flamekin")   # armour 2/2/2
    dfn.shields = 1
    dfn.damage["Mid"] = 1
    run_attack(state, atk, give(state, atk, "Chainsaw_Disembowel"), dfn)  # Mid 4
    assert dfn.damage["Mid"] == 1, "absorbed"
    assert dfn.alive and dfn.deathstrike_until is None


def test_a_shielded_attack_does_not_drop_the_shiny_thing():
    """No damage was taken, so there is nothing to drop it for."""
    from playtest.engine import objectives as O

    state, atk, dfn = _duel(defender_frame="Hannael")
    obj = O.create_objective(state, "Shiny Thing", 0, defend=1, attack=2,
                             spawns=[dfn.pos])
    token = O.tokens_of(state, obj)[0]
    O.on_move(state, dfn, dfn.pos)
    assert token.carrier == dfn.id
    dfn.shields = 1
    run_attack(state, atk, give(state, atk, "Greatsword_Cleave"), dfn)
    assert token.carrier == dfn.id, "the shield took the brunt, so no drop"


def test_a_single_zone_counter_attack_still_costs_a_counter():
    """`On Block:` damage is its own attack, so it spends its own counter."""
    from playtest.engine.state import deal_damage

    state, atk, dfn = _duel(defender_frame="Hannael")
    dfn.shields = 1
    assert deal_damage(state, dfn, "Mid", 1) == 0
    assert dfn.shields == 0
    assert deal_damage(state, dfn, "Mid", 1) == 1
