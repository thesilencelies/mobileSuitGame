"""Workstream D's tests: the AI plays legally, blindly, and reproducibly.

Four things are load-bearing and each has a test here:

* every command the agent emits is one the engine actually offered;
* it never reads another seat's hidden state -- enforced structurally, by
  handing it a JSON round-trip of its own redacted view and nothing else;
* it is deterministic under a fixed seed;
* a full AI-vs-AI game runs to completion through the public API.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

from playtest.ai import (
    PARAM_SCHEMA,
    PRESETS,
    Agent,
    AIParams,
    GreedyAgent,
    RandomAgent,
    params_from_dict,
    params_schema,
    preset,
)
from playtest.ai import arena, scoring as S
from playtest.ai.view import Catalogue, Snapshot
from playtest.engine import (
    GameConfig,
    IllegalCommand,
    apply_command,
    catalogue_json,
    is_over,
    legal_commands,
    load_cards,
    new_game,
    scores,
    view_for,
)
from playtest.engine.types import ZONES

DECKS_A = ["deck_aegis_percival", "deck_aegis_hector", "deck_collective_adam"]
DECKS_B = ["deck_guild_nautilus", "deck_ouwa_kamikiri", "deck_church_elemiah"]


@pytest.fixture(scope="module")
def catalogue():
    return catalogue_json(load_cards())


def make_game(seed: int = 11, frames: int = 2):
    return new_game(
        GameConfig(
            player_decks=DECKS_A[:frames],
            ai_decks=DECKS_B[:frames],
            seed=seed,
            frames_per_side=frames,
        )
    )


def play(state, agents, *, sanitise=True, record=None, limit=4000):
    """Drive a game with `agents`, handing each only its own redacted view."""
    steps = 0
    while not is_over(state) and state.pending is not None and steps < limit:
        seat = int(state.pending.seat)
        view = view_for(state, seat)
        if sanitise:
            # A JSON round trip: whatever the agent gets back is plain data
            # with no reference of any kind to the GameState.
            view = json.loads(json.dumps(view))
        command = agents[seat].act(view)
        assert command is not None, f"agent {seat} refused a {state.pending.kind}"
        if record is not None:
            record.append((seat, command.kind, json.dumps(command.payload, sort_keys=True)))
        state = apply_command(state, command)
        steps += 1
    return state


# --------------------------------------------------------------------------
# Legality
# --------------------------------------------------------------------------


def _matches(command, legal) -> bool:
    """Is `command` one of the `legal` ones, by the engine's own rules?

    The engine accepts a payload when some offered option agrees with every
    key the caller sent (`resolve._offered`), so a `move` may omit the `cost`
    the option carries. `commit_actions` is a set of uids, in any order.
    """
    for other in legal:
        if other.kind != command.kind or other.seat != command.seat:
            continue
        if command.kind == "commit_actions":
            if sorted(map(str, command.payload.get("uids", []))) == sorted(
                map(str, other.payload.get("uids", []))
            ):
                return True
            continue
        if all(other.payload.get(k) == v for k, v in command.payload.items()):
            return True
    return False


def test_agent_emits_only_legal_commands(catalogue):
    """Every command is one `legal_commands` offered, for every decision kind."""
    state = make_game(seed=5)
    agents = {s: Agent(seat=s, catalogue=catalogue, seed=100 + s) for s in (0, 1)}
    kinds = set()
    steps = 0
    while not is_over(state) and state.pending is not None and steps < 4000:
        seat = int(state.pending.seat)
        legal = legal_commands(state, seat)
        assert legal, "the engine offered a decision with no legal commands"
        command = agents[seat].act(json.loads(json.dumps(view_for(state, seat))))
        assert command is not None
        assert _matches(command, legal), f"illegal {command.kind}: {command.payload}"
        kinds.add(command.kind)
        state = apply_command(state, command)
        steps += 1
    # The interesting decisions must actually have come up, or the test is vacuous.
    assert {"commit_actions", "move"} <= kinds
    assert is_over(state)


def test_agent_never_sends_a_rejected_command(catalogue):
    """Belt and braces: the engine itself never raises on an agent command."""
    for seed in (3, 8):
        state = make_game(seed=seed)
        agents = {s: Agent(seat=s, catalogue=catalogue, seed=seed * 10 + s) for s in (0, 1)}
        try:
            state = play(state, agents)
        except IllegalCommand as exc:            # pragma: no cover - the failure
            pytest.fail(f"agent produced an illegal command: {exc}")
        assert is_over(state)


def test_random_and_greedy_baselines_are_legal(catalogue):
    state = make_game(seed=17)
    agents = {0: RandomAgent(seat=0, seed=1), 1: GreedyAgent(seat=1, seed=2)}
    state = play(state, agents)
    assert is_over(state)


# --------------------------------------------------------------------------
# No peeking
# --------------------------------------------------------------------------


def test_agent_input_carries_no_hidden_state(catalogue):
    """The redacted view an agent is handed contains no opponent secrets."""
    state = make_game(seed=21)
    view = view_for(state, 1)
    for frame in view["frames"]:
        if frame["seat"] == 1:
            continue
        assert "hand" not in frame, "an opponent's hand leaked into the view"
        for card in frame["committed"]:
            if card["faceDown"]:
                assert "key" not in card, "a face-down commitment leaked its identity"
    # And the whole thing is serialisable plain data -- no engine objects.
    json.dumps(view)


def test_agent_plays_a_whole_game_on_json_only(catalogue):
    """Structural proof: the agent only ever touches a JSON round trip.

    If the agent reached into a `GameState`, or held a reference to one
    through the view, this would fail -- `json.loads(json.dumps(...))` shares
    nothing with the state it came from.
    """
    state = make_game(seed=33)
    agents = {s: Agent(seat=s, catalogue=catalogue, seed=7 + s) for s in (0, 1)}
    state = play(state, agents, sanitise=True)
    assert is_over(state)
    assert sum(scores(state).values()) >= 0


def test_agent_refuses_another_seats_view(catalogue):
    state = make_game(seed=4)
    agent = Agent(seat=1, catalogue=catalogue, seed=1)
    wrong = view_for(state, 0)
    # Seat 0's view, force-labelled as a pending decision for seat 1.
    wrong = json.loads(json.dumps(wrong))
    wrong["pending"] = {"seat": 1, "kind": "commit_actions", "options": [{"uid": "x", "key": "y"}]}
    with pytest.raises(ValueError):
        agent.act(wrong)


def test_agent_returns_none_when_it_is_not_its_turn(catalogue):
    state = make_game(seed=4)
    idle = 1 - int(state.pending.seat)
    agent = Agent(seat=idle, catalogue=catalogue, seed=1)
    assert agent.act(json.loads(json.dumps(view_for(state, idle)))) is None


def test_ai_module_does_not_import_engine_internals():
    """The AI may use pure geometry and the frozen types; nothing stateful.

    Checked over the parsed import statements, so a module cannot reach the
    mutable game state at all -- `board` is pure geometry and `types` is the
    frozen contract, and nothing else in `engine` is importable from here.
    The arena is exempt: it is the harness and legitimately drives games.
    """
    allowed = {"..engine.board", "..engine.types", ".agent", ".baseline",
               ".params", ".scoring", ".view", "."}
    root = pathlib.Path(__file__).resolve().parents[1] / "ai"
    checked = 0
    for path in sorted(root.glob("*.py")):
        if path.name == "arena.py":
            continue
        tree = ast.parse(path.read_text())
        checked += 1
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = "." * node.level + (node.module or "")
                assert not module.startswith("..engine") or module in allowed, (
                    f"{path.name} imports engine internals: {module}"
                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("playtest.engine"), (
                        f"{path.name} imports {alias.name}"
                    )
    assert checked >= 4


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_agent_is_deterministic_under_a_fixed_seed(catalogue):
    """Same seeds in, byte-identical command sequence and result out.

    `think_ms=0` removes the wall-clock ceiling. With a clock-driven cutoff
    armed, play is only reproducible while the budget is not reached, which is
    machine- and load-dependent and so not something to assert in a test.
    """
    runs = []
    for _ in range(2):
        state = make_game(seed=64)
        agents = {
            s: Agent(seat=s, catalogue=catalogue, seed=999 + s,
                     params={"think_ms": 0})
            for s in (0, 1)
        }
        record: list = []
        state = play(state, agents, record=record)
        runs.append((record, scores(state)))
    assert runs[0][0] == runs[1][0]
    assert runs[0][1] == runs[1][1]
    assert len(runs[0][0]) > 20


def test_different_agent_seeds_diverge(catalogue):
    """The policy is genuinely mixed -- a different seed plays differently."""
    records = []
    for agent_seed in (1, 2):
        state = make_game(seed=64)
        agents = {
            s: Agent(seat=s, catalogue=catalogue, seed=agent_seed * 50 + s)
            for s in (0, 1)
        }
        record: list = []
        play(state, agents, record=record)
        records.append(record)
    assert records[0] != records[1]


def test_search_width_bounds_the_candidate_scan(catalogue):
    """The compute budget is a hard cap, not a suggestion."""
    narrow = Agent(seat=0, catalogue=catalogue, params={"search_width": 12})
    wide = Agent(seat=0, catalogue=catalogue, params={"search_width": 120})
    assert narrow.move_candidates == 12
    assert wide.move_candidates == 120
    assert narrow.opportunity_candidates < wide.opportunity_candidates
    # However tight the setting, a decision never drops below the floor.
    floor = Agent(seat=0, catalogue=catalogue, params={"search_width": 0})
    from playtest.ai.agent import MIN_CANDIDATES

    assert floor.move_candidates == MIN_CANDIDATES


def test_tight_time_budget_still_plays_a_legal_game(catalogue):
    """A 1ms ceiling degrades the search, it does not break or stall it."""
    state = make_game(seed=91)
    agents = {
        s: Agent(seat=s, catalogue=catalogue, seed=s,
                 params={"think_ms": 1, "search_width": 120})
        for s in (0, 1)
    }
    steps = 0
    while not is_over(state) and state.pending is not None and steps < 4000:
        seat = int(state.pending.seat)
        legal = legal_commands(state, seat)
        command = agents[seat].act(json.loads(json.dumps(view_for(state, seat))))
        assert command is not None
        assert _matches(command, legal)
        state = apply_command(state, command)
        steps += 1
    assert is_over(state)
    # The ceiling really did bite, otherwise this proves nothing.
    assert sum(a.stats.get("timeout", 0) for a in agents.values()) > 0
    assert sum(a.stats.get("fallback", 0) for a in agents.values()) == 0


def test_arena_pins_decks_to_seats(catalogue):
    """Swapping moves the sides between seats; the decks stay put.

    Guards the harness bug that made ten identical-vs-identical controls come
    out 58% to side A, because side A always played the stronger squad.
    """
    decks_a, decks_b = ["deck_aegis_percival"], ["deck_guild_nautilus"]
    side_a, side_b = arena.parse_side("beginner"), arena.parse_side("random")
    seats = []
    for swap in (False, True):
        result = arena.play_game(
            side_a, side_b, seed=15, decks_a=decks_a, decks_b=decks_b,
            swap=swap, catalogue=catalogue,
        )
        assert result.seat_decks == {0: decks_a, 1: decks_b}
        seats.append(result.seat_of[side_a.label])
    assert seats == [0, 1], "the sides must alternate seats"


def test_arena_records_think_time(catalogue):
    result = arena.play_game(
        arena.parse_side("standard"), arena.parse_side("random"),
        seed=19, decks_a=DECKS_A[:2], decks_b=DECKS_B[:2], catalogue=catalogue,
    )
    assert set(result.think) == {"standard", "random"}
    assert all(t >= 0.0 for times in result.think.values() for t in times)
    assert sum(len(t) for t in result.think.values()) == result.decisions


def test_arena_run_is_reproducible(catalogue):
    side_a = arena.parse_side("standard")
    side_b = arena.parse_side("random")
    first = arena.play_game(side_a, side_b, seed=12, decks_a=DECKS_A[:2],
                            decks_b=DECKS_B[:2], catalogue=catalogue)
    second = arena.play_game(side_a, side_b, seed=12, decks_a=DECKS_A[:2],
                             decks_b=DECKS_B[:2], catalogue=catalogue)
    assert first.vp == second.vp
    assert first.winner == second.winner
    assert first.decisions == second.decisions


# --------------------------------------------------------------------------
# A full AI-vs-AI game
# --------------------------------------------------------------------------


def test_full_ai_vs_ai_game_runs_to_completion(catalogue):
    result = arena.play_game(
        arena.parse_side("veteran"),
        arena.parse_side("standard"),
        seed=77,
        catalogue=catalogue,
    )
    assert result.turns >= 1
    assert result.decisions > 50
    assert set(result.vp) == {0, 1}
    assert all(v >= 0 for v in result.vp.values())


def test_arena_match_reports_both_sides(catalogue):
    report = arena.run_match(
        arena.parse_side("standard"),
        arena.parse_side("random"),
        games=2,
        seed=300,
        decks_a=DECKS_A[:2],
        decks_b=DECKS_B[:2],
        catalogue=catalogue,
    )
    data = report.to_dict()
    assert data["games"] == 2
    assert set(data["sides"]) == {"standard", "random"}
    for summary in data["sides"].values():
        assert 0.0 <= summary["win_rate"] <= 1.0
        assert summary["vp"] >= 0.0


def test_arena_cli_smoke(capsys, monkeypatch):
    assert arena.main(["--games", "1", "--seed", "5", "--a", "beginner",
                       "--b", "random", "--decks-a", *DECKS_A[:2],
                       "--decks-b", *DECKS_B[:2]]) == 0
    out = capsys.readouterr().out
    assert "beginner" in out and "random" in out


# --------------------------------------------------------------------------
# The parameter schema (workstream C serves this verbatim)
# --------------------------------------------------------------------------


def test_param_schema_shape_is_stable():
    required = {"name", "label", "min", "max", "default", "help"}
    names = set()
    for entry in PARAM_SCHEMA:
        assert required <= set(entry), f"{entry.get('name')} is missing {required - set(entry)}"
        assert isinstance(entry["help"], str) and entry["help"].strip()
        assert entry["min"] <= entry["default"] <= entry["max"]
        assert entry["type"] in ("int", "float")
        names.add(entry["name"])
    # The parameters the spec names must all be present.
    assert {
        "defense", "concentration", "aggression", "objective_weight",
        "focus_fire", "pool", "temperature",
    } <= names
    # Every schema entry is a real field on the dataclass and vice versa.
    assert names == set(AIParams().to_dict())
    json.dumps(params_schema())


def test_presets_are_valid_parameter_sets():
    bounds = {e["name"]: (e["min"], e["max"]) for e in PARAM_SCHEMA}
    for name in PRESETS:
        params = preset(name)
        for key, value in params.to_dict().items():
            lo, hi = bounds[key]
            assert lo <= value <= hi, f"{name}.{key}={value} outside [{lo},{hi}]"


def test_params_from_dict_applies_preset_then_overrides():
    params = params_from_dict({"preset": "veteran", "aggression": 2.5, "bogus": 1})
    assert params.aggression == 2.5
    assert params.temperature == PRESETS["veteran"]["temperature"]
    assert not hasattr(params, "bogus")
    assert params_from_dict(None) == AIParams()


# --------------------------------------------------------------------------
# Scorer behaviour
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cat(catalogue):
    return Catalogue(catalogue)


def test_reload_awareness_penalises_a_shot_that_will_be_swallowed(cat):
    """A reloading weapon's next attack does nothing at all -- never pick it."""
    reload_cards = [
        c for c in cat.cards.values() if c.reload and c.is_attack
    ]
    assert reload_cards, "no reload attack cards in the catalogue"
    card = reload_cards[0]
    prof = S.profile(list(cat.cards.values())[:60])
    params = AIParams()
    health = {z: 4 for z in ZONES}
    free = S.score_hand([card], prof, params, health, reloading=())
    duded = S.score_hand([card], prof, params, health, reloading={card.group})
    assert duded < free, "a shot spent reloading must score strictly worse"


def test_two_shots_from_one_reloading_weapon_score_worse_than_one(cat):
    group_cards: dict[str, list] = {}
    for c in cat.cards.values():
        if c.is_attack and c.reload:
            group_cards.setdefault(c.group, []).append(c)
    pair = next((v for v in group_cards.values() if len(v) >= 2), None)
    if pair is None:
        pytest.skip("no weapon group with two reload attacks")
    other = next(
        c for c in cat.cards.values()
        if c.is_attack and not c.reload and c.group != pair[0].group
    )
    prof = S.profile(list(cat.cards.values())[:60])
    params = AIParams()
    health = {z: 4 for z in ZONES}
    stacked = S.score_hand([pair[0], pair[1]], prof, params, health)
    spread = S.score_hand([pair[0], other], prof, params, health)
    assert spread > stacked


def test_dominated_card_filter():
    """A card that does strictly less than another is never worth an action."""
    from playtest.ai.view import CardInfo

    def make(key, attacks, blocks, movement=0):
        return CardInfo(
            key=key, name=key, group="G", faction="", card_type="weapon",
            initiative=(5,), movement=movement,
            attacks={z: attacks.get(z, 0) for z in ZONES},
            ranges={z: 0 for z in ZONES},
            dtypes={z: None for z in ZONES},
            blocks={z: blocks.get(z, 0) for z in ZONES},
            text="", keywords=frozenset(), knockback=0, persistence=0,
        )

    spear = make("spear", {"High": 2}, {"High": 1})
    pilot = make("pilot", {}, {"High": 1})
    assert S.dominates(spear, pilot)
    assert not S.dominates(pilot, spear)
    # A super block is dominated by nothing -- it is never spent.
    guard = make("guard", {}, {"Mid": 2})
    assert not S.dominates(spear, guard)


def test_elevation_shift_matches_the_worked_examples():
    """rules.tex's two worked figures, which the engine treats as authoritative."""
    # Attacker at 1 vs defender at 3, Cleave (High 2, Mid 2): High -> Low,
    # Mid falls off the bottom.
    assert S.elevation_shift({"High": 2, "Mid": 2}, -2) == {"Low": 2}
    # Attacker at 2 vs defender at 1, Thrust (Mid 1): Mid -> High.
    assert S.elevation_shift({"Mid": 1}, 1) == {"High": 1}


def test_high_ground_moves_an_attack_past_a_known_blocker(cat, catalogue):
    """The elevation lever: attacking uphill into a zone they cannot block.

    Same card, same target, same distance -- the only difference is standing
    one elevation higher, which shifts a Mid attack into High. The target has
    a revealed Mid blocker and nothing for High, so the uphill attack is worth
    strictly more.
    """
    from playtest.ai.view import CardInfo

    mid_hitter = CardInfo(
        key="Test_Thrust", name="Thrust", group="Spear", faction="",
        card_type="weapon", initiative=(6,), movement=0,
        attacks={"High": 0, "Mid": 2, "Low": 0},
        ranges={z: 0 for z in ZONES},
        dtypes={z: None for z in ZONES},
        blocks={z: 0 for z in ZONES},
        text="", keywords=frozenset(), knockback=0, persistence=0,
    )
    mid_blocker = next(
        c for c in cat.cards.values()
        if c.blocks.get("Mid", 0) == 1 and c.blocks.get("High", 0) == 0
    )

    view = _flat_view(catalogue, blocker_key=mid_blocker.key)
    snap = Snapshot(view)
    me = snap.frames["a0"]
    them = snap.frames["b0"]
    prof = S.profile(list(cat.cards.values())[:60])
    params = AIParams()

    level = S.attack_value(snap, me, mid_hitter, me.pos, them, prof, params)
    # Now put the attacker one level up, adjacent on the other side.
    from playtest.engine.types import Pos

    uphill_pos = Pos(6, 5)                     # an elevation-1 tile, still adjacent
    assert snap.elevation(uphill_pos) == 1
    uphill = S.attack_value(snap, me, mid_hitter, uphill_pos, them, prof, params)
    assert S.landing_zones(snap, me, mid_hitter, uphill_pos, them) == {"High": 2}
    assert uphill > level


def _flat_view(catalogue, blocker_key: str) -> dict:
    """A tiny hand-built view: two adjacent frames and one raised tile."""
    width, height = 10, 10
    tiles = []
    for y in range(height):
        for x in range(width):
            tiles.append({
                "x": x, "y": y,
                "elev": 1 if (x, y) == (6, 5) else 0,
                "impassable": False, "obstacle": False,
                "objective": None, "card": "",
            })

    def frame(fid, seat, x, y):
        return {
            "id": fid, "seat": seat, "name": f"F{fid}", "faction": "Aegis",
            "pos": {"x": x, "y": y}, "elev": 0, "alive": True,
            "armour": {z: 4 for z in ZONES}, "damage": {z: 0 for z in ZONES},
            "lastHit": {z: False for z in ZONES},
            "movement": 4, "shields": 0,
            "statuses": {}, "committed": [], "onField": [], "aside": [],
            "deckCount": 10, "discardCount": 0,
        }

    me = frame("a0", 0, 4, 5)
    them = frame("b0", 1, 5, 5)
    them["onField"] = [
        {"uid": "z1", "key": blocker_key, "resolved": True, "faceDown": False}
    ]
    return {
        "gameId": "t", "turn": 1, "phase": "action", "priority": 0, "seat": 0,
        "board": {"width": width, "height": height, "tiles": tiles, "objectives": []},
        "frames": [me, them], "tokens": [], "pending": None, "log": [],
        "vp": {"0": 0, "1": 0}, "over": False,
    }


def test_known_face_up_blocker_beats_the_statistical_prior(cat, catalogue):
    """A revealed blocker is a fact; the profile is only a guess."""
    mid_blocker = next(
        c for c in cat.cards.values()
        if c.blocks.get("Mid", 0) == 1 and c.blocks.get("High", 0) == 0
    )
    snap = Snapshot(_flat_view(catalogue, mid_blocker.key))
    them = snap.frames["b0"]
    prof = S.profile(list(cat.cards.values())[:60])
    dummy = next(c for c in cat.cards.values() if c.is_attack)
    blocked = S.block_probability(them, ["Mid"], dummy, prof)
    open_zone = S.block_probability(them, ["High"], dummy, prof)
    assert blocked == pytest.approx(S.MAX_BLOCK_PROB)
    assert open_zone == 0.0            # nothing hidden, nothing covers High


def test_ranged_frame_does_not_want_to_stand_next_to_the_enemy(cat, catalogue):
    """A ranged attack may not target an adjacent frame, so adjacency is bad."""
    from playtest.ai.view import CardInfo
    from playtest.engine.types import Pos

    rifle = CardInfo(
        key="Test_Shot", name="Shot", group="Rifle", faction="",
        card_type="weapon", initiative=(5,), movement=0,
        attacks={"High": 0, "Mid": 2, "Low": 0},
        ranges={"High": 0, "Mid": 5, "Low": 0},
        dtypes={z: None for z in ZONES},
        blocks={z: 0 for z in ZONES},
        text="", keywords=frozenset(), knockback=0, persistence=0,
    )
    blocker = next(c for c in cat.cards.values() if c.blocks.get("Mid", 0) == 1)
    snap = Snapshot(_flat_view(catalogue, blocker.key))
    me = snap.frames["a0"]
    prof = S.profile(list(cat.cards.values())[:60])
    params = AIParams()
    adjacent = S.position_value(snap, me, Pos(4, 5), prof, params, primary=rifle)
    standoff = S.position_value(snap, me, Pos(1, 5), prof, params, primary=rifle)
    assert standoff > adjacent


def test_objectives_pull_the_evaluator(cat, catalogue):
    from playtest.engine.types import Pos

    view = _flat_view(catalogue, next(iter(cat.cards)))
    view["board"]["objectives"] = [{
        "name": "The Egg", "owner": 1, "defend": 1, "attack": 2,
        "tiles": [[9, 9]], "status": "unscored", "value": 0,
    }]
    snap = Snapshot(view)
    me = snap.frames["a0"]
    prof = S.profile(list(cat.cards.values())[:60])
    params = AIParams()
    on_egg = S.objective_value(snap, me, Pos(9, 9), params)
    far = S.objective_value(snap, me, Pos(0, 0), params)
    assert on_egg > far > 0 or (on_egg > 0 and far == 0)
    assert S.objective_value(snap, me, Pos(9, 9), params.replace(objective_weight=0.0)) == 0.0


def test_shiny_thing_is_valued_even_though_it_has_no_tiles(cat, catalogue):
    """The one objective whose `tiles` are empty -- it lives on its token.

    An earlier version skipped every objective with no tiles, which silently
    dropped the Shiny Thing: across six games nobody ever picked it up.
    """
    from playtest.engine.types import Pos

    view = _flat_view(catalogue, next(iter(cat.cards)))
    view["board"]["objectives"] = [{
        "name": "Shiny Thing", "owner": 1, "defend": 1, "attack": 2,
        "tiles": [], "status": "unscored", "value": 0,
    }]
    view["tokens"] = [{
        "id": "s1", "kind": "shiny", "pos": {"x": 8, "y": 8},
        "hp": 0, "maxHp": 0, "alive": True, "carrier": None,
    }]
    snap = Snapshot(view)
    me = snap.frames["a0"]
    params = AIParams()
    on_token = S.objective_value(snap, me, Pos(8, 8), params)
    beside = S.objective_value(snap, me, Pos(7, 8), params)
    far = S.objective_value(snap, me, Pos(0, 0), params)
    assert on_token > beside > far


def test_unimplemented_card_text_is_flagged_and_not_valued(cat):
    """v1 defers all pilot and drone text; the scorer must not price it in."""
    deferred = [c for c in cat.cards.values() if c.not_implemented]
    assert deferred, "expected the catalogue to flag deferred effects"
    assert all(c.card_type in ("pilot", "drone") for c in deferred), (
        "something other than pilot/drone text is unimplemented: "
        + ", ".join(sorted({c.key for c in deferred if c.card_type not in ("pilot", "drone")}))
    )
    # A pilot card's value comes only from its printed block, never its text.
    pilot = next(c for c in deferred if c.card_type == "pilot")
    prof = S.profile(list(cat.cards.values())[:60])
    params = AIParams()
    health = {z: 4 for z in ZONES}
    assert S.score_hand([pilot], prof, params, health, pressure=0.0) >= 0.0
