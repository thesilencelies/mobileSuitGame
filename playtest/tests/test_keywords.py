"""Keyword behaviours and all twelve frame abilities from `Frames.csv`."""

from __future__ import annotations

import pytest

from playtest.engine import combat
from playtest.engine import resolve as R
from playtest.engine import keywords as kw
from playtest.engine.keywords import FRAME_ABILITIES
from playtest.engine.types import Pos

from ._helpers import CATALOGUE, FRAMES, add_frame, give, make_state, run_attack


def _duel(attacker="Kuwagata", defender="Kuwagata", a=Pos(2, 2), b=Pos(3, 2)):
    state = make_state()
    return state, add_frame(state, 0, attacker, a), add_frame(state, 1, defender, b)


def test_every_frame_in_the_csv_has_an_implemented_ability():
    assert set(FRAME_ABILITIES) == set(FRAMES)


# --------------------------------------------------------------------------
# Card keywords
# --------------------------------------------------------------------------


def test_committed_cards_are_discarded_the_moment_they_resolve():
    assert kw.is_committed(CATALOGUE["Sword_Lunge"])
    assert not kw.is_committed(CATALOGUE["Sword_Slice"])


def _reload_state():
    """A Cannon frame at range from a defender, with the Cannon reloading."""
    state = make_state()
    gunner = add_frame(state, 0, "J7R-Salaryman", Pos(2, 2))
    dfn = add_frame(state, 1, "Kuwagata", Pos(8, 2))
    state.phase = "action"
    return state, gunner, dfn


def arm_reload(state, frame, key):
    """Give the frame a Reload card that has *already fired*.

    Exactly the state `resolve._finish_card` leaves it in: resolved, out of
    the initiative queue, and holding its weapon's reload. Arming it any other
    way would leave the marker itself waiting to act.
    """
    uid = give(state, frame, key, resolved=True, face_down=False)
    state.cards[uid].init_index = len(CATALOGUE[key].initiative)
    kw.start_reload(state, frame, uid)
    return uid


def test_a_reloading_weapon_spends_its_next_attack_doing_nothing():
    """"has no effect or attack" -- the card resolves, but neither the effect
    step nor the attack step happens (rules.tex:963)."""
    state, gunner, dfn = _reload_state()
    marker = arm_reload(state, gunner, "Cannon_Fullbore")
    shot = give(state, gunner, "Cannon_Pummel")           # Low 3, range 12

    frame, uid = R.next_actor(state)
    R._begin_resolution(state, frame, uid)
    assert uid == shot
    assert state.resolution.spent_reloading is True
    assert state.resolution.steps == ["movement"], "no effect and no attack step"
    assert dfn.damage["Low"] == 0
    assert state.cards[marker].location == "discard"
    assert gunner.reloading == {}


def test_the_reload_dud_still_moves():
    state, gunner, dfn = _reload_state()
    marker = arm_reload(state, gunner, "Cannon_Fullbore")
    give(state, gunner, "Cannon_Pummel")

    frame, uid = R.next_actor(state)
    R._begin_resolution(state, frame, uid)
    assert "movement" in state.resolution.steps, "the frame still moves"
    R.advance(state)
    assert state.pending is not None
    assert state.pending.kind == "move" and state.pending.seat == 0


def test_the_reload_dud_consumes_no_block():
    """It never enters attack resolution, so the defender is never asked."""
    from playtest.engine import apply_command, legal_commands

    state, gunner, dfn = _reload_state()
    marker = arm_reload(state, gunner, "Cannon_Fullbore")
    give(state, gunner, "Cannon_Pummel")                  # the dud, Low 3
    blocker = give(state, dfn, "Basic_Block")             # blocks every zone

    # Play out this turn's action phase and nothing beyond it.
    kinds = []
    state = R.advance(state)
    last = state
    while state.phase == "action" and state.pending is not None:
        kinds.append(state.pending.kind)
        state = apply_command(state, legal_commands(state, state.pending.seat)[0])
        if state.phase == "action":
            last = state

    assert kinds, "the dud did resolve"
    assert "choose_block" not in kinds, "a reload dud must not force a block"
    assert last.cards[blocker].location == "committed", "the block is not spent"
    assert last.frames[dfn.id].damage == {"High": 0, "Mid": 0, "Low": 0}


def test_the_reload_dud_triggers_no_abilities():
    """No On Hit:, no Knockback, no frame-ability riders."""
    state = make_state()
    kamikiri = add_frame(state, 0, "Kamikiri", Pos(2, 2))
    dfn = add_frame(state, 1, "Kuwagata", Pos(3, 2))
    state.phase = "action"
    marker = arm_reload(state, kamikiri, "Stun Baton_Overcharge")
    give(state, kamikiri, "Stun Baton_Shock")            # On Hit: 2 stunned

    frame, uid = R.next_actor(state)
    R._begin_resolution(state, frame, uid)
    assert state.resolution.spent_reloading is True
    assert dfn.statuses["stunned"] == 0, "no On Hit: rider"
    assert dfn.damage == {"High": 0, "Mid": 0, "Low": 0}
    assert not kamikiri.turn_flags.get("kamikiri_used"), "no frame-ability rider"
    assert dfn.pos == Pos(3, 2), "no knockback"


def test_a_cannon_does_not_lock_itself_out_by_reloading_on_its_own_dud():
    """Cannon prints Reload on every card. If the dud re-armed the weapon the
    deck would never fire again."""
    state, gunner, dfn = _reload_state()
    marker = arm_reload(state, gunner, "Cannon_Fullbore")
    dud = give(state, gunner, "Cannon_Airburst")          # also carries Reload
    assert "reload" in CATALOGUE["Cannon_Airburst"].keywords

    frame, uid = R.next_actor(state)
    R._begin_resolution(state, frame, uid)
    state.resolution.steps = []                           # skip the move prompt
    R._finish_card(state)
    assert gunner.reloading == {}, "the dud must not re-arm the Cannon"

    # The next Cannon shot therefore fires for real.
    shot = give(state, gunner, "Cannon_Pummel")           # Low 3, range 12
    frame, uid = R.next_actor(state)
    R._begin_resolution(state, frame, uid)
    assert uid == shot and state.resolution.spent_reloading is False
    assert "attack" in state.resolution.steps


def test_a_reload_card_that_fires_normally_arms_the_weapon():
    state, gunner, dfn = _reload_state()
    shot = give(state, gunner, "Cannon_Fullbore")
    frame, uid = R.next_actor(state)
    R._begin_resolution(state, frame, uid)
    assert state.resolution.spent_reloading is False
    state.resolution.steps = []
    R._finish_card(state)
    assert gunner.reloading == {"Cannon": shot}, "firing arms the reload"
    assert state.cards[shot].persist_left is None


def test_reload_only_affects_its_own_weapon_group():
    state, gunner, dfn = _reload_state()
    marker = arm_reload(state, gunner, "Cannon_Fullbore")
    dfn.pos = Pos(3, 2)
    other = give(state, gunner, "Basic_Punch")            # a different weapon
    assert not kw.is_reloading_attack(state, gunner, CATALOGUE["Basic_Punch"])
    run_attack(state, gunner, other, dfn)
    assert dfn.damage["Mid"] == 1
    assert gunner.reloading == {"Cannon": marker}, "the Cannon is still reloading"


def test_a_block_only_card_from_the_group_does_not_clear_the_reload():
    """"until this frame next resolves an *attack* from this weapon"."""
    state, gunner, dfn = _reload_state()
    marker = arm_reload(state, gunner, "Plasma Rifle_Charged Shot")
    assert not kw.is_reloading_attack(state, gunner, CATALOGUE["Shield_Full Guard"])
    assert kw.is_reloading_attack(state, gunner, CATALOGUE["Plasma Rifle_Vent"])


def test_knockback_pushes_the_target_directly_away():
    state, atk, dfn = _duel(a=Pos(2, 2), b=Pos(3, 2))
    kw.apply_knockback(state, atk, dfn, 2)
    assert dfn.pos == Pos(5, 2)


def test_knockback_cannot_push_a_frame_up_an_elevation():
    state, atk, dfn = _duel(a=Pos(2, 2), b=Pos(3, 2))
    state.board.set_tile(Pos(4, 2), elevation=1)
    kw.apply_knockback(state, atk, dfn, 2)
    assert dfn.pos == Pos(3, 2), "blocked immediately by the raised tile"


def test_knockback_stops_at_the_board_edge_and_at_frames():
    state, atk, dfn = _duel(a=Pos(2, 2), b=Pos(3, 2))
    add_frame(state, 1, "Adam", Pos(4, 2))
    kw.apply_knockback(state, atk, dfn, 3)
    assert dfn.pos == Pos(3, 2)


def test_knockback_travels_diagonally():
    state, atk, dfn = _duel(a=Pos(2, 2), b=Pos(3, 3))
    kw.apply_knockback(state, atk, dfn, 2)
    assert dfn.pos == Pos(5, 5)


def test_knockback_lands_from_a_real_attack():
    state, atk, dfn = _duel(a=Pos(2, 2), b=Pos(3, 2))
    uid = give(state, atk, "Kinetic Hammer_Batter")      # Knockback(2)
    run_attack(state, atk, uid, dfn)
    assert dfn.pos == Pos(5, 2)


def test_a_blocked_attack_does_not_knock_back():
    state, atk, dfn = _duel(a=Pos(2, 2), b=Pos(3, 2))
    uid = give(state, atk, "Kinetic Hammer_Batter")      # High 1, Mid 1
    give(state, dfn, "Basic_Punch")
    run_attack(state, atk, uid, dfn)
    assert dfn.pos == Pos(3, 2)


# --------------------------------------------------------------------------
# Frame abilities
# --------------------------------------------------------------------------


def test_adam_pierce_attacks_get_plus_two_initiative():
    state = make_state()
    adam = add_frame(state, 0, "Adam", Pos(1, 1))
    other = add_frame(state, 1, "Kuwagata", Pos(5, 5))
    pierce = CATALOGUE["Spear_Thrust"]                   # initiative 7, pierce
    cut = CATALOGUE["Sword_Slice"]                       # initiative 5, cut
    assert kw.effective_initiative(state, adam, pierce) == 9
    assert kw.effective_initiative(state, adam, cut) == 5
    assert kw.effective_initiative(state, other, pierce) == 7


def test_initiative_falls_by_one_when_the_high_zone_is_at_its_last_hit():
    state = make_state()
    frame = add_frame(state, 0, "Kuwagata", Pos(1, 1))
    frame.damage["High"] = 3                             # armour 4
    assert kw.effective_initiative(state, frame, CATALOGUE["Spear_Thrust"]) == 6


def test_percival_softens_the_movement_cost_of_multi_block_attacks():
    state = make_state()
    percival = add_frame(state, 0, "Percival MkIV", Pos(1, 1))
    plain = add_frame(state, 1, "Kuwagata", Pos(9, 9))
    card = CATALOGUE["Sword_Slice"]                      # mv -2, blocks High+Low
    assert len(card.block_zones) >= 2 and card.is_attack
    assert kw.card_movement_modifier(state, percival, card) == 0
    assert kw.card_movement_modifier(state, plain, card) == -2


def test_percival_does_not_turn_a_penalty_into_a_bonus():
    state = make_state()
    percival = add_frame(state, 0, "Percival MkIV", Pos(1, 1))
    card = CATALOGUE["Chain_Throttle"]                   # mv -1, one block zone
    assert kw.card_movement_modifier(state, percival, card) == -1


def test_nautilus_softens_ranged_movement_and_salaryman_extends_range():
    state = make_state()
    nautilus = add_frame(state, 0, "VX4-Nautilus", Pos(1, 1))
    salaryman = add_frame(state, 0, "J7R-Salaryman", Pos(2, 1))
    card = CATALOGUE["Assault Rifle_Aimed Fire"]         # mv -2, range 9
    assert kw.card_movement_modifier(state, nautilus, card) == 0
    assert kw.range_bonus(state, salaryman, card) == 4
    assert kw.range_bonus(state, nautilus, card) == 0
    assert combat.effective_range(state, salaryman, card, "High") == 13


def test_rippersmasher_caps_every_movement_penalty_at_one():
    state = make_state()
    ripper = add_frame(state, 0, "RipperSmasher", Pos(1, 1))
    heavy = CATALOGUE["Chainsaw_Disembowel"]             # mv -4
    assert kw.card_movement_modifier(state, ripper, heavy) == -1
    assert kw.movement_budget(state, ripper, heavy) == ripper.spec.movement - 1


def test_fenrir_cannot_use_ranged_weapons():
    state = make_state()
    fenrir = add_frame(state, 0, "Fenrir", Pos(1, 1))
    target = add_frame(state, 1, "Kuwagata", Pos(5, 1))
    ranged = CATALOGUE["Stun Bow_Arc shot"]
    assert not kw.can_use_ranged(fenrir)
    assert combat.legal_targets(state, fenrir, ranged) == []
    # melee is unaffected
    target.pos = Pos(2, 1)
    assert combat.legal_targets(state, fenrir, CATALOGUE["Spear_Thrust"])


def test_hector_keeps_the_first_block_of_each_turn_but_not_the_second():
    state = make_state()
    atk = add_frame(state, 0, "Kuwagata", Pos(1, 1))
    hector = add_frame(state, 1, "Hector MkI", Pos(2, 1))
    first = give(state, hector, "Basic_Punch")
    second = give(state, hector, "Basic_Punch")

    run_attack(state, atk, give(state, atk, "Chainsaw_Disembowel"), hector)
    kept = [u for u in (first, second) if state.cards[u].location == "committed"]
    assert len(kept) == 2, "Hector's first block is not discarded"

    run_attack(state, atk, give(state, atk, "Chainsaw_Disembowel"), hector)
    kept = [u for u in (first, second) if state.cards[u].location == "committed"]
    assert len(kept) == 1, "the second block is discarded as normal"


def test_kamikiri_adds_a_mid_mark_to_its_first_melee_attack_only():
    state = make_state()
    kamikiri = add_frame(state, 0, "Kamikiri", Pos(1, 1))
    dfn = add_frame(state, 1, "Hector MkI", Pos(2, 1))   # armour 5/4/4
    first = give(state, kamikiri, "Axe_Chop")            # High 2, no Mid mark
    run_attack(state, kamikiri, first, dfn)
    assert dfn.damage["High"] == 2 and dfn.damage["Mid"] == 1

    second = give(state, kamikiri, "Axe_Chop")
    run_attack(state, kamikiri, second, dfn)
    assert dfn.damage["High"] == 4
    assert dfn.damage["Mid"] == 1, "only the first melee attack each turn"


def test_kamikiri_bonus_does_not_apply_to_ranged_attacks():
    state = make_state()
    kamikiri = add_frame(state, 0, "Kamikiri", Pos(1, 1))
    dfn = add_frame(state, 1, "Kuwagata", Pos(5, 1))
    uid = give(state, kamikiri, "Assault Rifle_Aimed Fire")
    run_attack(state, kamikiri, uid, dfn)
    assert dfn.damage["Mid"] == 0


def test_elemiah_gives_impact_attacks_knockback_one():
    state = make_state()
    elemiah = add_frame(state, 0, "Elemiah", Pos(2, 2))
    dfn = add_frame(state, 1, "Kuwagata", Pos(3, 2))
    punch = CATALOGUE["Basic_Punch"]                     # impact, no knockback
    assert punch.knockback == 0
    assert kw.knockback_amount(state, elemiah, punch) == 1
    run_attack(state, elemiah, give(state, elemiah, "Basic_Punch"), dfn)
    assert dfn.pos == Pos(4, 2)


def test_elemiah_leaves_non_impact_attacks_alone():
    state = make_state()
    elemiah = add_frame(state, 0, "Elemiah", Pos(2, 2))
    assert kw.knockback_amount(state, elemiah, CATALOGUE["Sword_Slice"]) == 0


def test_flamekin_repairs_one_at_the_end_of_every_turn():
    state = make_state()
    flamekin = add_frame(state, 0, "Flamekin", Pos(1, 1))
    flamekin.damage["Mid"] = 1
    kw.end_of_turn(state)
    assert flamekin.damage["Mid"] == 0


def test_hannael_is_flying_and_starts_with_a_shield():
    state = make_state()
    hannael = add_frame(state, 0, "Hannael", Pos(1, 1))
    assert kw.is_flying(hannael) and hannael.shields == 1
    assert not kw.is_flying(add_frame(state, 0, "Kuwagata", Pos(2, 2)))


def test_flying_ignores_obstacles_and_climbs_when_it_moves():
    state = make_state()
    flyer = add_frame(state, 0, "Hannael", Pos(1, 1))
    walker = add_frame(state, 1, "Kuwagata", Pos(8, 8))
    state.board.set_tile(Pos(2, 1), obstacle=True, elevation=3)
    budget = 2
    air = state.board.reachable(flyer.pos, budget, flying=True)
    ground = state.board.reachable(walker.pos, budget, flying=False)
    assert Pos(2, 1) in air
    assert Pos(2, 1) not in state.board.reachable(flyer.pos, budget, flying=False)
    assert ground


def test_flying_target_is_passed_to_line_of_sight_when_the_board_takes_it():
    """B1's board grew an optional `flying_target`; Flying says obstacles do
    not block LoS *to or from* the frame."""
    seen = {}

    class RecordingBoard(make_state().board.__class__):
        def has_line_of_sight(self, a, b, *, occupied=frozenset(),
                              flying_attacker=False, flying_target=False):
            seen["attacker"] = flying_attacker
            seen["target"] = flying_target
            return True

    state = make_state()
    state.board = RecordingBoard(10, 10)
    gunner = add_frame(state, 0, "J7R-Salaryman", Pos(1, 1))
    flyer = add_frame(state, 1, "Hannael", Pos(5, 1))
    combat.can_target(state, gunner, CATALOGUE["Cannon_Fullbore"], flyer.pos, flyer)
    assert seen == {"attacker": False, "target": True}


# --------------------------------------------------------------------------
# The effect registry: what v1 implements and what it defers
# --------------------------------------------------------------------------


def test_no_weapon_basic_or_booster_text_is_ever_deferred():
    """The durable invariant: the engine never silently drops card text.

    Weapon, basic, booster and frame card text must always be either
    implemented or handled elsewhere in the pipeline. Only pilot and drone
    text may be deferred, and that set shrinks as those effects land -- so
    this asserts the bound, not a count.
    """
    from playtest.engine import effects

    deferred = effects.deferred_effects(CATALOGUE)
    types = {CATALOGUE[key].card_type for key in deferred}
    assert types <= {"pilot", "drone"}, f"deferred outside pilot/drone: {types}"
    for key, marker in deferred.items():
        assert "not implemented" in marker.reason
        assert marker.text, f"{key} deferred with no text to show"


def test_keyword_only_text_does_not_produce_an_effect_step():
    from playtest.engine import effects

    for key in ("Spear_Jab", "Knife_Lunge", "Railgun_Kinetic Barrage",
                "Missile Rack_Missile Rack 1", "Booster_Quick Step",
                "Kinetic Hammer_Slam", "Sniper Rifle_Headshot",
                "Stun Baton_Overcharge", "Sword_Parry"):
        assert effects.effect_kind(CATALOGUE[key]) == "none", key
        assert not effects.has_effect_step(CATALOGUE[key]), key


def test_implemented_card_text_is_marked_handled():
    from playtest.engine import effects

    for key in ("Basic_Dodge", "Frame_Bio-regen", "Frame_Shield",
                "Frame_Call of Nature", "Booster_Accelerate"):
        assert effects.effect_kind(CATALOGUE[key]) == "handled", key


def test_accelerate_gives_later_actions_extra_movement():
    """"*Other* actions this turn get +3 mv" -- so not Accelerate's own move.

    Which matters because the controller orders a card's steps, and putting
    the effect step first would otherwise hand Accelerate its own bonus.
    """
    from playtest.engine import effects

    state = make_state()
    frame = add_frame(state, 0, "Kuwagata", Pos(1, 1))
    uid = give(state, frame, "Booster_Accelerate")
    before = kw.movement_budget(state, frame, CATALOGUE["Spear_Thrust"])
    accelerate = CATALOGUE["Booster_Accelerate"]
    own = kw.movement_budget(state, frame, accelerate)

    effects.resolve_effect(state, frame, uid)
    assert kw.movement_budget(state, frame, accelerate) == own, (
        "its own movement step is untouched, whichever order the steps run in"
    )
    assert kw.movement_budget(state, frame, CATALOGUE["Spear_Thrust"]) == before

    effects.after_card_resolved(state, frame, uid)     # the card finishes
    assert kw.movement_budget(state, frame, CATALOGUE["Spear_Thrust"]) == before + 3


def test_dodge_shortens_incoming_ranged_attacks():
    from playtest.engine import effects

    state = make_state()
    gunner = add_frame(state, 0, "Kuwagata", Pos(1, 1))
    dodger = add_frame(state, 1, "Kuwagata", Pos(6, 1))
    card = CATALOGUE["Assault Rifle_Aimed Fire"]          # range 9
    assert combat.zones_in_range(state, gunner, card, dodger.pos, dodger)
    uid = give(state, dodger, "Basic_Dodge")
    effects.resolve_effect(state, dodger, uid)
    assert combat.effective_range(state, gunner, card, "High", dodger) == 1
    assert combat.zones_in_range(state, gunner, card, dodger.pos, dodger) == {}


def test_frame_shield_card_adds_a_counter():
    from playtest.engine import effects

    state = make_state()
    frame = add_frame(state, 0, "Hannael", Pos(1, 1))
    frame.shields = 0
    uid = give(state, frame, "Frame_Shield")
    effects.resolve_effect(state, frame, uid)
    assert frame.shields == 1


def test_on_block_riders_fire_when_the_card_blocks():
    from playtest.engine import effects

    state = make_state()
    attacker = add_frame(state, 0, "Kuwagata", Pos(1, 1))
    defender = add_frame(state, 1, "Kuwagata", Pos(2, 1))
    effects.on_block(state, defender, CATALOGUE["Chain_Catch"], attacker)
    assert attacker.statuses["dazed"] == 1

    effects.on_block(state, defender, CATALOGUE["Sword_Parry"], attacker)
    assert attacker.damage["Mid"] == 1, "Parry strikes back for one Mid"
