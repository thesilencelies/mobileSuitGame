"""NetFrame playtest rules engine -- public API.

Workstreams C (server) and D (AI) code against exactly this surface:

    new_game(config)            -> GameState
    legal_commands(state, seat) -> list[Command]
    apply_command(state, cmd)   -> GameState        (a NEW state; never mutates)
    view_for(state, seat)       -> dict             (redacted client JSON)
    is_over(state)              -> bool
    scores(state)               -> dict[int, int]

`apply_command` is pure: it clones the state, applies the decision and then
advances the machine until the next decision is needed. All randomness goes
through the single `random.Random` on the state, so replaying the same seed
and the same command sequence reproduces a game exactly.
"""

from __future__ import annotations

from typing import Any, Mapping

from .types import (
    Card,
    Command,
    DecisionKind,
    FrameSpec,
    GameConfig,
    PendingDecision,
    Phase,
    Pos,
    Team,
    Tile,
    Zone,
    ZONES,
)
from .state import GameState, victory_points
from . import resolve as _resolve
from .resolve import IllegalCommand, FlatBoard, set_board_factory
from .serialize import view_for as _view_for, catalogue_json, card_json
from .cards import (
    DeckReport,
    available_decks,
    load_cards,
    load_deck,
    load_frames,
    validate_all_decks,
    validate_deck,
)

__all__ = [
    "new_game", "legal_commands", "apply_command", "view_for", "is_over",
    "scores",
    # supporting types and helpers the server and AI need
    "GameState", "GameConfig", "Command", "PendingDecision", "IllegalCommand",
    "Card", "FrameSpec", "Pos", "Tile", "Zone", "ZONES", "Team", "Phase",
    "DecisionKind", "FlatBoard", "set_board_factory",
    "load_cards", "load_frames", "load_deck", "available_decks",
    "validate_deck", "validate_all_decks", "DeckReport",
    "catalogue_json", "card_json",
]


def new_game(config: GameConfig) -> GameState:
    """Build and start a game, advanced to the first decision."""
    state = _resolve.new_game(config)
    return _resolve.advance(state)


def legal_commands(state: GameState, seat: Team) -> list[Command]:
    """Every command `seat` may legally send right now (empty if not its turn)."""
    return _resolve.legal_commands(state, seat)


def apply_command(state: GameState, cmd: Command) -> GameState:
    """Apply one decision and advance. Returns a NEW state; `state` is untouched."""
    nxt = state.clone()
    _resolve.handle_command(nxt, cmd)
    return _resolve.advance(nxt)


def view_for(state: GameState, seat: Team) -> dict:
    """The redacted view for `seat` -- no other seat's hidden information."""
    return _view_for(state, seat)


def is_over(state: GameState) -> bool:
    return state.phase == "finished"


def scores(state: GameState) -> dict[int, int]:
    """Victory points: 1 per enemy frame defeated plus objectives scored."""
    return dict(victory_points(state))
