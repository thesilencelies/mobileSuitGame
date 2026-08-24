"""Shared fixtures for the combat/state layer tests (workstream B2).

These tests deliberately do **not** need workstream B1's real board: the
combat layer depends on the spatial layer only through `BoardProtocol`, so a
featureless stub board is enough and keeps the units isolated. `StubBoard`
adds per-tile edits and a switchable line-of-sight answer on top of the
engine's own `FlatBoard`.
"""

from __future__ import annotations

import dataclasses
import random
from typing import Callable, Optional, Sequence

from playtest.engine import cards as cardlib
from playtest.engine import combat
from playtest.engine.resolve import FlatBoard
from playtest.engine.state import CardInstance, FrameState, GameState
from playtest.engine.types import Pos, Tile

CATALOGUE = cardlib.load_cards()
FRAMES = cardlib.load_frames()

_PILES = {
    "deck": "deck",
    "hand": "hand",
    "committed": "committed",
    "discard": "discard",
    "aside": "aside",
}


class StubBoard(FlatBoard):
    """A `BoardProtocol` stub: flat by default, tiles and LoS editable."""

    def __init__(self, width: int = 10, height: int = 10) -> None:
        super().__init__(width, height)
        self.los = True

    def set_tile(self, pos: Pos, **changes) -> None:
        self._tiles[pos] = dataclasses.replace(self._tiles[pos], **changes)

    def has_line_of_sight(self, attacker, target, *, occupied=frozenset(),
                          flying_attacker=False):
        return self.los


def make_state(seed: int = 0, width: int = 10, height: int = 10) -> GameState:
    state = GameState(
        game_id="test",
        rng=random.Random(seed),
        catalogue=CATALOGUE,
        board=StubBoard(width, height),
    )
    state.kills = {0: 0, 1: 0}
    return state


def add_frame(
    state: GameState,
    seat: int,
    frame_name: str,
    pos: Optional[Pos] = None,
    frame_id: Optional[str] = None,
) -> FrameState:
    spec = FRAMES[frame_name]
    if frame_id is None:
        count = sum(1 for f in state.frames.values() if f.seat == seat)
        frame_id = f"{'ab'[seat % 2]}{count}"
    frame = FrameState(id=frame_id, seat=seat, spec=spec, pos=pos)
    frame.shields = spec.shield
    state.frames[frame_id] = frame
    return frame


def give(
    state: GameState,
    frame: FrameState,
    key: str,
    *,
    location: str = "committed",
    resolved: bool = False,
    face_down: bool = True,
    echo: bool = False,
) -> str:
    """Put one copy of `key` into a frame's pile and return its uid."""
    assert key in CATALOGUE, f"no such card {key!r}"
    uid = state.next_uid()
    state.cards[uid] = CardInstance(
        uid=uid,
        key=key,
        owner=frame.id,
        location=location,
        face_down=face_down,
        resolved=resolved,
        is_echo=echo,
    )
    getattr(frame, _PILES[location]).append(uid)
    return uid


def run_attack(
    state: GameState,
    attacker: FrameState,
    uid: str,
    target: FrameState,
    chooser: Optional[Callable[[Sequence[str], Sequence[str]], str]] = None,
):
    """Drive one full attack, answering every compulsory block decision.

    `chooser(zones, candidates) -> uid`; the default takes the first candidate.
    Returns the finished `AttackInProgress`.
    """
    attack = combat.declare_attack(
        state, attacker, uid, target_kind="frame", target_id=target.id
    )
    guard = 0
    while attack.current is not None:
        guard += 1
        assert guard < 50, "attack failed to terminate"
        decision = combat.next_block_decision(state, attack)
        if decision is None:
            combat.finish_target(state, attack)
            combat.advance_attack(state, attack)
            continue
        zones, candidates = decision
        pick = chooser(zones, candidates) if chooser else candidates[0]
        defender = state.frames[attack.current.id]
        combat.apply_block(state, defender, attack, pick, zones)
    return attack


def play_out(state: GameState, seed: int = 0, limit: int = 5000):
    """Play a game to the end, choosing legal commands pseudo-randomly."""
    from playtest.engine import apply_command, is_over, legal_commands

    rng = random.Random(seed)
    steps = 0
    while not is_over(state):
        steps += 1
        assert steps < limit, f"game stalled on {state.pending}"
        pending = state.pending
        assert pending is not None, f"no decision in phase {state.phase}"
        options = legal_commands(state, pending.seat)
        assert options, f"no legal commands for {pending}"
        state = apply_command(state, rng.choice(options))
    return state
