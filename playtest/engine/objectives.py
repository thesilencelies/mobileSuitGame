"""The eight scripted objectives.

Nine terrain rows carry non-zero points; `Helpcard` is a legend, so eight are
real. The player who brought the card is the **defender** and scores the green
`Defend Points`; the other seat is the attacker and scores the red
`Attack Points`.

Scoring timing is under-specified in the rulebook. This engine assumes:

* **Latching** -- Power Reactors, The Tower, Fugitive and The Egg lock in the
  moment their condition is met and stay locked.
* **End of game** -- Shiny Thing, Triangle, Holo Spires and Church are
  evaluated once, after turn 5.
"""

from __future__ import annotations

from typing import Callable, Mapping, Optional, Sequence

from .state import (
    FrameState,
    GameState,
    ObjectiveState,
    TokenState,
)
from .types import Pos, Team

#: Objective -> (token kind, token count, hp each). Objectives with no token
#: are absent. Used by `create_objective` so setup does not have to know.
OBJECTIVE_TOKENS: Mapping[str, tuple[str, int, int]] = {
    "Power Reactors": ("reactor", 4, 2),
    "Shiny Thing": ("shiny", 1, 0),
    "Fugitive": ("fugitive", 1, 0),
    "The Tower": ("tower", 1, 4),
}

#: Every objective the engine scripts.
OBJECTIVE_NAMES: tuple[str, ...] = (
    "Power Reactors", "Shiny Thing", "Triangle", "Fugitive",
    "Holo Spires", "Church", "The Tower", "The Egg",
)

#: Objectives that latch the instant their condition is met.
LATCHING: frozenset[str] = frozenset(
    {"Power Reactors", "The Tower", "Fugitive", "The Egg"}
)


def other_seat(state: GameState, seat: Team) -> Team:
    for other in state.seats:
        if other != seat:
            return other
    return seat


# --------------------------------------------------------------------------
# Construction (called by setup, workstream B1)
# --------------------------------------------------------------------------


def create_objective(
    state: GameState,
    name: str,
    owner: Team,
    *,
    defend: int,
    attack: int,
    tiles: Sequence[Pos] = (),
    spawns: Sequence[Pos] = (),
) -> ObjectiveState:
    """Register an objective and spawn its tokens onto `spawns`."""
    token_ids: list[str] = []
    spec = OBJECTIVE_TOKENS.get(name)
    if spec is not None:
        kind, count, hp = spec
        places = list(spawns) or list(tiles)
        for index in range(count):
            pos = places[index] if index < len(places) else (
                places[-1] if places else None
            )
            token_id = state.next_uid("t")
            state.tokens[token_id] = TokenState(
                id=token_id,
                kind=kind,
                pos=pos,
                hp=hp,
                max_hp=hp,
                owner=owner,
                objective=name,
            )
            token_ids.append(token_id)
    objective = ObjectiveState(
        name=name,
        owner=owner,
        defend=defend,
        attack=attack,
        tiles=tuple(tiles),
        token_ids=tuple(token_ids),
    )
    state.objectives.append(objective)
    return objective


def tokens_of(state: GameState, objective: ObjectiveState) -> list[TokenState]:
    return [state.tokens[t] for t in objective.token_ids if t in state.tokens]


# --------------------------------------------------------------------------
# Individual scorers -- return the seat that scores, or None
# --------------------------------------------------------------------------


def _score_reactors(state: GameState, obj: ObjectiveState) -> Optional[Team]:
    destroyed = sum(1 for t in tokens_of(state, obj) if not t.alive)
    if destroyed >= 3:
        return other_seat(state, obj.owner)
    return obj.owner if state.phase == "finished" else None


def _score_tower(state: GameState, obj: ObjectiveState) -> Optional[Team]:
    tokens = tokens_of(state, obj)
    if tokens and not any(t.alive for t in tokens):
        return other_seat(state, obj.owner)
    return obj.owner if state.phase == "finished" else None


def _score_fugitive(state: GameState, obj: ObjectiveState) -> Optional[Team]:
    for token in tokens_of(state, obj):
        if token.pos is not None and token.pos in obj.tiles:
            return obj.owner
    return other_seat(state, obj.owner) if state.phase == "finished" else None


def _score_egg(state: GameState, obj: ObjectiveState) -> Optional[Team]:
    return obj.memo.get("scored")


def _score_shiny(state: GameState, obj: ObjectiveState) -> Optional[Team]:
    if state.phase != "finished":
        return None
    for token in tokens_of(state, obj):
        if token.carrier and token.carrier in state.frames:
            return state.frames[token.carrier].seat
    return None


def _teams_on(state: GameState, tiles: Sequence[Pos]) -> set[Team]:
    return {
        f.seat for f in state.frames.values()
        if f.alive and f.pos is not None and f.pos in tiles
    }


def _score_triangle(state: GameState, obj: ObjectiveState) -> Optional[Team]:
    if state.phase != "finished":
        return None
    teams = _teams_on(state, obj.tiles)
    return next(iter(teams)) if len(teams) == 1 else None


def _score_spires(state: GameState, obj: ObjectiveState) -> Optional[Team]:
    if state.phase != "finished":
        return None
    attacker = other_seat(state, obj.owner)
    return attacker if attacker in _teams_on(state, obj.tiles) else obj.owner


def _score_church(state: GameState, obj: ObjectiveState) -> Optional[Team]:
    if state.phase != "finished" or state.board is None:
        return None
    teams: set[Team] = set()
    for frame in state.frames.values():
        if not frame.alive or frame.pos is None:
            continue
        if any(state.board.distance(frame.pos, tile) <= 2 for tile in obj.tiles):
            teams.add(frame.seat)
    return next(iter(teams)) if len(teams) == 1 else None


SCORERS: Mapping[str, Callable[[GameState, ObjectiveState], Optional[Team]]] = {
    "Power Reactors": _score_reactors,
    "The Tower": _score_tower,
    "Fugitive": _score_fugitive,
    "The Egg": _score_egg,
    "Shiny Thing": _score_shiny,
    "Triangle": _score_triangle,
    "Holo Spires": _score_spires,
    "Church": _score_church,
}


def objective_score(
    state: GameState, objective: ObjectiveState
) -> tuple[Optional[Team], int]:
    """`(seat, points)` for one objective. `(None, 0)` if nobody scores it."""
    seat = objective.latched
    if seat is None:
        scorer = SCORERS.get(objective.name)
        seat = scorer(state, objective) if scorer else None
    if seat is None:
        return None, 0
    value = objective.defend if seat == objective.owner else objective.attack
    return seat, value


def latch_objectives(state: GameState) -> None:
    """Lock in any latching objective whose condition is now met."""
    for objective in state.objectives:
        if objective.latched is not None or objective.name not in LATCHING:
            continue
        scorer = SCORERS.get(objective.name)
        if scorer is None:
            continue
        seat = scorer(state, objective)
        if seat is not None:
            objective.latched = seat
            state.note(f"{objective.name} is scored by seat {seat}")


# --------------------------------------------------------------------------
# Hooks the rest of the engine calls
# --------------------------------------------------------------------------


def _carried_tokens(state: GameState, frame_id: str) -> list[TokenState]:
    return [t for t in state.tokens.values() if t.carrier == frame_id and t.alive]


def on_move(state: GameState, frame: FrameState, old_pos: Optional[Pos]) -> None:
    """Pick up / drag tokens after a frame moves.

    * Shiny Thing: picked up on contact (moving onto its tile).
    * Fugitive: "moves along with any ally touching it" -- read here as being
      carried by an adjacent frame of the defending team, the same way the
      Shiny Thing is carried.
    """
    if frame.pos is None or state.board is None:
        return
    for token in state.tokens.values():
        if not token.alive or token.carrier is not None:
            continue
        if token.kind == "shiny" and token.pos == frame.pos:
            token.carrier = frame.id
            state.note(f"{frame.spec.name} picks up the Shiny Thing")
        elif (
            token.kind == "fugitive"
            and token.owner == frame.seat
            and token.pos is not None
            and state.board.distance(token.pos, frame.pos) <= 1
        ):
            token.carrier = frame.id
            state.note(f"{frame.spec.name} takes the fugitive in tow")
    for token in _carried_tokens(state, frame.id):
        token.pos = frame.pos
    latch_objectives(state)


def on_damage(
    state: GameState, defender: FrameState, attacker: Optional[FrameState]
) -> None:
    """Damaged frames drop the Shiny Thing toward the damage source."""
    for token in _carried_tokens(state, defender.id):
        if token.kind != "shiny":
            continue
        token.carrier = None
        token.pos = _drop_tile(state, defender, attacker)
        state.note(f"{defender.spec.name} drops the Shiny Thing")


def _drop_tile(
    state: GameState, defender: FrameState, attacker: Optional[FrameState]
) -> Optional[Pos]:
    """The adjacent tile nearest the damage source."""
    if defender.pos is None or state.board is None:
        return defender.pos
    candidates = [
        p for p in state.board.neighbours(defender.pos)
        if not state.board.tile(p).impassable
    ]
    if not candidates:
        return defender.pos
    if attacker is None or attacker.pos is None:
        return sorted(candidates)[0]
    source = attacker.pos

    def nearness(pos: Pos) -> tuple[int, int]:
        # The game measures range as Chebyshev, but that leaves ties around a
        # diagonal source; squared Euclidean picks the genuinely nearest tile.
        return (
            state.board.distance(pos, source),
            (pos.x - source.x) ** 2 + (pos.y - source.y) ** 2,
        )

    return min(sorted(candidates), key=nearness)


def end_of_turn(state: GameState) -> None:
    """The Egg's consecutive-turn streaks, then latch anything that is met."""
    for objective in state.objectives:
        if objective.name != "The Egg" or objective.latched is not None:
            continue
        streaks: dict[str, int] = objective.memo.setdefault("streaks", {})
        for frame in state.frames.values():
            standing = (
                frame.alive and frame.pos is not None and frame.pos in objective.tiles
            )
            streaks[frame.id] = streaks.get(frame.id, 0) + 1 if standing else 0
            if streaks[frame.id] >= 2 and objective.memo.get("scored") is None:
                objective.memo["scored"] = frame.seat
    latch_objectives(state)
