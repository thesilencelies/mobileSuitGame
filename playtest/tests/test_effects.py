"""Pilot and drone card text: one test per card, proving the text does something.

Every card in `effects.EFFECT_STEPS` has a test here that fails if the effect
is removed. Cards that are still `DeferredEffect` are covered too -- by a test
asserting they are *flagged*, which is the honest behaviour for an effect the
engine does not implement.

Most tests drive the effect layer directly (`resolve_effect` /
`apply_effect_choice` / the passive hooks) rather than playing a whole game:
the point is to pin the card's own behaviour, not the state machine's. The
ones that must go through the real pipeline -- Net Strength's Guard Break, Ace
Reflexes' trigger, the drone's compulsory blocks -- use `combat` end to end.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from playtest.engine import combat, effects
from playtest.engine import effects_state as fx
from playtest.engine import keywords as kw
from playtest.engine.serialize import view_for
from playtest.engine.state import Resolution, move_card
from playtest.engine.types import Pos

from ._helpers import CATALOGUE, add_frame, give, make_state, run_attack

# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------


def play(state, frame, key, *, resolved: bool = True):
    """Commit a card to `frame` and run its effect step.

    Returns `(uid, decision)`. `resolved` leaves the card marked resolved, the
    way `_finish_card` would, which is what makes a persistent card count as
    "in play this turn" for `card_active`.
    """
    uid = give(state, frame, key)
    decision = effects.resolve_effect(state, frame, uid)
    if resolved:
        state.cards[uid].resolved = True
    return uid, decision


def answer(state, decision, option=None, index: int = 0):
    """Answer an `effect_choice` the way `resolve._handle_effect_choice` does.

    Returns the next decision if the effect asked another question.
    """
    assert decision is not None, "expected the effect to ask something"
    assert decision.kind == "effect_choice"
    frame = state.frames[str(decision.frame_id)]
    payload = dict(option if option is not None else decision.options[index])
    assert any(
        all(o.get(k) == v for k, v in payload.items()) for o in decision.options
    ), f"{payload} was not offered"
    state.pending = None
    effects.apply_effect_choice(state, frame, "", payload)
    nxt, state.pending = state.pending, None
    return nxt


def summon(state, frame, key, *spots, resolved: bool = True):
    """Play a drone card and answer the placement it asks for.

    Every drone card now names where its tokens land -- "within 3" for a Gun
    Tower, beside the frame otherwise -- so a test that wants a drone on a
    particular tile says so here instead of moving the token afterwards.
    """
    uid, decision = play(state, frame, key, resolved=resolved)
    wanted = list(spots)
    while decision is not None:
        spot = wanted.pop(0) if wanted else None
        decision = answer(
            state, decision, {"x": spot.x, "y": spot.y} if spot else None)
    assert not wanted, "more placements given than the card asked for"
    return uid


def carry_over(state, *uids):
    """Roll into the next turn, setting persistent cards aside as cleanup does."""
    for uid in uids:
        move_card(state, uid, "aside")
    state.turn += 1
    for frame in state.frames.values():
        frame.turn_flags = {}
    return state


def duel(seed: int = 0, gap: int = 1):
    """A state with one frame per seat, `gap` tiles apart."""
    state = make_state(seed)
    state.phase = "action"
    mine = add_frame(state, 0, "Kamikiri", Pos(2, 2))
    theirs = add_frame(state, 1, "Hector MkI", Pos(2 + gap, 2))
    return state, mine, theirs


# --------------------------------------------------------------------------
# Boosters
# --------------------------------------------------------------------------


def test_jump_makes_a_climb_cost_what_flat_ground_costs():
    """"All movement this turn ignores elevation penalties" -- Jump."""
    state = make_state()
    frame = add_frame(state, 0, "Kuwagata", Pos(1, 1))
    for x in range(2, 6):
        state.board.set_tile(Pos(x, 1), elevation=2)
    budget = kw.movement_budget(state, frame, CATALOGUE[effects.JUMP])

    def cost_to(pos, **kw_):
        for option in state.walk_options(frame, budget, **kw_):
            if (option["x"], option["y"]) == (pos.x, pos.y):
                return option["cost"]
        return None

    cliff = Pos(3, 1)
    assert cost_to(cliff) == 4, "one step up two levels, then one along"
    play(state, frame, effects.JUMP)
    assert effects.ignores_elevation(state, frame)
    assert cost_to(cliff, climb_free=True) == 2, "the climb is free, the steps are not"


def test_jump_does_not_let_the_frame_cross_an_obstacle():
    """Only half of Flying: the surcharge goes, the licence does not."""
    state = make_state()
    frame = add_frame(state, 0, "Kuwagata", Pos(1, 1))
    for y in range(state.board.height):          # a wall clean across the map
        state.board.set_tile(Pos(2, y), obstacle=True)
    play(state, frame, effects.JUMP)
    reached = {
        (o["x"], o["y"])
        for o in state.walk_options(frame, 6, climb_free=True)
    }
    assert not any(x >= 2 for x, _ in reached), "the wall still stops it"


def test_jump_is_in_force_for_its_own_movement():
    state = make_state()
    frame = add_frame(state, 0, "Kuwagata", Pos(1, 1))
    card = CATALOGUE[effects.JUMP]
    orders = effects.step_orders(card, ["movement", "effect"])
    assert orders == [["effect", "movement"]], (
        '"all movement this turn" has to include the move the card came with'
    )


def test_boomerang_returns_the_frame_to_where_the_action_started():
    state = make_state()
    frame = add_frame(state, 0, "Kuwagata", Pos(2, 2))
    play(state, frame, effects.BOOMERANG)
    assert fx.slot(state, "boomerang")[frame.id]["turn"] == state.turn + 1

    frame.pos = Pos(7, 7)                       # it spent the turn elsewhere
    effects.start_of_turn(state)
    assert frame.pos == Pos(7, 7), "not until the turn it named"

    state.turn += 1
    effects.start_of_turn(state)
    assert frame.pos == Pos(2, 2)
    assert not fx.slot(state, "boomerang"), "and only once"


def test_boomerang_notes_the_spot_before_the_card_moves_the_frame():
    card = CATALOGUE[effects.BOOMERANG]
    assert effects.step_orders(card, ["movement", "effect"]) == [
        ["effect", "movement"]
    ], "the anchor is where the action started, not where its movement ended"


def test_boomerang_lands_beside_the_anchor_when_someone_is_standing_on_it():
    state = make_state()
    frame = add_frame(state, 0, "Kuwagata", Pos(2, 2))
    play(state, frame, effects.BOOMERANG)
    add_frame(state, 1, "Adam", Pos(2, 2))      # parked on the spot
    frame.pos = Pos(7, 7)
    state.turn += 1
    effects.start_of_turn(state)
    assert frame.pos != Pos(7, 7), "it still comes back"
    assert state.board.distance(frame.pos, Pos(2, 2)) == 1


def test_explosive_exit_must_swing_before_it_leaves():
    """"Must attack before moving" -- read off the text, not the card key."""
    card = CATALOGUE[effects.EXPLOSIVE_EXIT]
    assert "must attack before moving" in card.text.lower()
    assert effects.step_orders(card, ["movement", "attack"]) == [
        ["attack", "movement"]
    ]
    # And the constraint is only as wide as it says: nothing is fixed about
    # where an effect step would go.
    assert ["attack", "effect", "movement"] in effects.step_orders(
        card, ["movement", "effect", "attack"]
    )


def test_a_card_that_says_nothing_about_order_still_offers_every_order():
    card = CATALOGUE["Booster_Full speed ahead"]
    assert len(effects.step_orders(card, ["movement", "effect", "attack"])) == 6


def test_explosive_exit_catches_everything_next_to_it():
    """Knockback and the splash both come from the printed keywords."""
    card = CATALOGUE[effects.EXPLOSIVE_EXIT]
    assert card.knockback == 1
    state, mine, theirs = duel()
    beside = add_frame(state, 1, "Hector MkI", Pos(2, 3))
    uid = give(state, mine, effects.EXPLOSIVE_EXIT)
    attack = combat.declare_attack(
        state, mine, uid, target_kind="frame", target_id=theirs.id
    )
    assert {t.id for t in attack.targets} == {theirs.id, beside.id}


# --------------------------------------------------------------------------
# Registry integrity
# --------------------------------------------------------------------------


def test_every_registered_effect_names_a_real_card_and_reads_as_handled():
    for key in effects.EFFECT_STEPS:
        assert key in CATALOGUE, f"{key} is not a card"
        assert effects.effect_kind(CATALOGUE[key]) == "handled", key
        assert effects.has_effect_step(CATALOGUE[key]), key


def test_every_decision_the_module_raises_has_a_handler():
    """A typo in an `_ask` name would silently drop the player's answer."""
    source = Path(effects.__file__).read_text()
    asked = set(re.findall(r'_ask\(\s*state,\s*"([a-z_]+)"', source))
    assert asked, "no _ask call sites found -- the guard is not biting"
    missing = asked - set(effects.CHOICE_HANDLERS)
    assert not missing, f"decisions with no handler: {sorted(missing)}"


def test_all_pilot_and_drone_text_is_implemented_or_flagged():
    """Asked through `_effect_handler`, not the table.

    Drone cards are matched on their *type* rather than listed key by key --
    every one of them summons, and the count and reach come off the text -- so
    a new drone in the CSV is implemented the moment it is added.
    """
    deferred = effects.deferred_effects(CATALOGUE)
    for key, card in CATALOGUE.items():
        if card.card_type not in ("pilot", "drone") or not card.text.strip():
            continue
        assert effects._effect_handler(card) is not None or key in deferred, (
            f"{key} has text that is neither implemented nor flagged"
        )


# --------------------------------------------------------------------------
# Bruiser
# --------------------------------------------------------------------------


def test_relentless_assault_sends_other_actions_back_round_the_queue():
    state, frame, _ = duel()
    before = frame.base_movement
    uid, decision = play(state, frame, effects.RELENTLESS)
    assert decision is None
    assert frame.base_movement == before, "not its own movement step"

    effects.after_card_resolved(state, frame, uid)     # the card finishes
    assert frame.base_movement == max(0, before - 2), "-2mv on the other actions"

    other = give(state, frame, "Spear_Thrust")
    state.cards[other].init_index = 1                 # as _finish_card leaves it
    effects.after_card_resolved(state, frame, other)
    assert state.cards[other].init_index == 0, "the action resolves a second time"

    state.cards[other].init_index = 1
    effects.after_card_resolved(state, frame, other)
    assert state.cards[other].init_index == 1, "twice, not for ever"

    state.cards[uid].init_index = 1
    effects.after_card_resolved(state, frame, uid)
    assert state.cards[uid].init_index == 1, "'other actions' excludes itself"


def test_relentless_assault_does_nothing_before_it_resolves():
    state, frame, _ = duel()
    other = give(state, frame, "Spear_Thrust")
    state.cards[other].init_index = 1
    effects.after_card_resolved(state, frame, other)
    assert state.cards[other].init_index == 1


def test_intimidate_makes_every_enemy_within_five_consume_a_block():
    state = make_state()
    bruiser = add_frame(state, 0, "Kamikiri", Pos(2, 2))
    near = add_frame(state, 1, "Hector MkI", Pos(4, 4))
    lone = add_frame(state, 1, "Adam", Pos(3, 3))
    far = add_frame(state, 1, "Fenrir", Pos(9, 9))

    a = give(state, near, "Bruiser_Lockdown")
    b = give(state, near, "Bruiser_Net Strength")
    only = give(state, lone, "Bruiser_Lockdown")
    safe = give(state, far, "Bruiser_Lockdown")

    _uid, decision = play(state, bruiser, effects.INTIMIDATE)
    assert decision is not None and decision.seat == near.seat
    assert {o["uid"] for o in decision.options} == {a, b}
    assert answer(state, decision, {"uid": b}) is None
    assert state.cards[b].location == "discard"
    assert state.cards[only].location == "discard", (
        "a frame with one possible block loses it without being asked"
    )
    assert state.cards[a].location == "committed", "only one block is consumed"
    assert state.cards[safe].location == "committed", "out of range, untouched"


def test_net_strength_gives_guard_break_and_a_daze_this_turn_and_next():
    state, attacker, defender = duel()
    uid, _ = play(state, attacker, effects.NET_STRENGTH)
    assert effects.grants_guard_break(state, attacker)

    spear = give(state, attacker, "Spear_Thrust")
    attack = combat.declare_attack(
        state, attacker, spear, target_kind="frame", target_id=defender.id
    )
    assert attack.guard_break, "Net Strength grants Guard Break"
    assert not kw.is_guard_break(CATALOGUE["Spear_Thrust"]), "not printed on the card"

    effects.on_hit(state, attacker, CATALOGUE["Spear_Thrust"], defender)
    assert defender.statuses["dazed"] > 0

    carry_over(state, uid)
    assert effects.grants_guard_break(state, attacker), "'this turn and next'"


def test_net_strength_expires_with_the_card():
    state, attacker, _ = duel()
    uid, _ = play(state, attacker, effects.NET_STRENGTH)
    move_card(state, uid, "discard")
    assert not effects.grants_guard_break(state, attacker)


def test_lockdown_slows_a_chosen_frame_within_three():
    state = make_state()
    pilot = add_frame(state, 0, "Kamikiri", Pos(2, 2))
    enemy = add_frame(state, 1, "Hector MkI", Pos(4, 2))
    ally = add_frame(state, 0, "Adam", Pos(3, 3))
    far = add_frame(state, 1, "Fenrir", Pos(9, 9))

    _uid, decision = play(state, pilot, effects.LOCKDOWN)
    offered = [o["frame"] for o in decision.options]
    assert set(offered) == {enemy.id, ally.id}
    assert offered[0] == enemy.id, "enemies are offered first"
    assert answer(state, decision) is None
    assert enemy.statuses["slowed"] == 3
    assert enemy.statuses["stunned"] == 3
    assert far.statuses["slowed"] == 0
    assert ally.statuses["slowed"] == 0, "only the chosen frame"


def test_lockdown_actually_costs_the_target_movement():
    state = make_state()
    pilot = add_frame(state, 0, "Kamikiri", Pos(2, 2))
    enemy = add_frame(state, 1, "Hector MkI", Pos(4, 2))
    before = enemy.base_movement
    play(state, pilot, effects.LOCKDOWN)
    assert enemy.statuses["slowed"] == 3
    assert enemy.base_movement < before


def test_bind_holds_an_adjacent_frame_still():
    state, bruiser, victim = duel()
    _uid, decision = play(state, bruiser, effects.BIND)
    assert decision is None, "only one frame is adjacent"
    assert effects.is_bound(state, victim)

    options = effects.adjust_move_options(
        state, victim, 4,
        [{"x": 5, "y": 5, "cost": 2}, {"x": 3, "y": 2, "cost": 1}],
    )
    assert options == [{"x": victim.pos.x, "y": victim.pos.y, "cost": 0}]


def test_a_bind_lets_go_when_the_bruiser_is_no_longer_next_to_it():
    from playtest.engine.state import destroy_frame

    state, bruiser, victim = duel()
    uid, _ = play(state, bruiser, effects.BIND)
    carry_over(state, uid)                       # persistence is infinite
    assert effects.is_bound(state, victim), "the hold survives the turn"

    victim.pos = Pos(7, 7)
    assert not effects.is_bound(state, victim), "out of reach"
    victim.pos = Pos(3, 2)
    assert effects.is_bound(state, victim)
    destroy_frame(state, bruiser)
    assert not effects.is_bound(state, victim), "a dead frame holds nothing"


def test_suplex_throws_a_frame_past_the_thrower_and_rattles_it():
    state = make_state()
    bruiser = add_frame(state, 0, "Kamikiri", Pos(5, 5))
    victim = add_frame(state, 1, "Hector MkI", Pos(7, 5))

    _uid, where = play(state, bruiser, effects.SUPLEX)
    assert where is not None and where.pick_kind == "place"
    tiles = {Pos(o["x"], o["y"]) for o in where.options}
    assert Pos(3, 5) in tiles, "the far side of the thrower"
    assert Pos(6, 5) not in tiles, "not back the way it came"
    assert all(state.board.distance(bruiser.pos, t) <= 3 for t in tiles)

    assert answer(state, where, {"x": 3, "y": 5}) is None
    assert victim.pos == Pos(3, 5)
    assert victim.statuses["stunned"] == 2
    assert victim.statuses["dazed"] == 2


# --------------------------------------------------------------------------
# Mystic
# --------------------------------------------------------------------------


def _images(state, frame):
    """`(real_token, [fake_tokens])` for a frame that has played the card."""
    ids = effects.image_tokens(state, frame)
    record = fx.slot(state, "images")[frame.id]
    real = state.tokens[record["real"]]
    return real, [state.tokens[i] for i in ids if i != record["real"]]


def test_ephemeral_images_puts_out_three_images_with_the_frame_on_one():
    state, frame, foe = duel(gap=4)
    play(state, frame, effects.EPHEMERAL)
    real, fakes = _images(state, frame)
    assert len(fakes) == 2, "three images, the frame's own tile included"
    assert real.pos == frame.pos, "the real image is wherever the frame is"
    assert all(f.pos != frame.pos for f in fakes)
    assert effects.is_cloaked(state, frame)


def test_a_cloaked_frame_is_not_a_legal_target_but_its_images_are():
    state, frame, foe = duel(gap=1)
    play(state, frame, effects.EPHEMERAL)
    card = CATALOGUE["Basic_Punch"]
    assert effects.is_untargetable(state, foe, card, frame)
    uid = give(state, foe, "Basic_Punch")
    options = combat.legal_targets(state, foe, CATALOGUE["Basic_Punch"])
    assert not [o for o in options if o["id"] == frame.id], (
        "the frame itself must not be offered while it is hiding"
    )
    offered = {o["id"] for o in options if o["kind"] == "token"}
    assert offered & set(effects.image_tokens(state, frame)), (
        "the images must be attackable in the frame's place"
    )


def test_another_seat_cannot_tell_the_frame_from_its_images():
    """The concealment is in the view, not in the client's manners."""
    state, frame, foe = duel(gap=4)
    play(state, frame, effects.EPHEMERAL)

    theirs = view_for(state, 1)
    hidden = next(f for f in theirs["frames"] if f["id"] == frame.id)
    assert hidden["pos"] is None, "a cloaked frame's tile must not be sent"
    assert hidden.get("cloaked") is True
    images = [t for t in theirs["tokens"] if t["kind"] == effects.IMAGE]
    assert len(images) == 3
    assert all("real" not in t for t in images), "the view marked the real one"

    mine = view_for(state, 0)
    seen = next(f for f in mine["frames"] if f["id"] == frame.id)
    assert seen["pos"] == {"x": frame.pos.x, "y": frame.pos.y}
    real = [t for t in mine["tokens"] if t.get("real")]
    assert len(real) == 1, "its own side knows which one it is standing on"


def test_striking_a_fake_removes_it_and_leaves_the_frame_untouched():
    state, frame, foe = duel(gap=1)
    play(state, frame, effects.EPHEMERAL)
    _, fakes = _images(state, frame)
    fake = fakes[0]
    foe.pos = Pos(fake.pos.x + 1, fake.pos.y)
    uid = give(state, foe, "Basic_Punch")
    run_attack(state, foe, uid, fake, target_kind="token")
    assert not fake.alive and fake.pos is None
    assert sum(frame.damage.values()) == 0, "a fake is not the frame"
    assert effects.is_cloaked(state, frame), "two images is still a guess"


def test_striking_the_real_image_hits_the_frame_and_ends_the_trick():
    state, frame, foe = duel(gap=1)
    play(state, frame, effects.EPHEMERAL)
    real, _ = _images(state, frame)
    foe.pos = Pos(real.pos.x + 1, real.pos.y)
    uid = give(state, foe, "Basic_Punch")
    attack = run_attack(state, foe, uid, real, target_kind="token")
    assert attack.targets[0].kind == "frame"
    assert attack.targets[0].id == frame.id, "the attack was on the frame itself"
    assert not effects.is_cloaked(state, frame)
    assert not [
        t for t in state.tokens.values() if t.kind == effects.IMAGE and t.alive
    ], "the images go once the frame has been found"


def test_the_real_image_goes_where_the_frame_goes_and_nothing_else_does():
    """Each image is its own piece; only the one the frame stands on follows it.

    They used to be dragged along at a fixed offset so a lone move could not
    say which was real. That is not needed and is now the wrong shape: the
    pieces are indistinguishable, so an enemy that shoves one learns only that
    it shoved one -- and each of them can be moved by name.
    """
    state, frame, foe = duel(gap=6)
    play(state, frame, effects.EPHEMERAL)
    _, fakes = _images(state, frame)
    before = {f.id: f.pos for f in fakes}
    frame.pos = Pos(frame.pos.x + 2, frame.pos.y + 1)
    effects.sync_images(state)
    real, fakes = _images(state, frame)
    assert real.pos == frame.pos, "the frame is standing on it"
    for fake in fakes:
        assert fake.pos == before[fake.id], "nothing moved them"
    assert effects.is_cloaked(state, frame)


def _walk_images(state, frame, uid, pick=None):
    """Run the per-image movement the frame's card grants. Returns the moves.

    `pick(options) -> option` chooses each image's tile; the default takes the
    furthest one offered, which is the interesting case.
    """
    state.resolution = Resolution(frame_id=frame.id, uid=uid, steps=[])
    effects.after_move(state, frame, frame.pos, frame.pos)
    asked = 0
    decision, state.pending = state.pending, None
    while decision is not None:
        asked += 1
        assert decision.kind == "effect_choice" and decision.pick_kind == "move"
        options = [o for o in decision.options if "x" in o]
        chosen = pick(options) if pick else max(
            options,
            key=lambda o: state.board.distance(frame.pos, Pos(o["x"], o["y"])),
        )
        decision = answer(state, decision, chosen)
    return asked


def test_each_image_walks_on_its_own_when_the_frame_acts():
    """"These tokens use this frame's actions" -- so each of them moves.

    They used to be dragged along at a fixed offset. Now the frame's own move
    is one answer and each fake is another, so the three can spread out.
    """
    state, frame, _ = duel(gap=9)
    play(state, frame, effects.EPHEMERAL)
    _, fakes = _images(state, frame)
    before = {f.id: f.pos for f in fakes}
    home = frame.pos

    uid = give(state, frame, "Basic_Sprint")
    budget = kw.movement_budget(state, frame, CATALOGUE["Basic_Sprint"])
    assert budget > 1, "the card has to actually grant a move"
    assert _walk_images(state, frame, uid) == len(fakes), "one question per fake"

    assert frame.pos == home, "the frame itself did not move"
    _, fakes = _images(state, frame)
    for fake in fakes:
        assert fake.pos != before[fake.id]
        assert state.board.distance(fake.pos, before[fake.id]) <= budget
    assert any(state.board.distance(f.pos, frame.pos) > 1 for f in fakes), (
        "an image can walk away from the frame, which is the point of them"
    )
    effects.sync_images(state)
    assert effects.is_cloaked(state, frame), "spreading out is not a reveal"
    assert all(f.alive for f in fakes)


def test_an_action_may_be_counted_from_any_image():
    """"effects can be counted from any of them": range and sight both."""
    state = make_state(width=20, height=20)
    state.phase = "action"
    frame = add_frame(state, 0, "Kamikiri", Pos(2, 2))
    foe = add_frame(state, 1, "Hector MkI", Pos(2, 8))
    card = CATALOGUE["Basic_Punch"]                       # melee, adjacent only

    play(state, frame, effects.EPHEMERAL)
    assert not combat.can_target(state, frame, card, foe.pos, foe), "nowhere near"

    _, fakes = _images(state, frame)
    fakes[0].pos = Pos(2, 7)                              # one image walks up
    assert combat.can_target(state, frame, card, foe.pos, foe), (
        "the image next to the target is what the action is counted from"
    )
    assert combat.zones_in_range(state, frame, card, foe.pos, foe)
    assert not combat.zones_in_range(
        state, frame, card, foe.pos, foe, origin=frame.pos
    ), "and not from the frame's own tile, which is still six tiles away"


def test_a_fake_that_would_have_dealt_damage_is_removed():
    """"the fakes are removed ... if they would deal damage".

    All three swing. The one that hit was real; any fake that also reached is
    revealed for what it is. A fake out of reach swung at nothing and stays.
    """
    state = make_state(width=20, height=20)
    state.phase = "action"
    frame = add_frame(state, 0, "Kamikiri", Pos(2, 2))
    foe = add_frame(state, 1, "Hector MkI", Pos(3, 2))
    play(state, frame, effects.EPHEMERAL)
    real, fakes = _images(state, frame)
    near, far = fakes
    near.pos = Pos(3, 1)                       # also next to the target
    far.pos = Pos(2, 9)                        # nowhere near it

    run_attack(state, frame, give(state, frame, "Basic_Punch"), foe)
    assert sum(foe.damage.values()) > 0, "the attack landed"
    assert not near.alive, "it reached, so it gave itself away"
    assert far.alive, "it could not have hit anything"
    assert real.alive and effects.is_cloaked(state, frame), "two images left"


def test_an_image_is_targeted_in_the_frames_place_and_the_debuff_sticks():
    """"Each image is treated as a frame in itself for interactions and
    targeting", and "debuffs that target an image are passed onto the original".
    """
    state = make_state(width=20, height=20)
    state.phase = "action"
    mystic = add_frame(state, 0, "Hannael", Pos(4, 4))
    ally = add_frame(state, 0, "Flamekin", Pos(6, 6))
    foe = add_frame(state, 1, "Hector MkI", Pos(5, 5))
    play(state, mystic, effects.EPHEMERAL)
    images = set(effects.image_tokens(state, mystic))
    assert len(images) == 3

    uid, decision = play(state, foe, effects.LOCKDOWN)
    assert decision is not None
    assert {o["token"] for o in decision.options if "token" in o} == images, (
        "three images are three things to aim at"
    )
    assert {o.get("frame") for o in decision.options} == {None, ally.id}, (
        "the hidden frame itself is not offered; the visible one is"
    )

    answer(state, decision, {"token": sorted(images)[0]})
    assert mystic.statuses["slowed"] > 0 and mystic.statuses["stunned"] > 0, (
        "aimed at an image, landed on the frame -- even if that image was a fake"
    )
    assert effects.is_cloaked(state, mystic), "and it is still hiding"


def test_a_displaced_image_moves_on_its_own():
    """"Each image can be individually moved using say Displace."

    A decoy that is shoved goes by itself and the frame stays put, which gives
    nothing away: the pieces are indistinguishable, so shoving one tells the
    shover only that it shoved one.
    """
    state = make_state(width=20, height=20)
    state.phase = "action"
    mystic = add_frame(state, 0, "Hannael", Pos(4, 4))
    tactician = add_frame(state, 1, "Hector MkI", Pos(6, 4))
    play(state, mystic, effects.EPHEMERAL)
    real, fakes = _images(state, mystic)
    home = mystic.pos
    fake = fakes[0]
    where = fake.pos

    _uid, decision = play(state, tactician, effects.DISPLACE)
    assert decision is not None
    picked = next(o for o in decision.options if o.get("token") == fake.id)
    decision = answer(state, decision, picked)
    assert decision is not None, "then where it goes"
    spot = next(o for o in decision.options
                if (o["x"], o["y"]) != (where.x, where.y))
    answer(state, decision, spot)

    assert fake.pos == Pos(spot["x"], spot["y"]), "the image was displaced"
    assert mystic.pos == home, "the frame was not"
    assert real.pos == mystic.pos
    assert effects.is_cloaked(state, mystic)


def test_teleport_takes_every_image_with_it():
    """"When they use Teleport each image teleports, as they all use the
    abilities" -- asked once per image, the real one carrying the frame."""
    state = make_state(width=20, height=20)
    state.phase = "action"
    mystic = add_frame(state, 0, "Hannael", Pos(4, 4))
    play(state, mystic, effects.EPHEMERAL)
    real, _fakes = _images(state, mystic)
    before = {t: state.tokens[t].pos for t in effects.image_tokens(state, mystic)}

    _uid, decision = play(state, mystic, effects.TELEPORT)
    asked = 0
    far = [Pos(15, 15), Pos(16, 16), Pos(15, 16)]
    while decision is not None:
        asked += 1
        spot = next(o for o in decision.options
                    if Pos(o["x"], o["y"]) in far)
        decision = answer(state, decision, spot)
    assert asked == len(before), "one question per image"

    effects.sync_images(state)
    now = {t: state.tokens[t].pos for t in effects.image_tokens(state, mystic)}
    assert all(now[t] != before[t] for t in now), "all three went"
    assert mystic.pos == now[real.id], "the frame is under the real one"
    assert effects.is_cloaked(state, mystic)


def test_one_storm_is_placed_in_range_of_any_image():
    """"If they use storm one storm is placed in range of all of them."

    One token, and the "within 5" may be measured from whichever image suits.
    """
    state = make_state(width=20, height=20)
    state.phase = "action"
    mystic = add_frame(state, 0, "Hannael", Pos(3, 3))
    play(state, mystic, effects.EPHEMERAL)
    _real, fakes = _images(state, mystic)
    fakes[0].pos = Pos(14, 14)                      # one image walks off

    _uid, decision = play(state, mystic, effects.PSYCHIC_STORM)
    assert decision is not None
    tiles = {(o["x"], o["y"]) for o in decision.options}
    reach = effects.STORM_RADIUS
    assert any(state.board.distance(Pos(*t), fakes[0].pos) <= reach
               and state.board.distance(Pos(*t), mystic.pos) > reach
               for t in tiles), "in range of the far image and nothing else"

    spot = next(t for t in sorted(tiles)
                if state.board.distance(Pos(*t), fakes[0].pos) <= reach
                and state.board.distance(Pos(*t), mystic.pos) > reach)
    answer(state, decision, {"x": spot[0], "y": spot[1]})
    storms = [t for t in state.tokens.values() if t.kind == fx.STORM and t.alive]
    assert len(storms) == 1, "one storm, not one per image"
    assert storms[0].pos == Pos(*spot)


def test_a_blocked_attack_leaves_the_images_standing():
    """No damage was dealt, so no fake was shown up."""
    state = make_state(width=20, height=20)
    state.phase = "action"
    frame = add_frame(state, 0, "Kamikiri", Pos(2, 2))
    foe = add_frame(state, 1, "Hector MkI", Pos(3, 2))
    play(state, frame, effects.EPHEMERAL)
    _, fakes = _images(state, frame)
    fakes[0].pos = Pos(3, 1)

    give(state, foe, "Basic_Block")
    run_attack(state, frame, give(state, frame, "Basic_Punch"), foe)
    assert sum(foe.damage.values()) == 0, "the block held"
    assert all(f.alive for f in fakes)
    assert effects.is_cloaked(state, frame)


def test_one_image_left_is_no_disguise_at_all():
    state, frame, foe = duel(gap=1)
    play(state, frame, effects.EPHEMERAL)
    _, fakes = _images(state, frame)
    for fake in fakes:
        foe.pos = Pos(fake.pos.x + 1, fake.pos.y)
        run_attack(state, foe, give(state, foe, "Basic_Punch"), fake,
                   target_kind="token")
    effects.sync_images(state)
    assert not effects.is_cloaked(state, frame)
    assert not effects.image_tokens(state, frame)


def test_the_images_go_when_the_frame_does():
    from playtest.engine.state import destroy_frame

    state, frame, foe = duel(gap=4)
    play(state, frame, effects.EPHEMERAL)
    destroy_frame(state, frame)
    effects.sync_images(state)
    assert not [
        t for t in state.tokens.values() if t.kind == effects.IMAGE and t.alive
    ]


def test_teleport_repositions_the_frame_the_moment_it_resolves():
    """The card used to read "next turn ... at initiative 4"; it no longer does.

    So it is an ordinary effect step now, and nothing is owed afterwards.
    """
    state, frame, _ = duel()
    _uid, decision = play(state, frame, effects.TELEPORT)
    assert decision is not None, "the jump happens now"
    assert answer(state, decision, {"x": 8, "y": 8}) is None
    assert frame.pos == Pos(8, 8), "anywhere on the map"
    assert effects.followup_decision(state) is False, "nothing is owed later"


def test_utter_darkness_makes_everything_within_five_untargetable_next_turn():
    state = make_state()
    mystic = add_frame(state, 0, "Hannael", Pos(2, 2))
    ally = add_frame(state, 0, "Flamekin", Pos(3, 2))
    hunter = add_frame(state, 1, "Adam", Pos(4, 2))
    far = add_frame(state, 0, "Percival MkIV", Pos(9, 9))
    card = CATALOGUE["Spear_Thrust"]

    uid, _ = play(state, mystic, effects.UTTER_DARKNESS)
    assert not effects.is_untargetable(state, hunter, card, ally), "'next turn'"

    carry_over(state, uid)
    assert effects.is_untargetable(state, hunter, card, ally)
    assert effects.is_untargetable(state, hunter, card, mystic)
    assert not effects.is_untargetable(state, hunter, card, far)
    assert ally.id not in {
        o["id"] for o in combat.legal_targets(state, hunter, card)
    }


def test_encode_the_future_lets_every_ally_commit_from_its_deck():
    """"Next turn: allied frames choose cards from their deck" -- all of them.

    The card used to name one ally and ask which; it now names the side, so
    the effect arms every frame on it and asks nothing.
    """
    state = make_state()
    mystic = add_frame(state, 0, "Hannael", Pos(2, 2))
    ally = add_frame(state, 0, "Flamekin", Pos(3, 3))
    enemy = add_frame(state, 1, "Fenrir", Pos(8, 8))
    hand = give(state, ally, "Spear_Thrust", location="hand")
    deck = give(state, ally, "Halberd_Crush", location="deck")
    mine = give(state, mystic, "Spear_Thrust", location="hand")
    mine_deck = give(state, mystic, "Halberd_Crush", location="deck")
    theirs = give(state, enemy, "Spear_Thrust", location="hand")
    give(state, enemy, "Halberd_Crush", location="deck")

    uid, decision = play(state, mystic, effects.ENCODE)
    assert decision is None, "there is nothing left to choose"
    assert effects.commit_pool(state, ally) == [hand], "not this turn"

    carry_over(state, uid)
    assert set(effects.commit_pool(state, ally)) == {hand, deck}
    assert set(effects.commit_pool(state, mystic)) == {mine, mine_deck}, "itself too"
    assert effects.commit_pool(state, enemy) == [theirs], "allies only"

    carry_over(state)
    assert effects.commit_pool(state, ally) == [hand], "one turn only"


def test_psychic_storm_hurts_everything_standing_in_it_every_turn():
    state = make_state(width=20, height=20)
    mystic = add_frame(state, 0, "Hannael", Pos(2, 2))
    caught = add_frame(state, 1, "Hector MkI", Pos(5, 3))
    friendly = add_frame(state, 0, "Adam", Pos(4, 4))
    clear = add_frame(state, 1, "Fenrir", Pos(15, 15))

    _uid, decision = play(state, mystic, effects.PSYCHIC_STORM)
    assert decision is not None and decision.pick_kind == "place"
    assert answer(state, decision, {"x": 5, "y": 5}) is None

    effects.end_of_turn(state)
    assert caught.damage["High"] == 1
    assert friendly.damage["High"] == 1, "a storm does not pick sides"
    assert clear.damage["High"] == 0

    effects.end_of_turn(state)
    assert caught.damage["High"] == 2, "at the end of *each* turn"


def test_dooms_target_takes_it_next_turn_unless_it_runs():
    state = make_state()
    mystic = add_frame(state, 0, "Hannael", Pos(2, 2))
    marked = add_frame(state, 1, "Hector MkI", Pos(4, 2))

    _uid, decision = play(state, mystic, effects.DOOM)
    assert decision is None, "only one frame is in range"
    assert marked.statuses["dazed"] == 1

    effects.end_of_turn(state)
    assert marked.damage["High"] == 0, "it lands at the end of *next* turn"

    state.turn += 1
    effects.end_of_turn(state)
    assert marked.damage["High"] == effects.DOOM_DAMAGE


def test_a_doomed_frame_that_moves_far_enough_shakes_it_off():
    state = make_state()
    mystic = add_frame(state, 0, "Hannael", Pos(2, 2))
    marked = add_frame(state, 1, "Hector MkI", Pos(4, 2))
    play(state, mystic, effects.DOOM)

    state.turn += 1
    marked.turn_flags["moved_distance"] = effects.DOOM_ESCAPE + 1
    effects.end_of_turn(state)
    assert marked.damage["High"] == 0
    effects.end_of_turn(state)
    assert marked.damage["High"] == 0, "and it does not come back round"


# --------------------------------------------------------------------------
# Tactician
# --------------------------------------------------------------------------


def test_tactical_broadcast_makes_allies_in_range_lucid():
    state = make_state(width=20, height=20)
    tac = add_frame(state, 0, "Hector MkI", Pos(2, 2))
    ally = add_frame(state, 0, "Percival MkIV", Pos(7, 2))   # five away
    far = add_frame(state, 0, "Adam", Pos(19, 19))
    enemy = add_frame(state, 1, "Fenrir", Pos(3, 3))

    before = ally.draw_count
    play(state, tac, effects.BROADCAST)
    assert ally.statuses["lucid"] == 2
    assert ally.draw_count > before, "lucid means more cards"
    assert tac.statuses["lucid"] == 2, "the broadcaster is an allied frame too"
    assert far.statuses["lucid"] == 0
    assert enemy.statuses["lucid"] == 0


def test_fog_of_war_blocks_ranged_attacks_on_nearby_allies_next_turn():
    state = make_state(width=20, height=20)
    tac = add_frame(state, 0, "Hector MkI", Pos(2, 2))
    ally = add_frame(state, 0, "Percival MkIV", Pos(6, 2))
    exposed = add_frame(state, 0, "Adam", Pos(19, 19))
    sniper = add_frame(state, 1, "Fenrir", Pos(10, 2))

    ranged = CATALOGUE["Assault Rifle_From the hip"]
    melee = CATALOGUE["Spear_Thrust"]
    assert ranged.is_ranged and not melee.is_ranged

    uid, _ = play(state, tac, effects.FOG_OF_WAR)
    assert not effects.is_untargetable(state, sniper, ranged, ally), "'next turn'"

    carry_over(state, uid)
    assert effects.is_untargetable(state, sniper, ranged, ally)
    assert not effects.is_untargetable(state, sniper, melee, ally), "ranged only"
    assert not effects.is_untargetable(state, sniper, ranged, exposed), "out of 7"


def test_set_the_trap_moves_an_ally_and_reveals_enemies_around_it():
    """One ally to shove, so it goes straight to "where"."""
    state = make_state()
    tac = add_frame(state, 0, "Hector MkI", Pos(2, 2))
    ally = add_frame(state, 0, "Percival MkIV", Pos(4, 4))
    enemy = add_frame(state, 1, "Fenrir", Pos(6, 5))
    hidden = give(state, enemy, "Spear_Thrust")

    _uid, decision = play(state, tac, effects.SET_THE_TRAP)
    assert decision.pick_kind == "move", "the board draws it as movement"
    assert all("x" in o and "cost" in o for o in decision.options)
    assert answer(state, decision, {"x": 5, "y": 5}) is None
    assert ally.pos == Pos(5, 5)
    assert enemy.statuses["revealed"] > 0
    assert state.cards[hidden].face_down is False, "revealed turns actions face up"


def test_a_shove_asks_who_before_it_asks_where():
    """"Move a frame within N up to M" is two questions, not one long list.

    It used to be a single list of every (frame, destination) pair, which on
    this board is dozens of rows of raw coordinates and nothing the map can
    show. Who first -- a list of frames -- then that frame's own reachable
    tiles, which the board draws in the same green as a move.
    """
    state = make_state()
    tac = add_frame(state, 0, "Hector MkI", Pos(2, 2))
    near = add_frame(state, 0, "Percival MkIV", Pos(4, 4))
    far = add_frame(state, 0, "Kuwagata", Pos(3, 5))
    add_frame(state, 1, "Fenrir", Pos(9, 9))

    _uid, who = play(state, tac, effects.SET_THE_TRAP)
    assert {o["frame"] for o in who.options} == {near.id, far.id}
    assert not any("x" in o for o in who.options), "the tiles come after"

    where = answer(state, who, {"frame": far.id, "name": far.spec.name})
    assert where is not None and where.pick_kind == "move"
    assert all("x" in o and "y" in o and "cost" in o for o in where.options)
    assert far.pos not in [Pos(o["x"], o["y"]) for o in where.options], (
        "staying put is not a shove"
    )
    assert answer(state, where, {"x": 3, "y": 4}) is None
    assert far.pos == Pos(3, 4)
    assert near.pos == Pos(4, 4), "the frame that was not chosen did not move"


def test_outfox_reveals_and_dazes_a_frame_in_range():
    state = make_state(width=20, height=20)
    tac = add_frame(state, 0, "Hector MkI", Pos(2, 2))
    enemy = add_frame(state, 1, "Fenrir", Pos(7, 2))         # five away
    far = add_frame(state, 1, "Adam", Pos(19, 19))
    hidden = give(state, enemy, "Spear_Thrust")

    before = enemy.draw_count
    _uid, decision = play(state, tac, effects.OUTFOX)
    if decision is not None:
        answer(state, decision, {"frame": enemy.id})
    assert enemy.statuses["revealed"] == 3
    assert enemy.statuses["dazed"] == 2
    assert enemy.draw_count < before, "dazed means fewer cards"
    assert state.cards[hidden].face_down is False
    assert far.statuses["dazed"] == 0


def test_displace_reaches_five_and_throws_eight():
    """The two distances on the card are different and both are read."""
    state = make_state(width=20, height=20)
    tac = add_frame(state, 0, "Hector MkI", Pos(10, 10))
    enemy = add_frame(state, 1, "Fenrir", Pos(12, 10))
    near = add_frame(state, 1, "Kuwagata", Pos(10, 14))       # four away
    out_of_reach = add_frame(state, 1, "Adam", Pos(17, 10))   # seven away
    state.board.set_tile(Pos(11, 10), impassable=True)   # a wall between them

    _uid, who = play(state, tac, effects.DISPLACE)
    assert who is not None and who.pick_kind == "frame"
    assert {o["frame"] for o in who.options} == {enemy.id, near.id}, (
        "it can only grab what is within five"
    )
    where = answer(state, who, {"frame": enemy.id, "name": enemy.spec.name})
    assert where.pick_kind == "place", "it is put down, not walked"
    tiles = {Pos(o["x"], o["y"]) for o in where.options}
    assert Pos(2, 10) in tiles, "eight from the Tactician, wall or no wall"
    assert Pos(1, 10) not in tiles, "and no further"
    assert out_of_reach.pos not in tiles, "nor onto another frame"

    assert answer(state, where, {"x": 2, "y": 10}) is None
    assert enemy.pos == Pos(2, 10)


def test_ennervate_stims_every_ally_in_range():
    state = make_state(width=20, height=20)
    tac = add_frame(state, 0, "Hector MkI", Pos(2, 2))
    ally = add_frame(state, 0, "Adam", Pos(6, 5))
    far = add_frame(state, 0, "Kuwagata", Pos(19, 19))
    enemy = add_frame(state, 1, "Fenrir", Pos(3, 3))

    _uid, decision = play(state, tac, effects.ENNERVATE)
    assert decision is None
    assert tac.statuses["stimmed"] == 3, "including the frame that played it"
    assert ally.statuses["stimmed"] == 3
    assert far.statuses["stimmed"] == 0
    assert enemy.statuses["stimmed"] == 0


# --------------------------------------------------------------------------
# Wunderkid
# --------------------------------------------------------------------------


def test_hyper_allows_one_extra_action_next_turn_only():
    state, frame, _ = duel()
    uid, _ = play(state, frame, effects.HYPER)
    assert effects.actions_to_commit(state, frame) == 2, "not the turn it is played"
    carry_over(state, uid)
    assert effects.actions_to_commit(state, frame) == 3
    carry_over(state)
    move_card(state, uid, "discard")
    assert effects.actions_to_commit(state, frame) == 2


def test_net_speed_stims_and_boosts_this_frame():
    """The counts come off the card, so a balance edit needs no code change."""
    state, frame, _ = duel()
    card = CATALOGUE[effects.NET_SPEED]
    printed = dict(effects._parse_statuses(card.text))
    move, init = frame.base_movement, frame.initiative_mod
    play(state, frame, effects.NET_SPEED)
    assert frame.statuses["stimmed"] == printed["stimmed"] == 3
    assert frame.statuses["boosted"] == printed["boosted"] == 3
    assert frame.base_movement > move
    assert frame.initiative_mod > init
    assert "committed" in card.keywords, "it is spent when it resolves"


def test_portal_names_its_own_two_tiles_and_links_them():
    """"Create two portals within 7. Those tiles are connected."

    It used to be a movement rider -- a portal at each end of the step it was
    played with -- so the pair could only be known after the frame had walked.
    The card now picks both tiles itself, which is two decisions and no
    dependence on the move at all.
    """
    state = make_state(width=12, height=12)
    state.phase = "action"
    frame = add_frame(state, 0, "Kuwagata", Pos(1, 1))
    uid = give(state, frame, effects.PORTAL)
    state.resolution = Resolution(frame_id=frame.id, uid=uid, steps=[])

    first = effects.resolve_effect(state, frame, uid)
    assert first is not None, "the card asks where the near end goes"
    reach = max(state.board.distance(frame.pos, Pos(o["x"], o["y"]))
                for o in first.options)
    assert reach == 7, "'within 7' is read off the card"

    second = answer(state, first, {"x": 6, "y": 1})
    assert second is not None, "and then where the far end goes"
    assert not any(o["x"] == 6 and o["y"] == 1 for o in second.options), (
        "a portal cannot link a tile to itself"
    )
    assert not [t for t in state.tokens.values() if t.kind == fx.PORTAL], (
        "neither end exists until the pair is complete"
    )
    assert answer(state, second, {"x": 1, "y": 3}) is None

    portals = [t for t in state.tokens.values() if t.kind == fx.PORTAL]
    assert {t.pos for t in portals} == {Pos(6, 1), Pos(1, 3)}


def test_a_portal_pair_shortens_the_trip():
    state = make_state(width=12, height=12)
    state.phase = "action"
    frame = add_frame(state, 0, "Kuwagata", Pos(6, 1))
    uid = give(state, frame, effects.PORTAL)
    state.resolution = Resolution(frame_id=frame.id, uid=uid, steps=[])
    answer(state, answer(state, effects.resolve_effect(state, frame, uid),
                         {"x": 1, "y": 1}), {"x": 7, "y": 1})

    # One step onto the near end, one more through the link, and you are out
    # the far side five tiles away with a step left to spend.
    plain = [{"x": 6, "y": 1, "cost": 0}, {"x": 7, "y": 1, "cost": 1}]
    options = effects.adjust_move_options(state, frame, 3, plain)
    costs = {(o["x"], o["y"]): o["cost"] for o in options}
    assert costs[(1, 1)] == 2, "step on, step through"
    assert (0, 0) in costs, "and you keep going from the far side"
    assert costs[(0, 0)] == 3


def test_a_move_no_longer_creates_portals_by_itself():
    """The old card built its pair in `after_move`; nothing does now."""
    state = make_state(width=12, height=12)
    state.phase = "action"
    frame = add_frame(state, 0, "Kuwagata", Pos(1, 1))
    uid = give(state, frame, effects.PORTAL)
    state.resolution = Resolution(frame_id=frame.id, uid=uid, steps=[])
    effects.after_move(state, frame, Pos(1, 1), Pos(3, 1))
    effects.after_move(state, frame, Pos(3, 1), Pos(5, 1))
    assert not [t for t in state.tokens.values() if t.kind == fx.PORTAL]


def test_ace_reflexes_moves_the_frame_after_it_is_attacked():
    state, attacker, defender = duel()
    uid, _ = play(state, defender, effects.ACE_REFLEXES)
    # Ace Reflexes itself blocks High, so the swing has to come in somewhere
    # else or the attack it is meant to react to never lands.
    kick = give(state, attacker, "Basic_Kick")           # Low: nothing blocks it
    run_attack(state, attacker, kick, defender)

    assert defender.damage["Low"] > 0, "the attack still lands"
    assert defender.turn_flags["reflex_moves"] == 1
    assert effects.followup_decision(state) is True
    decision = state.pending
    assert decision.seat == defender.seat
    start = defender.pos
    assert answer(state, decision, {"x": start.x, "y": start.y + 2}) is None
    assert defender.pos == Pos(start.x, start.y + 2)
    assert effects.followup_decision(state) is False, "one move per attack"


def test_ace_reflexes_does_nothing_for_a_frame_that_did_not_play_it():
    state, attacker, defender = duel()
    crush = give(state, attacker, "Halberd_Crush")
    run_attack(state, attacker, crush, defender)
    assert not defender.turn_flags.get("reflex_moves")


def test_parallel_action_redraws_and_swaps_the_next_action():
    state, kid, _foe = duel()
    spare = give(state, kid, "Halberd_Crush", location="deck")
    for other in ("Sword_Lunge", "Sword_Feint", "Axe_Chop"):
        give(state, kid, other, location="deck")

    card, decision = play(state, kid, effects.PARALLEL_ACTION)
    assert decision is None, "it arms; it does not ask yet"

    carry_over(state, card)                        # "Next turn:"
    keep = give(state, kid, "Spear_Thrust")
    swap = give(state, kid, "Spear_Jab")
    for uid in (keep, swap):
        state.cards[uid].face_down = True

    kid.turn_flags["parallel_now"] = True          # as if it had been attacked
    assert effects.followup_decision(state) is True
    out = state.pending
    state.pending = None
    assert {o.get("uid") for o in out.options} >= {keep, swap}
    assert any(o.get("done") for o in out.options), "swapping is optional"

    into = answer(state, out, {"uid": swap, "key": "Spear_Jab", "swap": "out"})
    assert spare in {o["uid"] for o in into.options}, "the fresh hand"
    nxt = answer(state, into, {"uid": spare, "key": "Halberd_Crush", "swap": "in"})

    assert state.cards[swap].location == "discard"
    assert spare in kid.committed and state.cards[spare].location == "committed"
    assert state.cards[spare].face_down is True

    assert answer(state, nxt, {"done": True}) is None
    assert kid.hand == [], "the rest of the extra hand goes"
    assert state.cards[card].location == "discard", "and so does the card"


def test_parallel_action_waits_for_next_turn_so_it_cannot_be_burnt():
    """"Next turn:" -- otherwise the enemy takes it off you by swinging once.

    Played, then attacked on the same turn, the redraw would fire with nothing
    left worth changing, and the card would be spent for nothing.
    """
    state, kid, foe = duel()
    give(state, kid, "Spear_Thrust")
    give(state, kid, "Halberd_Crush", location="deck")
    card, _ = play(state, kid, effects.PARALLEL_ACTION)
    state.phase = "action"

    effects.after_attacked(state, kid, foe)
    assert not kid.turn_flags.get("parallel_now"), "not on the turn it resolved"
    assert effects.followup_decision(state) is False

    carry_over(state, card)
    state.phase = "action"
    assert effects.followup_decision(state) is True, "its own action is next"
    assert state.pending.frame_id == kid.id


def test_being_attacked_sets_off_the_swap():
    """"...or is attacked" -- through the real attack pipeline, not a flag set
    by hand, because that is the path a playtester actually takes."""
    state, kid, foe = duel()
    card, _ = play(state, kid, effects.PARALLEL_ACTION)
    carry_over(state, card)
    state.phase = "action"
    give(state, kid, "Spear_Thrust")           # spent blocking, compulsorily
    give(state, kid, "Axe_Chop")               # and this one is left to swap
    give(state, kid, "Halberd_Crush", location="deck")
    give(state, kid, "Sword_Lunge", location="deck")

    run_attack(state, foe, give(state, foe, "Spear_Thrust"), kid)
    assert kid.turn_flags.get("parallel_now"), "being hit arms it"
    assert effects.followup_decision(state) is True
    assert state.pending.frame_id == kid.id
    assert any(o.get("swap") == "out" for o in state.pending.options)


def test_a_swap_can_be_taken_back():
    state, kid, _foe = duel()
    card, _ = play(state, kid, effects.PARALLEL_ACTION)
    carry_over(state, card)
    mine = give(state, kid, "Spear_Thrust")
    state.cards[mine].face_down = True
    spare = give(state, kid, "Halberd_Crush", location="deck")

    kid.turn_flags["parallel_now"] = True
    assert effects.followup_decision(state) is True
    out, state.pending = state.pending, None
    into = answer(state, out, {"uid": mine, "key": "Spear_Thrust", "swap": "out"})
    assert any(o.get("swap") == "keep" for o in into.options), (
        "the card being dropped is offered back"
    )
    assert answer(state, into, {"uid": mine, "key": "Spear_Thrust",
                                "swap": "keep"}) is None
    assert state.cards[mine].location == "committed", "kept, not discarded"
    assert state.cards[spare].location == "discard", "the extra hand still goes"


def test_a_swapped_in_card_resolves_at_its_own_initiative():
    """Swap in something faster and it is what happens next.

    The swap runs from `followup_decision`, which the driver calls *before* it
    picks the next card, so the new action joins the queue in time to be that
    card rather than waiting a beat.
    """
    from playtest.engine import resolve as R

    state, kid, foe = duel()
    card, _ = play(state, kid, effects.PARALLEL_ACTION)
    carry_over(state, card)
    slow = give(state, kid, "Halberd_Crush")             # initiative 3
    state.cards[slow].face_down = True
    give(state, foe, "Sword_Slice")                      # initiative 5
    fast = give(state, kid, "Bruiser_Intimidate", location="deck")   # initiative 8
    state.phase = "action"

    assert R.next_actor(state)[0].id == foe.id, "the enemy is faster to begin with"

    kid.turn_flags["parallel_now"] = True
    assert effects.followup_decision(state) is True
    out, state.pending = state.pending, None
    into = answer(state, out, {"uid": slow, "key": "Halberd_Crush", "swap": "out"})
    nxt = answer(state, into,
                 {"uid": fast, "key": "Bruiser_Intimidate", "swap": "in"})
    if nxt is not None:
        answer(state, nxt, {"done": True})

    actor = R.next_actor(state)
    assert actor is not None and actor[1] == fast, "the faster card goes next"


def test_looking_at_the_queue_does_not_advance_the_tie_alternation():
    """`peek_actor` must put back what `next_actors` moves on."""
    from playtest.engine import resolve as R

    state, mine, theirs = duel()
    give(state, mine, "Spear_Thrust")
    give(state, theirs, "Spear_Thrust")
    state.phase = "action"
    R.next_actors(state)
    before = (state.tie_value, state.tie_index)
    R.peek_actor(state)
    assert (state.tie_value, state.tie_index) == before


def test_parallel_action_expires_with_the_card():
    state, kid, foe = duel()
    give(state, kid, "Spear_Thrust")
    give(state, kid, "Halberd_Crush", location="deck")
    card, _ = play(state, kid, effects.PARALLEL_ACTION)
    carry_over(state, card)
    from playtest.engine.state import discard_card

    discard_card(state, card)                      # as cleanup does when it runs out
    state.phase = "action"
    effects.after_attacked(state, kid, foe)
    assert effects.followup_decision(state) is False


def test_showboating_forces_the_enemy_to_swing_at_it_and_keeps_its_blocks():
    state = make_state()
    kid = add_frame(state, 0, "Kuwagata", Pos(4, 4))
    mate = add_frame(state, 0, "Adam", Pos(5, 5))
    foe = add_frame(state, 1, "Hector MkI", Pos(5, 4))
    card = CATALOGUE["Spear_Thrust"]

    before = {o["id"] for o in combat.legal_targets(state, foe, card)}
    assert {kid.id, mate.id} <= before

    play(state, kid, effects.SHOWBOATING)
    after = combat.legal_targets(state, foe, card)
    assert [o["id"] for o in after] == [kid.id], "it must be attacked"

    block = give(state, kid, "Spear_Thrust")
    attack = combat.declare_attack(
        state, foe, give(state, foe, "Spear_Thrust"),
        target_kind="frame", target_id=kid.id,
    )
    combat.apply_block(state, kid, attack, block, ["Mid"])
    assert state.cards[block].location == "committed", "the block is kept"


# --------------------------------------------------------------------------
# Engineer
# --------------------------------------------------------------------------


def test_battlefield_repairs_heals_an_ally_within_three():
    state = make_state()
    engineer = add_frame(state, 0, "Percival MkIV", Pos(2, 2))
    hurt = add_frame(state, 0, "Hector MkI", Pos(4, 2))
    also = add_frame(state, 0, "Adam", Pos(3, 3))
    far = add_frame(state, 0, "Fenrir", Pos(9, 9))
    hurt.damage["Mid"] = 3
    also.damage["Mid"] = 1
    far.damage["Mid"] = 3

    _uid, decision = play(state, engineer, effects.REPAIRS)
    assert {o["frame"] for o in decision.options} == {hurt.id, also.id}
    assert decision.options[0]["frame"] == hurt.id, "most damaged first"
    assert answer(state, decision, {"frame": hurt.id}) is None
    assert hurt.damage["Mid"] == 0
    assert far.damage["Mid"] == 3, "out of range"


def test_barricade_makes_up_to_three_tiles_impassable():
    state = make_state()
    engineer = add_frame(state, 0, "Percival MkIV", Pos(4, 4))
    _uid, decision = play(state, engineer, effects.BARRICADE)

    placed = []
    for _ in range(effects.BARRICADE_COUNT):
        assert decision is not None
        pick = next(o for o in decision.options if "x" in o)
        placed.append(Pos(pick["x"], pick["y"]))
        decision = answer(state, decision, pick)
    assert decision is None, "three is the limit"

    tokens = [t for t in state.tokens.values() if t.kind == fx.BARRICADE]
    assert len(tokens) == 3
    assert {t.pos for t in tokens} == set(placed)
    assert set(placed) <= state.occupied(), "barricaded tiles cannot be entered"
    for pos in placed:
        assert state.board.distance(engineer.pos, pos) <= 3


def test_barricade_can_be_stopped_early():
    state = make_state()
    engineer = add_frame(state, 0, "Percival MkIV", Pos(4, 4))
    _uid, decision = play(state, engineer, effects.BARRICADE)
    assert answer(state, decision, {"done": True}) is None
    assert not [t for t in state.tokens.values() if t.kind == fx.BARRICADE]


def test_gravity_well_taxes_every_step_away_from_it():
    state = make_state()
    engineer = add_frame(state, 0, "Percival MkIV", Pos(4, 4))
    _uid, decision = play(state, engineer, effects.GRAVITY_WELL)
    assert answer(state, decision, {"x": 4, "y": 5}) is None
    wells = [t for t in state.tokens.values() if t.kind == fx.GRAVITY_WELL]
    assert len(wells) == 1 and wells[0].pos == Pos(4, 5)

    # Two steps straight away from the well cost an extra 1 per step.
    plain = [
        {"x": 4, "y": 3, "cost": 1},
        {"x": 4, "y": 2, "cost": 2},
        {"x": 5, "y": 4, "cost": 1},
    ]
    options = effects.adjust_move_options(state, engineer, 4, plain)
    costs = {(o["x"], o["y"]): o["cost"] for o in options}
    assert costs[(4, 3)] == 2, "one step away costs 1 extra"
    assert costs[(4, 2)] == 4, "two steps away cost 2 extra"
    assert costs[(5, 4)] == 1, "moving around it is free"

    # And a destination that no longer fits the budget drops out of the offer.
    tight = effects.adjust_move_options(state, engineer, 2, plain)
    assert (4, 2) not in {(o["x"], o["y"]) for o in tight}


def test_precision_tuning_boosts_this_frame():
    state, frame, _ = duel()
    before = frame.base_movement
    play(state, frame, effects.PRECISION_TUNING)
    assert frame.statuses["boosted"] == 4
    assert frame.base_movement > before


def test_system_override_hands_the_next_move_to_the_other_seat():
    state = make_state()
    eng = add_frame(state, 0, "Percival MkIV", Pos(2, 2))
    enemy = add_frame(state, 1, "Hector MkI", Pos(6, 2))      # four away
    add_frame(state, 1, "Fenrir", Pos(9, 9))                  # out of range

    _uid, decision = play(state, eng, effects.SYSTEM_OVERRIDE)
    assert decision is None, "only one frame is within four"
    assert effects.move_chooser(state, enemy) == eng.seat
    assert effects.move_chooser(state, enemy) == enemy.seat, "once only"


def test_sensory_overload_dazes_stuns_and_shortens_the_targets_range():
    state = make_state()
    eng = add_frame(state, 0, "Percival MkIV", Pos(2, 2))
    enemy = add_frame(state, 1, "J7R-Salaryman", Pos(8, 2))   # six away
    card = CATALOGUE["Railgun_Snipe"]                       # range 12
    assert combat.effective_range(state, enemy, card, "High") > 4

    _uid, decision = play(state, eng, effects.SENSORY_OVERLOAD)
    assert decision is None
    assert enemy.statuses["dazed"] == 2
    assert enemy.statuses["stunned"] == 3
    assert combat.effective_range(state, enemy, card, "High") == 2, (
        "a cap, not a modifier -- the frame's +4 cannot buy it back"
    )


# --------------------------------------------------------------------------
# Specialist
# --------------------------------------------------------------------------


def test_combo_strike_adds_a_second_attack_from_the_same_weapon():
    state, attacker, defender = duel()
    play(state, attacker, effects.COMBO_STRIKE)

    spare = give(state, attacker, "Halberd_Crush", location="deck")
    give(state, attacker, "Spear_Thrust", location="deck")      # wrong weapon
    swing = give(state, attacker, "Halberd_Eviscerate")
    card = CATALOGUE["Halberd_Eviscerate"]
    assert effects.has_effect_step(card, state, attacker), "the rider forces a step"

    state.resolution = Resolution(frame_id=attacker.id, uid=swing, steps=["attack"])
    decision = effects.resolve_effect(state, attacker, swing)
    assert {o.get("uid") for o in decision.options} == {spare, None}, (
        "only same-weapon attacks from the top 4, plus a way to decline"
    )
    assert answer(state, decision, {"uid": spare}) is None
    assert state.cards[spare].location == "discard"

    bonus, spread = effects.attack_damage_bonus(state, attacker, card, defender.id)
    combo = CATALOGUE["Halberd_Crush"]
    assert bonus == {z: n for z, n in combo.attacks.items() if n}, (
        "Halberd_Crush's attack is added, in its own zone"
    )
    assert spread == 0, "a combo names its zones; it is not a flat +N"
    assert effects.attack_damage_bonus(state, attacker, card, defender.id) == ({}, 0), (
        "and only once"
    )


def test_combo_strike_rides_every_attack_until_the_end_of_next_turn():
    """"Until the end of next turn: when resolving attacks from this frame..."

    The card in play is the whole state, so it rides each attack in turn --
    including one whose attack step the controller already ran, which simply
    has nothing to add to and does not spend the card.
    """
    state, attacker, _ = duel()
    uid, _ = play(state, attacker, effects.COMBO_STRIKE)
    give(state, attacker, "Halberd_Crush", location="deck")

    early = give(state, attacker, "Halberd_Eviscerate")
    state.resolution = Resolution(frame_id=attacker.id, uid=early, steps=[])
    assert effects.resolve_effect(state, attacker, early) is None, (
        "the attack already happened: nothing to add to"
    )

    first = give(state, attacker, "Halberd_Sweep")
    state.resolution = Resolution(frame_id=attacker.id, uid=first, steps=["attack"])
    assert effects.resolve_effect(state, attacker, first) is not None

    carry_over(state, uid)
    give(state, attacker, "Halberd_Crush", location="deck")
    later = give(state, attacker, "Halberd_Sweep")
    state.resolution = Resolution(frame_id=attacker.id, uid=later, steps=["attack"])
    assert effects.resolve_effect(state, attacker, later) is not None, "next turn too"


def test_snipers_aim_extends_range_ignores_obstacles_and_adds_damage():
    state = make_state(width=20, height=20)
    state.phase = "action"
    sniper = add_frame(state, 0, "Hector MkI", Pos(2, 2))
    target = add_frame(state, 1, "Fenrir", Pos(12, 2))
    card = CATALOGUE["Assault Rifle_From the hip"]          # Mid, range 6

    before = combat.effective_range(state, sniper, card, "Mid", target)
    assert not effects.ignores_obstacles(state, sniper)
    play(state, sniper, effects.SNIPERS_AIM)

    assert combat.effective_range(state, sniper, card, "Mid", target) == before + 4
    assert effects.ignores_obstacles(state, sniper)
    assert effects.attack_damage_bonus(state, sniper, card, target.id) == ({}, 1), (
        "no zone named, so it spreads over every zone the attack applies to"
    )
    melee = CATALOGUE["Spear_Thrust"]
    assert effects.attack_damage_bonus(state, sniper, melee, target.id) == ({}, 0), (
        "ranged attacks only"
    )


def test_master_duelist_reveals_melee_targets_and_takes_over_their_blocks():
    state, attacker, defender = duel()
    uid, _ = play(state, attacker, effects.MASTER_DUELIST)
    hidden = give(state, defender, "Spear_Thrust")

    spear = give(state, attacker, "Spear_Thrust")
    attack = combat.declare_attack(
        state, attacker, spear, target_kind="frame", target_id=defender.id
    )
    assert defender.statuses["revealed"] > 0
    assert state.cards[hidden].face_down is False
    assert effects.block_chooser(state, defender, attack) == attacker.seat

    ranged = give(state, attacker, "Assault Rifle_From the hip")
    far = add_frame(state, 1, "Adam", Pos(6, 2))
    shot = combat.declare_attack(
        state, attacker, ranged, target_kind="frame", target_id=far.id
    )
    assert effects.block_chooser(state, far, shot) == far.seat, "melee only"

    carry_over(state, uid)
    assert fx.card_active(state, attacker, effects.MASTER_DUELIST), "3 turns"


def test_practiced_technique_stacks_damage_across_one_weapon_next_turn():
    state, attacker, defender = duel()
    uid, _ = play(state, attacker, effects.PRACTICED)
    assert attacker.statuses["lucid"] > 0, "the printed status applies at once"

    card = CATALOGUE["Halberd_Eviscerate"]
    give(state, attacker, "Halberd_Eviscerate")
    assert effects.attack_damage_bonus(state, attacker, card, defender.id) == ({}, 0), (
        "'next turn'"
    )

    carry_over(state, uid)

    def completed(key):
        """A card of `key` that has already resolved this turn."""
        state.cards[give(state, attacker, key)].resolved = True

    give(state, attacker, "Halberd_Crush")
    assert effects.attack_damage_bonus(state, attacker, card, defender.id) == ({}, 0), (
        "'each other completed attack' -- one still face down is not completed"
    )
    completed("Halberd_Crush")
    assert effects.attack_damage_bonus(state, attacker, card, defender.id) == ({}, 1)
    completed("Halberd_Sweep")
    assert effects.attack_damage_bonus(state, attacker, card, defender.id) == ({}, 2)
    completed("Spear_Thrust")
    assert effects.attack_damage_bonus(state, attacker, card, defender.id) == ({}, 2), (
        "another weapon adds nothing"
    )


def test_rebound_lends_the_frame_sight_of_what_it_cannot_see():
    state = make_state()
    spec = add_frame(state, 0, "J7R-Salaryman", Pos(2, 2))
    enemy = add_frame(state, 1, "Hector MkI", Pos(8, 2))

    _uid, decision = play(state, spec, effects.REBOUND)
    assert decision is not None and decision.pick_kind == "place"
    assert answer(state, decision, {"x": 6, "y": 4}) is None

    # The frame can see its own mirror but nothing beyond it.
    seen = {Pos(6, 4)}
    state.board.has_line_of_sight = (
        lambda a, b, **kw: b in seen
    )
    card = CATALOGUE["Railgun_Snipe"]
    assert combat.can_target(state, spec, card, enemy.pos, enemy), (
        "four from the mirror, so the mirror sees it"
    )
    enemy.pos = Pos(8, 9)
    assert not combat.can_target(state, spec, card, enemy.pos, enemy)


def test_a_rebound_belongs_to_the_frame_that_put_it_down():
    state = make_state()
    spec = add_frame(state, 0, "J7R-Salaryman", Pos(2, 2))
    mate = add_frame(state, 0, "Adam", Pos(3, 2))
    _uid, decision = play(state, spec, effects.REBOUND)
    answer(state, decision, {"x": 6, "y": 4})
    assert effects.rebound_sight(state, spec, Pos(7, 5))
    assert not effects.rebound_sight(state, mate, Pos(7, 5))


def test_cage_fight_walls_both_fighters_in_and_pushes_bystanders_out():
    state = make_state()
    spec = add_frame(state, 0, "Kamikiri", Pos(5, 5))
    foe = add_frame(state, 1, "Hector MkI", Pos(6, 5))
    bystander = add_frame(state, 0, "Adam", Pos(3, 5))       # on the wall line

    _uid, decision = play(state, spec, effects.CAGE_FIGHT)
    assert decision is None, "only one enemy is within 2"

    walls = {t.pos for t in state.tokens.values()
             if t.kind == fx.CAGE and t.alive}
    assert Pos(3, 5) in walls or bystander.pos != Pos(3, 5)
    assert bystander.pos not in walls, "it was pushed outside"
    assert state.board.distance(Pos(5, 5), bystander.pos) > 2
    assert spec.pos in {Pos(5, 5)} and foe.pos == Pos(6, 5), "the fighters stay"
    assert all(state.board.distance(Pos(5, 5), p) == 2 for p in walls)
    assert Pos(5, 5) in state.occupied() and next(iter(walls)) in state.occupied()


def test_the_cage_comes_down_when_a_fighter_leaves_it():
    state = make_state()
    spec = add_frame(state, 0, "Kamikiri", Pos(5, 5))
    foe = add_frame(state, 1, "Hector MkI", Pos(6, 5))
    uid, _ = play(state, spec, effects.CAGE_FIGHT)
    assert any(t.kind == fx.CAGE and t.alive for t in state.tokens.values())

    foe.pos = Pos(9, 9)                    # teleported, knocked back, whatever
    effects.sync_cages(state)
    assert not any(t.kind == fx.CAGE and t.alive for t in state.tokens.values())
    assert state.cards[uid].location == "discard", "the card goes with it"


# --------------------------------------------------------------------------
# Drones
# --------------------------------------------------------------------------


def test_summoning_a_drone_puts_a_token_out_and_the_frame_does_not_swing():
    state, frame, _ = duel(gap=4)
    card = CATALOGUE["Swarm_Swarm"]
    assert card.is_attack, "the card prints an attack"
    assert effects.delegates_attack(card), "but the drone makes it, not the frame"

    res = Resolution(frame_id=frame.id, uid="", steps=["effect", "attack"])
    state.resolution = res
    uid = give(state, frame, "Swarm_Swarm")
    res.uid = uid
    decision = effects.resolve_effect(state, frame, uid)
    assert res.steps == ["effect"], "the frame's attack step is dropped"
    assert decision is not None, "the controller chooses where it lands"
    assert all(
        state.board.distance(frame.pos, Pos(o["x"], o["y"])) == 1
        for o in decision.options
    ), "'Summon one Swarm' says nothing about range, so it lands beside you"
    assert answer(state, decision, {"x": 2, "y": 3}) is None

    drones = [t for t in state.tokens.values() if t.kind == fx.DRONE]
    assert len(drones) == 1
    drone = drones[0]
    assert drone.hp == drone.max_hp == card.drone_health
    assert drone.owner == frame.seat
    assert drone.pos == Pos(2, 3)


def test_the_drone_moves_and_attacks_every_turn():
    state, frame, enemy = duel(gap=4)
    state.resolution = None
    uid = summon(state, frame, "Swarm_Swarm")
    drone = next(t for t in state.tokens.values() if t.kind == fx.DRONE)
    drone.pos = Pos(4, 2)

    # It acts on the turn it is summoned.
    assert effects.followup_decision(state) is True
    decision = state.pending
    assert decision.kind == "effect_choice" and decision.seat == frame.seat
    beside = Pos(enemy.pos.x - 1, enemy.pos.y)
    assert answer(state, decision, {"x": beside.x, "y": beside.y}) is None
    assert drone.pos == beside
    assert enemy.damage["Mid"] == 1, "the drone attacks with the card's profile"
    assert effects.followup_decision(state) is False, "once per turn"

    # And again next turn.
    carry_over(state, uid)
    enemy.damage["Mid"] = 0
    assert effects.followup_decision(state) is True
    assert answer(state, state.pending, {"x": beside.x, "y": beside.y}) is None
    assert enemy.damage["Mid"] == 1


def test_a_drones_attack_can_be_blocked_but_the_drone_itself_never_blocks():
    state, frame, enemy = duel(gap=4)
    summon(state, frame, "Swarm_Swarm")
    drone = next(t for t in state.tokens.values() if t.kind == fx.DRONE)
    drone.pos = Pos(4, 2)
    guard = give(state, enemy, "Spear_Thrust")             # blocks Mid
    state.cards[guard].init_index = 1        # already acted; it still blocks

    assert effects.followup_decision(state) is True
    beside = Pos(enemy.pos.x - 1, enemy.pos.y)
    block = answer(state, state.pending, {"x": beside.x, "y": beside.y})
    assert block is not None and block.seat == enemy.seat, "blocking is compulsory"
    assert {o["uid"] for o in block.options} == {guard}
    assert answer(state, block, {"uid": guard}) is None
    assert enemy.damage["Mid"] == 0, "the block held"
    assert state.cards[guard].face_down is False
    assert enemy.turn_flags.get("hector_block_used"), (
        "the drone's attack goes through the ordinary block rules, frame "
        "abilities included -- Hector keeps its first block of the turn"
    )


def test_catching_a_drones_attack_does_not_daze_the_frame_that_sent_it():
    """"On Block: attacker gets Dazed" -- and the attacker is the drone.

    Dazing a machine on the far side of the board because its owner's card is
    the one being resolved is not what the card says. A token carries no
    statuses, so the debuff simply fizzles.
    """
    state, frame, enemy = duel(gap=4)
    summon(state, frame, "Swarm_Swarm")
    drone = next(t for t in state.tokens.values() if t.kind == fx.DRONE)
    drone.pos = Pos(4, 2)
    catch = give(state, enemy, "Chain_Catch")              # blocks High/Mid
    state.cards[catch].init_index = 1

    assert effects.followup_decision(state) is True
    beside = Pos(enemy.pos.x - 1, enemy.pos.y)
    block = answer(state, state.pending, {"x": beside.x, "y": beside.y})
    assert answer(state, block, {"uid": catch}) is None
    assert frame.statuses["dazed"] == 0, "the summoner is not the attacker"


def test_a_parry_against_a_drone_hits_the_drone():
    """The other On Block rider deals damage back, and it lands on the drone."""
    state, frame, enemy = duel(gap=4)
    summon(state, frame, "Swarm_Swarm")
    drone = next(t for t in state.tokens.values() if t.kind == fx.DRONE)
    drone.pos = Pos(4, 2)
    before = drone.hp
    parry = give(state, enemy, "Sword_Parry")
    state.cards[parry].init_index = 1

    assert effects.followup_decision(state) is True
    beside = Pos(enemy.pos.x - 1, enemy.pos.y)
    block = answer(state, state.pending, {"x": beside.x, "y": beside.y})
    assert answer(state, block, {"uid": parry}) is None
    assert sum(frame.damage.values()) == 0, "not the summoner"
    assert drone.hp == before - 1, "the drone takes it"


def test_a_token_shot_out_of_a_carrier_drops_toward_the_drone():
    """The damage is the summoner's -- it takes the kill -- but it comes from
    the drone, and a carried token drops toward the muzzle."""
    from playtest.engine import objectives as O
    from playtest.engine.state import TokenState

    state = make_state(width=20, height=20)
    state.phase = "action"
    frame = add_frame(state, 0, "Kamikiri", Pos(2, 10))
    enemy = add_frame(state, 1, "Hector MkI", Pos(10, 10))
    summon(state, frame, "Gun Tower_Gun Tower 1", Pos(3, 10))
    drone = next(t for t in state.tokens.values() if t.kind == fx.DRONE)
    # The far side of the enemy, at the tower's printed reach: the point is
    # which way the token falls, so the shot has to actually connect.
    reach = CATALOGUE["Gun Tower_Gun Tower 1"].ranges["High"]
    drone.pos = Pos(enemy.pos.x + reach, 10)
    shiny = TokenState(id="s", kind="shiny", pos=enemy.pos, carriable=True,
                       objective="Shiny Thing", carrier=enemy.id)
    state.tokens[shiny.id] = shiny
    state.objectives.append(
        O.ObjectiveState(name="Shiny Thing", owner=1, defend=1, attack=1))

    record = fx.slot(state, "drones")[drone.id]
    record["acted"] = 0
    # The gun tower cannot move and has one target, so its whole turn runs
    # without a decision -- `followup_decision` parks nothing and says so.
    assert effects.followup_decision(state) is False
    while state.pending is not None:
        answer(state, state.pending)
    assert enemy.damage["High"] or enemy.damage["Mid"] or enemy.damage["Low"]
    assert shiny.carrier is None, "the hit knocked it loose"
    assert shiny.pos.x > enemy.pos.x, (
        "toward the drone that fired, not the frame that owns it"
    )


def test_a_drone_shoots_an_objective():
    """"Tokens can attack objectives" -- a reactor is a target like any other."""
    from playtest.engine.state import TokenState

    state = make_state()
    frame = add_frame(state, 0, "Kuwagata", Pos(2, 2))
    add_frame(state, 1, "Hector MkI", Pos(9, 9))          # far away, no frames near
    reactor = TokenState(id="r", kind="reactor", pos=Pos(3, 2), hp=2, max_hp=2,
                         owner=1, objective="Power Reactors")
    state.tokens[reactor.id] = reactor

    summon(state, frame, "Swarm_Swarm", Pos(2, 3))
    token_id = next(t.id for t in state.tokens.values() if t.kind == fx.DRONE)
    record = fx.slot(state, "drones")[token_id]
    record["acted"] = 0
    state.tokens[token_id].pos = Pos(3, 3)                 # adjacent to the reactor

    options = effects._drone_options(
        state, token_id, CATALOGUE["Swarm_Swarm"], frame)
    assert any(o.get("token") == reactor.id for o in options), options
    effects._drone_fire(state, token_id, record, {"token": reactor.id})
    assert reactor.hp < 2, "the drone shot it"


def test_a_drone_will_not_shoot_its_own_sides_objective():
    from playtest.engine.state import TokenState

    state = make_state()
    frame = add_frame(state, 0, "Kuwagata", Pos(2, 2))
    add_frame(state, 1, "Hector MkI", Pos(9, 9))
    mine = TokenState(id="r", kind="reactor", pos=Pos(3, 2), hp=2, max_hp=2,
                      owner=0, objective="Power Reactors")
    state.tokens[mine.id] = mine
    summon(state, frame, "Swarm_Swarm", Pos(3, 3))
    token_id = next(t.id for t in state.tokens.values() if t.kind == fx.DRONE)
    options = effects._drone_options(
        state, token_id, CATALOGUE["Swarm_Swarm"], frame)
    assert not any(o.get("token") == mine.id for o in options)


def test_a_drone_card_stops_blocking_once_it_has_resolved():
    """"Any blocks marked on the card that summoned the drone only apply
    before the card resolves" (rules.tex Drones)."""
    state = make_state()
    frame = add_frame(state, 0, "Kuwagata", Pos(2, 2))
    foe = add_frame(state, 1, "Hector MkI", Pos(3, 2))
    uid = give(state, frame, "Swarm_Swarm")
    assert CATALOGUE["Swarm_Swarm"].block_zones, "the card does print a block"

    attack = combat.declare_attack(
        state, foe, give(state, foe, "Spear_Thrust"),
        target_kind="frame", target_id=frame.id)
    zones = list(attack.current.pending_zones)
    assert uid in combat.block_options(state, frame, attack, zones), (
        "before it resolves it blocks"
    )
    state.cards[uid].resolved = True
    assert uid not in combat.block_options(state, frame, attack, zones)


def test_a_drone_can_be_attacked_and_dies_with_its_frame():
    state, frame, enemy = duel(gap=4)
    summon(state, frame, "Swarm_Swarm")
    drone = next(t for t in state.tokens.values() if t.kind == fx.DRONE)
    drone.pos = Pos(enemy.pos.x - 1, enemy.pos.y)

    assert drone.attackable
    targets = combat.legal_targets(state, enemy, CATALOGUE["Spear_Thrust"])
    assert any(o["kind"] == "token" and o["id"] == drone.id for o in targets)
    assert not any(
        o["kind"] == "token" for o in combat.legal_targets(
            state, frame, CATALOGUE["Spear_Thrust"]
        )
    ), "you cannot attack your own drone"

    frame.alive = False
    effects.followup_decision(state)
    assert not drone.alive, "the drone shuts down with its frame"


def test_crawl_summons_a_drone_that_attacks_low():
    state, frame, enemy = duel(gap=4)
    summon(state, frame, "Swarm_Crawl")
    next(t for t in state.tokens.values() if t.kind == fx.DRONE).pos = Pos(4, 2)
    assert effects.followup_decision(state) is True
    beside = Pos(enemy.pos.x - 1, enemy.pos.y)
    assert answer(state, state.pending, {"x": beside.x, "y": beside.y}) is None
    assert enemy.damage["Low"] == 1
    assert enemy.damage["Mid"] == 0


def test_a_gun_tower_is_placed_at_range_and_shoots_without_moving():
    """"Summon one Gun Tower within N" -- a turret, not a pet.

    Placement reach and count both come off the printed text, so the card
    needed no handler of its own: `_effect_handler` matches it on being a
    drone card at all.
    """
    card = CATALOGUE["Gun Tower_Gun Tower 1"]
    reach = effects._reach_from_text(card.text, 1)
    state, frame, enemy = duel(gap=card.ranges["High"])
    assert card.drone_movement == 0, "it does not move"

    uid, decision = play(state, frame, "Gun Tower_Gun Tower 1")
    assert decision is not None
    gaps = {state.board.distance(frame.pos, Pos(o["x"], o["y"]))
            for o in decision.options}
    assert gaps and max(gaps) == reach, "'within N' is read off the card"
    assert answer(state, decision, {"x": 4, "y": 2}) is None

    towers = [t for t in state.tokens.values() if t.kind == fx.DRONE]
    assert len(towers) == 1
    tower = towers[0]
    assert tower.pos == Pos(4, 2)
    assert tower.hp == card.drone_health == 2

    # It cannot close, but it does not need to: it shoots at its printed
    # range. With one target and nothing to block with there is nothing to
    # ask, so the whole of the tower's turn happens inside this call.
    assert effects.followup_decision(state) is False
    assert tower.pos == Pos(4, 2), "a turret stays put"
    assert enemy.damage["High"] == 1, "it shoots from where it was built"
    assert state.board.distance(tower.pos, enemy.pos) > 1, "well out of reach"


def test_attack_dogs_come_out_two_at_a_time():
    """"Summon two attack dogs" -- one decision each, two tokens."""
    state, frame, _ = duel(gap=4)
    first = play(state, frame, "Attack Dog_Rex and Rover")[1]
    assert first is not None and "2 left" in first.prompt
    second = answer(state, first, {"x": 2, "y": 1})
    assert second is not None, "the second dog is a decision of its own"
    assert not any(o["x"] == 2 and o["y"] == 1 for o in second.options), (
        "the first dog is standing there"
    )
    assert answer(state, second, {"x": 2, "y": 3}) is None

    dogs = [t for t in state.tokens.values() if t.kind == fx.DRONE]
    assert {d.pos for d in dogs} == {Pos(2, 1), Pos(2, 3)}
    assert all(d.hp == 1 for d in dogs), "one hit each"
    assert len(fx.slot(state, "drones")) == 2, "both act on their own"


def test_a_drone_card_needs_no_entry_in_the_effect_table():
    """The point of matching drones by type: new ones just work.

    Both cards added since this test was written -- the Gun Tower and the
    Attack Dog -- resolve through the same handler as the Swarm, and neither
    is named anywhere in `effects.py`.
    """
    source = Path(effects.__file__).read_text()
    drones = [c for c in CATALOGUE.values() if c.card_type == "drone"]
    assert len(drones) >= 6, "the catalogue has grown -- keep this honest"
    for card in drones:
        assert card.key not in effects.EFFECT_STEPS
        assert f'"{card.key}"' not in source, f"{card.key} is special-cased"
        assert effects._effect_handler(card) is effects._effect_summon_drone


#: Every drone card in the catalogue. Listed so a rename or a new one shows up
#: here as a failing lookup rather than as silently untested behaviour -- and
#: because the coverage guard at the bottom of this file wants every drone key
#: to appear in it by name.
DRONE_KEYS = (
    "Swarm_Swarm",
    "Swarm_Crawl",
    "Gun Tower_Gun Tower 1",
    "Gun Tower_Gun Tower 2",
    "Attack Dog_Rex and Rover",
    "Attack Dog_Max and Ceaser",
)


@pytest.mark.parametrize("key", DRONE_KEYS)
def test_every_drone_card_summons_what_its_printed_text_says(key):
    """The whole contract for a drone card, over every one of them.

    Nothing about these is written in Python: the handler is chosen by card
    *type* and both numbers come off the text, so this is the test that says
    the reading is right for each card actually in the catalogue. A variant
    added by copying a row -- two Gun Towers with different attack zones, two
    pairs of dogs -- is covered the moment its key goes in `DRONE_KEYS`.
    """
    assert key in CATALOGUE, f"{key} is not a card -- renamed in the CSV?"
    card = CATALOGUE[key]
    assert card.card_type == "drone"
    wanted = effects._count_from_text(card.text, 1)
    reach = effects._reach_from_text(card.text, 1)

    state, frame, _ = duel(gap=6)
    uid, decision = play(state, frame, key)
    seen = 0
    while decision is not None:
        gaps = {state.board.distance(frame.pos, Pos(o["x"], o["y"]))
                for o in decision.options}
        assert max(gaps) == reach, f"{key} places within {reach}"
        assert all(o["reach"] == max(
            (card.ranges[z] for z in ("High", "Mid", "Low") if card.attacks[z] > 0),
            default=0) for o in decision.options), (
            "each option carries the drone's own reach, for the AI to stand off by"
        )
        decision = answer(state, decision)
        seen += 1

    tokens = [t for t in state.tokens.values() if t.kind == fx.DRONE]
    assert len(tokens) == wanted, f"{card.text!r} should summon {wanted}"
    assert seen in (0, wanted), "one placement decision per token"
    for token in tokens:
        assert token.hp == token.max_hp == max(1, card.drone_health)
        assert token.owner == frame.seat
        assert state.board.distance(frame.pos, token.pos) <= reach
    records = fx.slot(state, "drones")
    assert len(records) == wanted
    assert all(r["key"] == key for r in records.values()), (
        "each token repeats the card that made it, not its group-mate"
    )


# --------------------------------------------------------------------------
# "Where does it go": the contract the board UI is drawn from
# --------------------------------------------------------------------------


def _tiles(decision):
    return [o for o in decision.options if "x" in o and "y" in o]


def test_a_placement_decision_says_how_many_tiles_it_still_wants():
    """The client marks tiles on the board and commits the set in one go.

    It can only do that if it knows how many the effect is going to ask for --
    the engine hands them over one at a time, so without a range every card
    that places more than one thing would be that many separate confirmations.
    """
    state, frame, _ = duel(gap=6)

    # Barricade: "up to 3", so nothing is compulsory and `done` stops early.
    barricade = play(state, frame, effects.BARRICADE)[1]
    assert barricade is not None
    assert (barricade.pick_min, barricade.pick_max) == (0, 3)
    assert any(o.get("done") for o in barricade.options), "a way to stop early"
    assert _tiles(barricade), "and tiles to choose from, in the same list"

    # Two attack dogs: both have to go somewhere.
    state, frame, _ = duel(gap=6)
    dogs = play(state, frame, "Attack Dog_Max and Ceaser")[1]
    assert (dogs.pick_min, dogs.pick_max) == (2, 2)
    assert not any(o.get("done") for o in dogs.options)

    # A portal is a pair -- one end on its own connects nothing.
    state, frame, _ = duel(gap=6)
    portal = play(state, frame, effects.PORTAL)[1]
    assert (portal.pick_min, portal.pick_max) == (2, 2)
    second = answer(state, portal, {"x": portal.options[0]["x"],
                                    "y": portal.options[0]["y"]})
    assert (second.pick_min, second.pick_max) == (1, 1), "one end left to place"


def test_a_single_tile_decision_stays_a_single_tile_decision():
    """One tile keeps movement's tap-to-propose, tap-to-commit.

    The batching UI is for cards that lay out a *set*; a Teleport or a gravity
    well is one answer and should not grow a commit button. `pick_min` and
    `pick_max` of 1 are the default, and the view omits them entirely.
    """
    from playtest.engine.serialize import _pending_json

    state, frame, _ = duel(gap=6)
    well = play(state, frame, effects.GRAVITY_WELL)[1]
    assert well is not None and _tiles(well)
    assert (well.pick_min, well.pick_max) == (1, 1)
    assert "pickMin" not in _pending_json(well, frame.seat)


def test_the_placement_range_reaches_the_client_view():
    from playtest.engine.serialize import _pending_json

    state, frame, _ = duel(gap=6)
    state.pending = play(state, frame, effects.BARRICADE)[1]
    blob = _pending_json(state.pending, frame.seat)
    assert blob["pickMin"] == 0 and blob["pickMax"] == 3
    # The tiles and the "stop" option travel together; a client that demanded
    # every option be a tile fell back to a list of raw grid coordinates.
    assert any("x" in o for o in blob["options"])
    assert any(o.get("done") for o in blob["options"])


# --------------------------------------------------------------------------
# The Revealed status, which nothing used to read
# --------------------------------------------------------------------------


def test_revealed_turns_committed_actions_face_up_for_the_other_seat():
    state = make_state()
    mine = add_frame(state, 0, "Kamikiri", Pos(2, 2))
    theirs = add_frame(state, 1, "Hector MkI", Pos(4, 2))
    seen = give(state, theirs, "Spear_Thrust")
    secret = give(state, mine, "Spear_Thrust")

    before = view_for(state, 0)
    enemy_cards = [
        c for f in before["frames"] if f["seat"] == 1 for c in f["committed"]
    ]
    assert enemy_cards and all("key" not in c for c in enemy_cards)

    effects.reveal_committed(state, theirs)
    after = view_for(state, 0)
    enemy_cards = [
        c for f in after["frames"] if f["seat"] == 1 for c in f["committed"]
    ]
    assert all(c.get("key") == "Spear_Thrust" for c in enemy_cards)

    mine_seen_by_them = [
        c for f in view_for(state, 1)["frames"] if f["seat"] == 0
        for c in f["committed"]
    ]
    assert all("key" not in c for c in mine_seen_by_them), (
        "revealing one frame reveals nothing else"
    )
    assert state.cards[secret].face_down is True


def test_revealed_frames_stay_face_up_as_the_turn_runs():
    state, mine, theirs = duel()
    theirs.statuses["revealed"] = 2
    later = give(state, theirs, "Spear_Thrust")
    assert state.cards[later].face_down is True
    effects.followup_decision(state)
    assert state.cards[later].face_down is False


# --------------------------------------------------------------------------
# Purity and determinism
# --------------------------------------------------------------------------


def test_effect_bookkeeping_survives_a_clone_without_sharing_it():
    state, frame, _ = duel()
    play(state, frame, effects.ENCODE)
    armed = fx.slot(state, "encode")[frame.id]
    copy = state.clone()
    assert fx.slot(copy, "encode")[frame.id] == armed
    fx.slot(copy, "encode").pop(frame.id)
    assert fx.slot(state, "encode")[frame.id] == armed, "the clone is independent"


def test_tokens_created_by_effects_are_deterministic():
    def run(seed):
        state = make_state(seed)
        engineer = add_frame(state, 0, "Percival MkIV", Pos(4, 4))
        _uid, decision = play(state, engineer, effects.BARRICADE)
        while decision is not None:
            pick = next(o for o in decision.options if "x" in o)
            decision = answer(state, decision, pick)
        return sorted((t.kind, t.pos.x, t.pos.y) for t in state.tokens.values())

    assert run(1) == run(1)


def test_every_pilot_and_drone_card_is_exercised_by_this_file():
    """Coverage guard: a new pilot card with no test here fails the build."""
    source = Path(__file__).read_text()
    named = {
        value: name for name, value in vars(effects).items()
        if isinstance(value, str) and value in CATALOGUE
    }
    for key, card in CATALOGUE.items():
        if card.card_type not in ("pilot", "drone"):
            continue
        alias = named.get(key)
        assert f'"{key}"' in source or (alias and f"effects.{alias}" in source), (
            f"{key} has no test in test_effects.py"
        )


# --------------------------------------------------------------------------
# Hidden information: the two cards that look at cards nobody normally sees
# --------------------------------------------------------------------------


def _strings_in(blob):
    """Every string anywhere in a view, however deeply nested."""
    if isinstance(blob, dict):
        return set().union(*(_strings_in(v) for v in blob.values())) if blob else set()
    if isinstance(blob, list):
        return set().union(*(_strings_in(v) for v in blob)) if blob else set()
    return {blob} if isinstance(blob, str) else set()


def test_the_combo_strike_deck_peek_reaches_only_its_own_seat():
    """Revealing the top 4 of a deck must not reveal them to the opponent."""
    state, attacker, _defender = duel()
    play(state, attacker, effects.COMBO_STRIKE)
    spare = give(state, attacker, "Halberd_Crush", location="deck")
    swing = give(state, attacker, "Halberd_Eviscerate")
    state.resolution = Resolution(frame_id=attacker.id, uid=swing, steps=["attack"])
    state.pending = effects.resolve_effect(state, attacker, swing)
    assert state.pending is not None

    mine = view_for(state, 0)
    assert any(o.get("uid") == spare for o in mine["pending"]["options"])

    theirs = view_for(state, 1)
    assert theirs["pending"] == {"seat": 0, "kind": "effect_choice", "waiting": True}
    leaked = _strings_in(theirs)
    assert spare not in leaked, "a deck card's uid reached the other seat"
    assert "Halberd_Crush" not in leaked, "a deck card's identity reached the other seat"


def test_encode_the_futures_deck_wide_commit_pool_stays_with_its_own_seat():
    from playtest.engine.types import PendingDecision

    state = make_state()
    mystic = add_frame(state, 0, "Hannael", Pos(2, 2))
    add_frame(state, 1, "Fenrir", Pos(8, 8))
    deck = give(state, mystic, "Halberd_Crush", location="deck")

    uid, decision = play(state, mystic, effects.ENCODE)
    assert decision is None, "with no other ally the card picks itself"
    carry_over(state, uid)
    pool = effects.commit_pool(state, mystic)
    assert deck in pool, "the whole deck is on offer"

    # The commit decision built from that pool is what the seats actually see.
    state.pending = PendingDecision(
        kind="commit_actions",
        seat=0,
        prompt="",
        options=[{"uid": u, "key": state.cards[u].key} for u in pool],
        frame_id=mystic.id,
    )
    leaked = _strings_in(view_for(state, 1))
    assert deck not in leaked
    assert "Halberd_Crush" not in leaked
    assert deck in _strings_in(view_for(state, 0))
