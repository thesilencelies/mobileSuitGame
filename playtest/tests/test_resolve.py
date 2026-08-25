"""Turn/phase state machine, the initiative queue, cleanup and the public API.

Also covers the two properties the whole build rests on: `apply_command` is
pure, and `view_for` cannot leak another seat's hidden information.
"""

from __future__ import annotations

import copy

import pytest

from playtest.engine import (
    apply_command,
    is_over,
    legal_commands,
    new_game,
    scores,
    view_for,
)
from playtest.engine import resolve as R
from playtest.engine.resolve import IllegalCommand, next_actor
from playtest.engine.state import move_card, tick_statuses
from playtest.engine.types import (
    ACTIONS_PER_TURN,
    TURNS_PER_GAME,
    Command,
    GameConfig,
    Pos,
)

from ._helpers import (
    CATALOGUE, add_frame, deploy_all, give, make_state, play_out,
)

PLAYER_DECKS = ["deck_aegis_hector", "deck_ouwa_kuwagata", "deck_guild_nautilus"]
AI_DECKS = ["deck_collective_adam", "deck_revolution_ripper", "deck_church_hannael"]


def start(seed: int = 1, frames: int = 3, *, deploy: bool = True):
    """A new game. By default it is played past setup into turn 1 planning;
    `deploy=False` leaves it parked on the first `deploy` decision."""
    state = new_game(GameConfig(
        player_decks=PLAYER_DECKS[:frames],
        ai_decks=AI_DECKS[:frames],
        seed=seed,
        frames_per_side=frames,
    ))
    return deploy_all(state, seed) if deploy else state


# --------------------------------------------------------------------------
# The initiative queue
# --------------------------------------------------------------------------


def _action_state():
    """A hand-built state parked in the action phase with two frames."""
    state = make_state()
    a = add_frame(state, 0, "Kuwagata", Pos(1, 1))
    b = add_frame(state, 1, "Hector MkI", Pos(8, 8))
    state.phase = "action"
    return state, a, b


def test_initiative_resolves_highest_first():
    state, a, b = _action_state()
    slow = give(state, a, "Chainsaw_Disembowel")         # initiative 3
    fast = give(state, a, "Spear_Jab")                   # initiative 8
    frame, uid = next_actor(state)
    assert uid == fast
    state.cards[fast].init_index = 1                     # pretend it acted
    assert next_actor(state)[1] == slow


def test_ties_alternate_clockwise_from_the_priority_marker():
    state, a, b = _action_state()
    a1 = give(state, a, "Basic_Punch")                   # initiative 6
    a2 = give(state, a, "Basic_Punch")
    b1 = give(state, b, "Basic_Punch")
    b2 = give(state, b, "Basic_Punch")
    state.priority = 0

    order = []
    for _ in range(4):
        frame, uid = next_actor(state)
        order.append(frame.seat)
        state.cards[uid].init_index = 1
    assert order == [0, 1, 0, 1], "a tie alternates rather than clumping"


def test_ties_start_from_the_priority_seat():
    state, a, b = _action_state()
    give(state, a, "Basic_Punch")
    give(state, b, "Basic_Punch")
    state.priority = 1
    assert next_actor(state)[0].seat == 1


def test_a_cards_initiative_list_makes_it_act_twice():
    """`Quick Step` is "8,3" and acts at each value if it is not consumed."""
    state, a, b = _action_state()
    uid = give(state, a, "Booster_Quick Step")
    assert CATALOGUE["Booster_Quick Step"].initiative == (8, 3)

    frame, first = next_actor(state)
    assert first == uid
    from playtest.engine import keywords as kw
    assert kw.effective_initiative(state, a, state.card(uid), 0) == 8

    state.cards[uid].init_index = 1                      # first act done
    frame, second = next_actor(state)
    assert second == uid, "it is still in the queue at its second value"
    assert kw.effective_initiative(state, a, state.card(uid), 1) == 3

    state.cards[uid].init_index = 2
    assert next_actor(state) is None


def test_a_consumed_quick_step_does_not_act_again():
    state, a, b = _action_state()
    uid = give(state, a, "Booster_Quick Step")
    state.cards[uid].init_index = 1
    move_card(state, uid, "discard")                     # spent blocking
    assert next_actor(state) is None


def test_echo_cards_block_but_never_act():
    state, a, b = _action_state()
    dead = add_frame(state, 0, "Adam", None)
    dead.alive = False
    uid = give(state, a, "Basic_Block", echo=True)
    state.cards[uid].owner = dead.id
    assert next_actor(state) is None, "an echo never takes an action"
    from playtest.engine.combat import remaining_cards
    assert uid in remaining_cards(state, a), "but it can still block"


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


def test_planning_asks_each_frame_to_commit_two_actions():
    state = start(frames=2)
    assert state.phase == "planning"
    assert state.pending.kind == "commit_actions"
    frame = state.frames[state.pending.frame_id]
    assert len(frame.hand) == 7
    assert len(state.pending.options) == 7


def test_committing_moves_two_cards_down_and_discards_the_rest():
    state = start(frames=2)
    frame_id = state.pending.frame_id
    picks = [o["uid"] for o in state.pending.options[:ACTIONS_PER_TURN]]
    state = apply_command(
        state, Command("commit_actions", state.pending.seat, {"uids": picks})
    )
    frame = state.frames[frame_id]
    assert frame.committed == picks
    assert frame.hand == []
    assert len(frame.discard) == 5
    assert all(state.cards[u].face_down for u in picks)


def test_committing_a_card_that_is_not_in_hand_is_rejected():
    state = start(frames=2)
    seat = state.pending.seat
    with pytest.raises(IllegalCommand):
        apply_command(state, Command("commit_actions", seat, {"uids": ["nope", "x"]}))


def test_a_command_for_the_wrong_seat_is_rejected():
    state = start(frames=2)
    picks = [o["uid"] for o in state.pending.options[:2]]
    wrong = 1 - state.pending.seat
    with pytest.raises(IllegalCommand):
        apply_command(state, Command("commit_actions", wrong, {"uids": picks}))


def test_statuses_are_removed_at_the_end_of_planning_not_the_start():
    state = start(frames=1)
    for frame in state.frames.values():
        frame.statuses["stunned"] = 2
        frame.statuses["dazed"] = 1

    # Still in planning: nothing has been removed yet.
    assert state.phase == "planning"
    assert all(f.statuses["stunned"] == 2 for f in state.frames.values())

    while state.phase == "planning":
        pending = state.pending
        state = apply_command(state, legal_commands(state, pending.seat)[0])

    assert state.phase != "planning"
    assert all(f.statuses["stunned"] == 1 for f in state.frames.values())
    assert all(f.statuses["dazed"] == 0 for f in state.frames.values())


def test_dazed_reduces_the_planning_draw():
    state = start(frames=1)
    for frame in state.frames.values():
        frame.statuses["dazed"] = 3
        assert frame.draw_count == 5


# --------------------------------------------------------------------------
# Cleanup and persistence
# --------------------------------------------------------------------------


def _cleanup_state():
    state = make_state()
    frame = add_frame(state, 0, "Kuwagata", Pos(1, 1))
    add_frame(state, 1, "Adam", Pos(8, 8))
    state.phase = "action"
    return state, frame


def test_cleanup_discards_cards_without_persistence():
    state, frame = _cleanup_state()
    uid = give(state, frame, "Basic_Punch", resolved=True)
    state.cards[uid].persist_left = 0
    R.cleanup_phase(state)
    assert state.cards[uid].location == "discard"


def test_cleanup_discards_unresolved_cards_too():
    state, frame = _cleanup_state()
    uid = give(state, frame, "Specialist_Master duelist")   # persistence 3
    R.cleanup_phase(state)
    assert state.cards[uid].location == "discard", "it never resolved"


def test_a_persistent_card_is_set_aside_and_neither_resolves_nor_blocks():
    state, frame = _cleanup_state()
    uid = give(state, frame, "Specialist_Practiced Technique")   # persistence 1
    state.cards[uid].resolved = True
    state.cards[uid].persist_left = 1
    R.cleanup_phase(state)
    assert state.cards[uid].location == "aside"
    assert uid in frame.aside and uid not in frame.committed

    from playtest.engine.combat import remaining_cards
    assert uid not in remaining_cards(state, frame), "set-aside cards never block"
    state.phase = "action"
    assert next_actor(state) is None, "and never resolve again"


def test_a_persistence_one_card_survives_the_next_turn_then_expires():
    state, frame = _cleanup_state()
    uid = give(state, frame, "Specialist_Practiced Technique")
    state.cards[uid].resolved = True
    state.cards[uid].persist_left = 1

    R.cleanup_phase(state)                               # turn it resolved
    assert state.cards[uid].location == "aside"
    state.turn += 1
    R.cleanup_phase(state)                               # the following turn
    assert state.cards[uid].location == "discard"


def test_an_infinite_persistence_card_is_permanent():
    state, frame = _cleanup_state()
    uid = give(state, frame, "Cannon_Fullbore")          # persistence \infty
    state.cards[uid].resolved = True
    state.cards[uid].persist_left = None
    for _ in range(3):
        R.cleanup_phase(state)
        state.turn += 1
    assert state.cards[uid].location == "aside"


def test_a_reload_marker_stays_out_whatever_persistence_says():
    """`Railgun_Kinetic Barrage` prints Reload with Persistence 0."""
    state, frame = _cleanup_state()
    uid = give(state, frame, "Railgun_Kinetic Barrage")
    state.cards[uid].resolved = True
    from playtest.engine import keywords as kw
    kw.start_reload(state, frame, uid)
    R.cleanup_phase(state)
    assert state.cards[uid].location == "aside"
    assert frame.reloading == {"Railgun": uid}


def test_cleanup_moves_the_priority_marker_one_step_anticlockwise():
    state, frame = _cleanup_state()
    assert state.priority == 0
    R.cleanup_phase(state)
    assert state.priority == 1


def test_cleanup_advances_the_turn_and_ends_the_game_after_five():
    state, frame = _cleanup_state()
    state.turn = TURNS_PER_GAME
    R._cleanup(state)
    assert state.turn == TURNS_PER_GAME + 1
    assert state.phase == "finished"
    assert is_over(state)


def test_a_deathstruck_frame_dies_at_the_end_of_the_next_turn():
    state, frame = _cleanup_state()
    ripper = add_frame(state, 0, "RipperSmasher", Pos(2, 2))   # Deathstrike
    ripper.damage["Mid"] = 3                                   # Mid armour 3
    from playtest.engine.state import check_destruction
    check_destruction(state, ripper)
    assert ripper.alive and ripper.deathstrike_until == state.turn + 1

    R.cleanup_phase(state)                    # end of this turn -- still fighting
    assert ripper.alive
    state.turn += 1
    R.cleanup_phase(state)                    # end of the next turn
    assert not ripper.alive


def test_flamekins_end_of_turn_repair_can_pull_it_out_of_deathstrike():
    """"they can be repaired" -- Flamekin repairs itself every turn."""
    state, frame = _cleanup_state()
    flamekin = add_frame(state, 0, "Flamekin", Pos(2, 2))
    flamekin.damage["Mid"] = 2
    from playtest.engine.state import check_destruction
    check_destruction(state, flamekin)
    R.cleanup_phase(state)
    assert flamekin.alive and flamekin.deathstrike_until is None


# --------------------------------------------------------------------------
# Full games through the public API
# --------------------------------------------------------------------------


def test_a_full_game_runs_to_the_end_of_turn_five():
    state = play_out(start(seed=5))
    assert is_over(state)
    assert state.phase == "finished"
    assert state.turn == TURNS_PER_GAME + 1 or not all(
        any(f.alive for f in state.frames_of(s)) for s in state.seats
    )
    assert set(scores(state)) == {0, 1}


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 7])
def test_random_games_never_stall_or_offer_an_empty_decision(seed):
    state = play_out(start(seed=seed), seed=seed)
    assert is_over(state)


def test_legal_commands_is_empty_for_the_seat_that_is_not_deciding():
    state = start(frames=2)
    idle = 1 - state.pending.seat
    assert legal_commands(state, idle) == []
    assert legal_commands(state, state.pending.seat)


def test_the_engine_always_parks_on_a_decision_until_the_game_ends():
    state = start(seed=4)
    for _ in range(60):
        if is_over(state):
            break
        assert state.pending is not None
        state = apply_command(state, legal_commands(state, state.pending.seat)[0])


# --------------------------------------------------------------------------
# Purity and determinism
# --------------------------------------------------------------------------


def test_apply_command_does_not_mutate_the_state_it_is_given():
    state = start(seed=9)
    before = copy.deepcopy(view_for(state, 0))
    before_frames = {
        f.id: (list(f.hand), list(f.committed), list(f.deck), dict(f.damage))
        for f in state.frames.values()
    }
    before_log = list(state.log)

    nxt = apply_command(state, legal_commands(state, state.pending.seat)[0])

    assert nxt is not state
    assert view_for(state, 0) == before, "the input view is unchanged"
    assert state.log == before_log
    for frame in state.frames.values():
        assert (
            list(frame.hand), list(frame.committed),
            list(frame.deck), dict(frame.damage),
        ) == before_frames[frame.id]
    # And the new state really did move on.
    assert (nxt.pending, nxt.phase, len(nxt.log)) != (
        state.pending, state.phase, len(state.log)
    )


def test_the_same_seed_and_the_same_choices_replay_identically():
    def run(seed):
        state = start(seed=seed)
        trail = []
        import random
        rng = random.Random(seed)
        while not is_over(state):
            options = legal_commands(state, state.pending.seat)
            choice = rng.choice(options)
            trail.append((state.pending.kind, state.pending.seat, str(choice.payload)))
            state = apply_command(state, choice)
        return trail, scores(state), [e["text"] for e in state.log]

    first = run(23)
    second = run(23)
    assert first == second, "a seeded game replays exactly"


def test_different_seeds_give_different_games():
    a = play_out(start(seed=1), seed=1)
    b = play_out(start(seed=2), seed=2)
    assert [e["text"] for e in a.log] != [e["text"] for e in b.log]


def test_all_randomness_goes_through_the_state_rng():
    """Two games from the same seed deal the same battlefield and decks."""
    one, two = start(seed=31), start(seed=31)
    assert one.game_id == two.game_id
    assert [f.pos for f in one.frames.values()] == [f.pos for f in two.frames.values()]
    assert [(o.name, o.owner) for o in one.objectives] == [
        (o.name, o.owner) for o in two.objectives
    ]
    for fid, frame in one.frames.items():
        assert [one.cards[u].key for u in frame.deck] == [
            two.cards[u].key for u in two.frames[fid].deck
        ]


# --------------------------------------------------------------------------
# Hidden information
# --------------------------------------------------------------------------


def test_view_for_hides_the_other_seats_hand_and_deck_order():
    state = start(seed=13)
    # Commit for every frame so both sides have face-down cards.
    while state.phase == "planning":
        state = apply_command(state, legal_commands(state, state.pending.seat)[0])

    view = view_for(state, 0)
    mine = [f for f in view["frames"] if f["seat"] == 0]
    theirs = [f for f in view["frames"] if f["seat"] == 1]
    assert mine and theirs

    for frame in theirs:
        assert "hand" not in frame, "an opponent's hand is never in the view"
        assert "deck" not in frame
        assert isinstance(frame["deckCount"], int)
        for card in frame["committed"]:
            assert card["faceDown"] is True
            assert "key" not in card, "face-down commitments are redacted"


def test_view_for_shows_the_seat_its_own_cards():
    state = start(seed=13)
    seat = state.pending.seat
    view = view_for(state, seat)
    own = [f for f in view["frames"] if f["seat"] == seat]
    assert any(f.get("hand") for f in own)


def test_a_revealed_card_becomes_visible_to_everyone():
    state = start(seed=13)
    while state.phase == "planning":
        state = apply_command(state, legal_commands(state, state.pending.seat)[0])
    # Resolve one card, which reveals it.
    for _ in range(20):
        if state.phase != "action":
            break
        state = apply_command(state, legal_commands(state, state.pending.seat)[0])
        revealed = [
            c for f in view_for(state, 0)["frames"] if f["seat"] == 1
            for c in f["committed"] + f["onField"] if not c["faceDown"]
        ]
        if revealed:
            assert all("key" in c for c in revealed)
            return
    pytest.skip("no card was revealed in the sampled window")


def test_the_pending_decision_of_the_other_seat_carries_no_options():
    state = start(seed=13)
    other = 1 - state.pending.seat
    view = view_for(state, other)
    assert view["pending"]["waiting"] is True
    assert "options" not in view["pending"]


def test_the_view_is_plain_data_with_no_reference_back_to_the_state():
    state = start(seed=13)
    view = view_for(state, 0)
    view["frames"][0]["damage"]["High"] = 99
    view["log"].append({"turn": 0, "text": "tampered"})
    assert all(f.damage["High"] == 0 for f in state.frames.values())
    assert not any(e["text"] == "tampered" for e in state.log)


def test_face_down_cards_of_another_seat_carry_no_decodable_uid():
    """The uid is redacted on exactly the same condition as the key.

    Card uids are allocated in deck-file order and the deck CSVs ship with the
    app, so a raw uid *is* the card's identity to anyone who can count. Hiding
    the key while still shipping the uid would hide nothing.
    """
    import re

    state = start(seed=17)
    while state.phase == "planning":
        state = apply_command(state, legal_commands(state, state.pending.seat)[0])

    checked = 0
    for seat in (0, 1):
        for frame in view_for(state, seat)["frames"]:
            for card in frame["committed"] + frame["onField"]:
                if "key" in card:
                    continue
                checked += 1
                uid = card["uid"]
                assert uid not in state.cards, f"view ships the real uid {uid}"
                assert not re.fullmatch(r"c\d+", uid), f"{uid} is positional"
    assert checked, "no redacted card was actually examined"


def test_the_whole_game_never_ships_a_decodable_id_for_a_hidden_card():
    import re

    state = start(seed=8)
    checked = 0
    for _ in range(120):
        if is_over(state):
            break
        for seat in (0, 1):
            for frame in view_for(state, seat)["frames"]:
                for card in frame["committed"] + frame["onField"]:
                    if "key" not in card:
                        checked += 1
                        assert card["uid"] not in state.cards
                        assert not re.fullmatch(r"c\d+", card["uid"])
        state = apply_command(state, legal_commands(state, state.pending.seat)[0])
    assert checked > 20, f"only {checked} hidden cards seen -- test is not biting"


def test_a_seat_keeps_real_uids_for_the_cards_it_may_see():
    """Redaction must not over-apply: a seat acts on its own cards by uid."""
    state = start(seed=17)
    seat = state.pending.seat
    view = view_for(state, seat)
    own = [f for f in view["frames"] if f["seat"] == seat]
    for frame in own:
        for card in frame.get("hand", []):
            assert card["uid"] in state.cards
    for option in view["pending"]["options"]:
        assert option["uid"] in state.cards, "a seat's own options stay actionable"


def test_hidden_ids_are_stable_so_the_client_can_track_a_face_down_card():
    state = start(seed=17)
    while state.phase == "planning":
        state = apply_command(state, legal_commands(state, state.pending.seat)[0])
    first = view_for(state, 0)
    assert view_for(state, 0) == first, "polling twice gives the same ids"


def test_hidden_ids_are_not_derivable_from_the_seed():
    """The salt is deliberately not drawn from the seeded rng.

    If it were, an observer who knows the seed -- the app chooses it and it can
    be echoed back -- could rebuild the mapping and read the commitments.
    """
    def hidden(state):
        while state.phase == "planning":
            state = apply_command(
                state, legal_commands(state, state.pending.seat)[0]
            )
        return [
            card["uid"]
            for frame in view_for(state, 0)["frames"] if frame["seat"] == 1
            for card in frame["committed"] if "key" not in card
        ]

    one, two = start(seed=17), start(seed=17)
    assert one.game_id == two.game_id, "same seed, same game"
    ids_one, ids_two = hidden(one), hidden(two)
    assert ids_one and len(ids_one) == len(ids_two)
    assert ids_one != ids_two, "hidden ids must not be reproducible from the seed"


def test_the_two_seats_get_different_hidden_ids_for_the_same_card():
    """Per-seat derivation, so nothing correlates across the two views."""
    from playtest.engine.serialize import hidden_id

    state = start(seed=17)
    uid = next(iter(state.cards))
    assert hidden_id(state, 0, uid) != hidden_id(state, 1, uid)


# --------------------------------------------------------------------------
# The featureless board must not silently invent rules
# --------------------------------------------------------------------------


def test_a_featureless_board_refuses_to_judge_line_of_sight():
    """`FlatBoard` has no terrain, so it cannot answer -- and must not guess.

    Answering "clear" to everything would not fail; it would quietly hand
    every ranged attack unlimited sight and play a different game.
    """
    board = R.FlatBoard(8, 8)
    with pytest.raises(NotImplementedError) as excinfo:
        board.has_line_of_sight(Pos(0, 0), Pos(4, 4))
    assert "line of sight" in str(excinfo.value)
    assert "los_always_clear" in str(excinfo.value), "the message names the opt-in"


def test_ignoring_line_of_sight_has_to_be_asked_for_explicitly():
    board = R.FlatBoard(8, 8, los_always_clear=True)
    assert board.has_line_of_sight(Pos(0, 0), Pos(4, 4)) is True


def test_the_featureless_board_still_does_real_geometry():
    """Its movement and range are genuine -- only LoS is missing."""
    board = R.FlatBoard(8, 8)
    assert board.distance(Pos(0, 0), Pos(3, 2)) == 3
    assert len(list(board.neighbours(Pos(0, 0)))) == 3
    assert Pos(2, 0) in board.reachable(Pos(0, 0), 2)
    assert Pos(5, 0) not in board.reachable(Pos(0, 0), 2)


def test_a_ranged_attack_on_a_featureless_board_fails_loudly():
    """The regression this guards: no silent "everything is visible"."""
    from playtest.engine import combat
    from playtest.tests._helpers import add_frame, give

    state = make_state()
    state.board = R.FlatBoard(10, 10)
    gunner = add_frame(state, 0, "J7R-Salaryman", Pos(1, 1))
    target = add_frame(state, 1, "Kuwagata", Pos(6, 1))
    with pytest.raises(NotImplementedError):
        combat.legal_targets(state, gunner, CATALOGUE["Cannon_Fullbore"])


def test_a_board_factory_that_sets_no_board_is_an_error():
    """A factory that forgets the board must stop the game, not fall back."""
    R.set_board_factory(lambda state, config: None)
    try:
        with pytest.raises(RuntimeError) as excinfo:
            start(seed=3)
        assert "battlefield" in str(excinfo.value)
    finally:
        R.set_board_factory(None)


def test_a_broken_terrain_deal_stops_the_game_instead_of_flattening_it():
    """There is no fallback: a setup failure propagates."""
    import playtest.engine.setup as setup_mod

    original = setup_mod.deal_battlefield
    setup_mod.deal_battlefield = lambda *a, **k: (_ for _ in ()).throw(
        ValueError("terrain deck is broken")
    )
    try:
        with pytest.raises(ValueError, match="terrain deck is broken"):
            start(seed=3)
    finally:
        setup_mod.deal_battlefield = original


def test_real_games_get_the_real_board_not_a_flat_one():
    state = start(seed=3)
    assert not isinstance(state.board, R.FlatBoard)
    assert type(state.board).__name__ == "Board"


# --------------------------------------------------------------------------
# Setup: deployment (rules.tex Setup)
# --------------------------------------------------------------------------


def test_a_new_game_opens_on_a_deployment_decision():
    state = start(seed=5, deploy=False)
    assert state.phase == "setup"
    assert state.pending.kind == "deploy"
    assert all(f.pos is None for f in state.frames.values())


def test_deploy_options_pair_a_frame_with_a_legal_tile():
    state = start(seed=5, deploy=False)
    seat = state.pending.seat
    tiles = R.deployment_tiles(state, seat)
    own = {f.id for f in state.frames_of(seat, alive_only=False)}
    assert state.pending.options
    for option in state.pending.options:
        assert set(option) == {"frame", "name", "x", "y"}
        assert option["frame"] in own, "a seat only places its own frames"
        assert Pos(option["x"], option["y"]) in tiles
    # every undeployed frame is offered, on every free tile
    assert len(state.pending.options) == len(own) * len(tiles)


def test_deployment_alternates_between_seats():
    """"Each player takes it in turns to put one of their frames"."""
    state = start(seed=5, deploy=False)
    seats = []
    while state.phase == "setup":
        seats.append(state.pending.seat)
        state = apply_command(state, legal_commands(state, state.pending.seat)[0])
    assert seats == [0, 1, 0, 1, 0, 1]


def test_deployment_places_every_frame_on_its_own_edge_and_no_two_share_a_tile():
    state = start(seed=5)
    assert state.phase == "planning"
    positions = [f.pos for f in state.frames.values()]
    assert all(p is not None for p in positions)
    assert len(set(positions)) == len(positions), "no two frames on one tile"
    for frame in state.frames.values():
        row = state.board.height - 1 if frame.seat == 0 else 0
        assert frame.pos.y == row, "frames deploy on their own nearest edge"


def test_a_seat_cannot_deploy_the_other_seats_frame():
    state = start(seed=5, deploy=False)
    seat = state.pending.seat
    enemy_frame = next(f for f in state.frames.values() if f.seat != seat)
    option = dict(state.pending.options[0])
    option["frame"] = enemy_frame.id
    with pytest.raises(IllegalCommand):
        apply_command(state, Command("deploy", seat, option))


def test_a_frame_cannot_be_deployed_onto_an_occupied_tile():
    state = start(seed=5, deploy=False)
    seat = state.pending.seat
    first = dict(state.pending.options[0])
    state = apply_command(state, Command("deploy", seat, first))
    taken = Pos(first["x"], first["y"])
    # come back round to the same seat
    while state.pending.seat != seat:
        state = apply_command(state, legal_commands(state, state.pending.seat)[0])
    assert all(
        Pos(o["x"], o["y"]) != taken for o in state.pending.options
    ), "the tile just used is no longer offered"


def test_a_deployed_frame_is_not_offered_again():
    state = start(seed=5, deploy=False)
    seat = state.pending.seat
    first = dict(state.pending.options[0])
    state = apply_command(state, Command("deploy", seat, first))
    while state.pending.seat != seat:
        state = apply_command(state, legal_commands(state, state.pending.seat)[0])
    assert all(o["frame"] != first["frame"] for o in state.pending.options)


def test_the_priority_marker_goes_to_whoever_deployed_first():
    state = start(seed=5, deploy=False)
    first_seat = state.pending.seat
    state = deploy_all(state, 5)
    assert state.priority == first_seat


def test_the_fugitive_is_placed_after_deployment_on_a_free_tile():
    """"Put a fugitive token anywhere in the enemy back row after deployment"."""
    for seed in range(12):
        state = start(seed=seed)
        fugitives = [t for t in state.tokens.values() if t.kind == "fugitive"]
        if not fugitives:
            continue
        for token in fugitives:
            assert token.pos is not None
            assert state.frame_at(token.pos) is None, "not under a frame"
            objective = next(o for o in state.objectives if o.name == "Fugitive")
            enemy_row = state.board.height - 1 if objective.owner == 1 else 0
            assert token.pos.y == enemy_row, "it starts in the enemy back row"
        return
    pytest.skip("no Fugitive objective came up in the sampled seeds")


def test_deployment_is_deterministic_for_a_seed():
    a = start(seed=21)
    b = start(seed=21)
    assert [f.pos for f in a.frames.values()] == [f.pos for f in b.frames.values()]


# --------------------------------------------------------------------------
# The log is a public channel
# --------------------------------------------------------------------------

SEED_LITERAL = 987654321


def test_the_seed_never_reaches_either_seats_view():
    """Seed + the shipped deck CSVs would replay `rng` and expose the
    opponent's deck order and future draws -- strictly worse than reading a
    face-down card, because it exposes the future."""
    import json

    state = start(seed=SEED_LITERAL)
    for seat in (0, 1):
        blob = json.dumps(view_for(state, seat))
        assert str(SEED_LITERAL) not in blob, f"seat {seat} can read the seed"
        assert "seed" not in blob.lower()


def test_the_seed_stays_available_privately_for_replay():
    state = start(seed=SEED_LITERAL)
    assert state.seed == SEED_LITERAL
    assert any(str(SEED_LITERAL) in e["text"] for e in state.private_log)


def test_the_private_log_is_never_shipped():
    state = start(seed=SEED_LITERAL)
    state.note_private("operator only: nothing here is for a player")
    for seat in (0, 1):
        view = view_for(state, seat)
        assert "private_log" not in view and "privateLog" not in view
        texts = [e["text"] for e in view["log"]]
        assert not any("operator only" in t for t in texts)


def test_an_autogenerated_seed_is_also_kept_out_of_the_view():
    """A game started without an explicit seed still must not leak the one
    the engine picked for it."""
    import json

    state = new_game(GameConfig(
        player_decks=PLAYER_DECKS[:1], ai_decks=AI_DECKS[:1], frames_per_side=1,
    ))
    assert state.seed is not None
    assert str(state.seed) not in json.dumps(view_for(state, 0))


def _keys_named_in(text: str, keys: list[str]) -> list[str]:
    """Card keys a log line names, ignoring ones covered by a longer key.

    "Axe_Split" is a substring of "Great Axe_Split", so a naive scan
    double-counts.
    """
    matched: list[str] = []
    for key in keys:
        if key in text and not any(key in longer for longer in matched):
            matched.append(key)
    return matched


def test_the_public_log_only_ever_names_cards_that_are_public():
    """The log ships wholesale to both seats, so it is public by construction.

    The invariant: no log line names a card that is still hidden once the
    command that wrote it has finished. It is checked per command rather than
    against the finished log because card *keys* are not unique -- decks hold
    duplicates and cards recycle through reshuffles -- so scanning the whole
    log for a key proves nothing on its own.
    """
    from playtest.engine.state import GameState

    pending_checks: list[tuple[str, str, bool]] = []
    original = GameState.note

    def spy(self, text: str) -> None:
        keys = sorted({c.key for c in self.cards.values()}, key=len, reverse=True)
        for key in _keys_named_in(text, keys):
            public_now = any(
                c.key == key and not c.face_down for c in self.cards.values()
            )
            pending_checks.append((text, key, public_now))
        original(self, text)

    GameState.note = spy
    try:
        state = start(seed=31)
        checked = 0
        for _ in range(140):
            if is_over(state):
                break
            pending_checks.clear()
            state = apply_command(
                state, legal_commands(state, state.pending.seat)[0]
            )
            for text, key, public_at_write in pending_checks:
                if public_at_write:
                    checked += 1
                    continue
                # Some effects log the card and reveal it in the next
                # statement; what matters is that it is public by the time
                # the client can see the line.
                public_after = any(
                    c.key == key and not c.face_down for c in state.cards.values()
                )
                assert public_after, f"log names hidden card {key!r}: {text!r}"
                checked += 1
    finally:
        GameState.note = original
    assert checked > 40, f"only {checked} named cards seen -- test is not biting"


def test_draw_and_reshuffle_lines_carry_counts_not_card_names():
    """Deck contents are the other thing the log must never expose."""
    state = start(seed=31)
    for _ in range(80):
        if is_over(state):
            break
        state = apply_command(state, legal_commands(state, state.pending.seat)[0])

    every_key = {c.key for c in state.cards.values()}
    interesting = [
        e["text"] for e in state.log
        if "draws" in e["text"] or "reshuffles" in e["text"]
        or "commits" in e["text"]
    ]
    assert interesting, "no draw/reshuffle/commit lines to check"
    for text in interesting:
        assert not any(key in text for key in every_key), text
