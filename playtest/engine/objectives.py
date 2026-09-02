"""The scripted objectives.

Every terrain row that carries points is an objective; `Helpcard` is a legend,
so :data:`OBJECTIVE_NAMES` is the real set. The player who brought the card is
the **defender** and scores the green `Defend Points`; the other seat is the
attacker and scores the red `Attack Points`.

Scoring timing is under-specified in the rulebook. This engine assumes:

* **Latching** -- Power Reactors, The Tower, Fugitive, The Egg, Riverside and
  Car Park lock in the moment their condition is met and stay locked. All six
  turn on something that cannot be undone: a destroyed token, a token that
  reached its tile, two turns already stood.
* **End of game** -- Shiny Thing, Triangle, Holo Spires, Church, Solar Farm
  and Lake Crosses are evaluated once, after turn 5, because until then the
  ground (or the relic) can still change hands.

Carried tokens are generic (rules.tex:826): any token marked `carriable` is
picked up by a frame that *enters its tile*, is dragged along as that frame
moves, and is dropped toward the damage source when its carrier is hit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

from .state import (
    FrameState,
    GameState,
    ObjectiveState,
    TokenState,
)
from .terrain import CARD_ROWS
from .types import PendingDecision, Pos, Team, team_name


@dataclass(frozen=True)
class TokenSpec:
    """The tokens one objective puts on the table.

    `count` and `hp` mirror the `Tokens` column and the card's rules text --
    the CSV states how many there are, this states what they *do*, which is
    the part no column can carry.
    """

    kind: str
    count: int
    hp: int = 0
    #: Taken off every zone of every attack that hits it (The Tower).
    damage_reduction: int = 0
    #: Picked up by a frame that enters its tile (rules.tex:826).
    carriable: bool = False
    #: Who may pick it up: `"any"` side, or only the objective `"owner"`'s.
    holders: str = "any"
    #: Tiles it may be moved each turn, and the initiative it moves at. The
    #: side that created it moves it (rules.tex:829).
    movement: int = 0
    initiative: int = 0
    #: Who creates them, relative to the objective's owner. The creator owns
    #: them -- which is also who may not shoot them, and who moves them.
    creator: str = "owner"
    #: Where they start: `"spawn"` is the card's own `tkn` cells, `"withheld"`
    #: is off the board until the objective's own rule brings it on, and the
    #: rest are regions the creator picks tiles from, read relative to the
    #: creator's own edge.
    start: str = "spawn"
    #: How the placement decision reads to whoever is placing them.
    prompt: str = ""


#: Objective -> the tokens it brings. Objectives with no token are absent.
#: Used by `create_objective` so setup does not have to know.
OBJECTIVE_TOKENS: Mapping[str, TokenSpec] = {
    "Power Reactors": TokenSpec("reactor", 4, hp=2),
    "Shiny Thing": TokenSpec("shiny", 1, carriable=True),
    "The Tower": TokenSpec("tower", 1, hp=4, damage_reduction=1),
    "Fugitive": TokenSpec(
        "fugitive", 1, carriable=True, holders="owner",
        start="enemy_back_row",
        prompt="Fugitive: hide it anywhere in the enemy back row",
    ),
    "Lake Crosses": TokenSpec("relic", 1, carriable=True, start="withheld"),
    "Riverside": TokenSpec(
        "gang", 3, hp=1, movement=1, initiative=1,
        creator="enemy", start="off_back_row",
        prompt="Gangs: put a gang anywhere off your own back row",
    ),
    "Car Park": TokenSpec(
        "refugee", 3, hp=1, movement=1, initiative=1,
        start="enemy_half",
        prompt="Refugees: put a refugee anywhere in the enemy half",
    ),
}

#: Every objective the engine scripts.
OBJECTIVE_NAMES: tuple[str, ...] = (
    "Power Reactors", "Shiny Thing", "Triangle", "Fugitive",
    "Holo Spires", "Church", "The Tower", "The Egg",
    "Riverside", "Solar Farm", "Lake Crosses", "Car Park", "Dome Campus",
)

#: Objectives that latch the instant their condition is met.
LATCHING: frozenset[str] = frozenset({
    "Power Reactors", "The Tower", "Fugitive", "The Egg",
    "Riverside", "Car Park", "Dome Campus",
})


def other_seat(state: GameState, seat: Team) -> Team:
    for other in state.seats:
        if other != seat:
            return other
    return seat


def creator_seat(state: GameState, objective: ObjectiveState) -> Team:
    """The side that creates, owns and moves this objective's tokens."""
    spec = OBJECTIVE_TOKENS.get(objective.name)
    if spec is not None and spec.creator == "enemy":
        return other_seat(state, objective.owner)
    return objective.owner


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
    text: str = "",
    card_tiles: Sequence[Pos] = (),
) -> ObjectiveState:
    """Register an objective and spawn its tokens onto `spawns`.

    Tokens whose spec names a starting *region* rather than the card's own
    `tkn` cells come on with no position at all: they are put down by their
    creator once the frames are deployed, through `setup_decision`.
    """
    objective = ObjectiveState(
        name=name,
        owner=owner,
        defend=defend,
        attack=attack,
        tiles=tuple(tiles),
        text=text,
        card_tiles=tuple(card_tiles),
    )
    state.objectives.append(objective)
    spec = OBJECTIVE_TOKENS.get(name)
    if spec is None:
        return objective
    placed = spec.start == "spawn"
    places = list(spawns) or list(tiles)
    token_ids: list[str] = []
    for index in range(spec.count):
        pos: Optional[Pos] = None
        if placed and places:
            pos = places[index] if index < len(places) else places[-1]
        token_id = state.next_uid("t")
        state.tokens[token_id] = TokenState(
            id=token_id,
            kind=spec.kind,
            pos=pos,
            hp=spec.hp,
            max_hp=spec.hp,
            owner=creator_seat(state, objective),
            objective=name,
            carriable=spec.carriable,
            holders=owner if spec.holders == "owner" else None,
            damage_reduction=spec.damage_reduction,
            movement=spec.movement,
            initiative=spec.initiative,
        )
        token_ids.append(token_id)
    objective.token_ids = tuple(token_ids)
    return objective


def tokens_of(state: GameState, objective: ObjectiveState) -> list[TokenState]:
    return [state.tokens[t] for t in objective.token_ids if t in state.tokens]


def objective_named(state: GameState, name: str) -> Optional[ObjectiveState]:
    for objective in state.objectives:
        if objective.name == name:
            return objective
    return None


# --------------------------------------------------------------------------
# Individual scorers -- return the seat that scores, or None
# --------------------------------------------------------------------------


def _all_dead(state: GameState, obj: ObjectiveState) -> bool:
    tokens = tokens_of(state, obj)
    return bool(tokens) and not any(t.alive for t in tokens)


def _score_reactors(state: GameState, obj: ObjectiveState) -> Optional[Team]:
    destroyed = sum(1 for t in tokens_of(state, obj) if not t.alive)
    if destroyed >= 3:
        return other_seat(state, obj.owner)
    return obj.owner if state.phase == "finished" else None


def _score_tower(state: GameState, obj: ObjectiveState) -> Optional[Team]:
    if _all_dead(state, obj):
        return other_seat(state, obj.owner)
    return obj.owner if state.phase == "finished" else None


def _score_fugitive(state: GameState, obj: ObjectiveState) -> Optional[Team]:
    for token in tokens_of(state, obj):
        if token.pos is not None and token.pos in obj.tiles:
            return obj.owner
    return other_seat(state, obj.owner) if state.phase == "finished" else None


def _score_egg(state: GameState, obj: ObjectiveState) -> Optional[Team]:
    return obj.memo.get("scored")


def _score_held(state: GameState, obj: ObjectiveState) -> Optional[Team]:
    """Whoever is holding the token when the game ends.

    Shared by the Shiny Thing and the Lake Crosses relic: both are carried
    tokens whose only question is whose frame has it at the end. A token lying
    on the ground scores for nobody -- it can still be walked onto.
    """
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


def _score_gangs(state: GameState, obj: ObjectiveState) -> Optional[Team]:
    """Riverside: the defender scores by clearing every gang off the map."""
    if _all_dead(state, obj):
        return obj.owner
    return other_seat(state, obj.owner) if state.phase == "finished" else None


def _score_refugees(state: GameState, obj: ObjectiveState) -> Optional[Team]:
    """Car Park: the attacker scores only by killing all three refugees."""
    if _all_dead(state, obj):
        return other_seat(state, obj.owner)
    return obj.owner if state.phase == "finished" else None


def _score_farm(state: GameState, obj: ObjectiveState) -> Optional[Team]:
    """Solar Farm: most charge banked over the game. A tie scores for nobody."""
    if state.phase != "finished":
        return None
    charge: dict[Team, int] = obj.memo.get("charge") or {}
    if not charge:
        return None
    best = max(charge.values())
    leaders = [seat for seat, value in charge.items() if value == best]
    return leaders[0] if best > 0 and len(leaders) == 1 else None


def _score_bomb(state: GameState, obj: ObjectiveState) -> Optional[Team]:
    """Dome Campus: latched by `end_of_turn` when the carrier is on the site."""
    scored = obj.memo.get("scored")
    if scored is not None:
        return scored
    return obj.owner if state.phase == "finished" else None


SCORERS: Mapping[str, Callable[[GameState, ObjectiveState], Optional[Team]]] = {
    "Power Reactors": _score_reactors,
    "The Tower": _score_tower,
    "Fugitive": _score_fugitive,
    "The Egg": _score_egg,
    "Shiny Thing": _score_held,
    "Lake Crosses": _score_held,
    "Triangle": _score_triangle,
    "Holo Spires": _score_spires,
    "Church": _score_church,
    "Riverside": _score_gangs,
    "Car Park": _score_refugees,
    "Solar Farm": _score_farm,
    "Dome Campus": _score_bomb,
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
            state.note(f"{objective.name} is scored by {team_name(seat)}")
            after = ON_LATCH.get(objective.name)
            if after is not None:
                after(state, objective, seat)


def _extracted(state: GameState, obj: ObjectiveState, seat: Team) -> None:
    """The fugitive is off the board once it is out.

    "Extraction: ... Defenders score if it reaches the objective point" -- it
    has reached it, so it is gone: nothing left to shoot, to carry or to take
    back. Only on the defender's score; when the attackers take it at the end
    of the game the token is still sitting wherever it was stopped.
    """
    if seat != obj.owner:
        return
    for token in tokens_of(state, obj):
        if not token.alive:
            continue
        carrier = state.frames.get(token.carrier or "")
        token.alive = False
        token.pos = None
        token.carrier = None
        state.note(
            f"{token_label(token)} is extracted"
            + (f" by {carrier.id}" if carrier else "")
        )


#: What happens to an objective's pieces the moment it latches. Most leave
#: them on the table -- a destroyed reactor is still rubble in the way.
ON_LATCH: Mapping[str, Any] = {
    "Fugitive": _extracted,
}


# --------------------------------------------------------------------------
# Setup: tokens that are put down, and the frame that carries the bomb
# --------------------------------------------------------------------------


def _edge_rows(state: GameState, seat: Team, depth: int) -> range:
    """The `depth` tile rows nearest `seat`'s own board edge."""
    from . import setup as _setup

    height = state.board.height
    near_top = _setup.deployment_row(state.board, seat) == 0
    return range(0, depth) if near_top else range(height - depth, height)


def _free(state: GameState, pos: Pos) -> bool:
    tile = state.board.tile(pos)
    if tile.impassable or tile.obstacle:
        return False
    if state.frame_at(pos) is not None:
        return False
    return not any(t.alive and t.pos == pos for t in state.tokens.values())


def placement_tiles(state: GameState, objective: ObjectiveState) -> list[Pos]:
    """Where this objective's tokens may be put down, in board order.

    Regions are read relative to the *creator* -- the side holding the pen --
    because that is how the cards word them: the Fugitive's owner hides it in
    "the enemy back row", Riverside's opponent puts its gangs anywhere off
    "their back row of cards".
    """
    spec = OBJECTIVE_TOKENS.get(objective.name)
    if spec is None or state.board is None or spec.start in ("spawn", "withheld"):
        return []
    seat = creator_seat(state, objective)
    enemy = other_seat(state, seat)
    if spec.start == "enemy_back_row":
        rows = set(_edge_rows(state, enemy, 1))
    elif spec.start == "enemy_half":
        rows = set(_edge_rows(state, enemy, state.board.height // 2))
    elif spec.start == "off_back_row":
        rows = set(range(state.board.height)) - set(_edge_rows(state, seat, CARD_ROWS))
    else:                                     # pragma: no cover - typo guard
        return []
    return [
        Pos(x, y)
        for y in sorted(rows)
        for x in range(state.board.width)
        if _free(state, Pos(x, y))
    ]


def _unplaced(state: GameState, objective: ObjectiveState) -> list[TokenState]:
    spec = OBJECTIVE_TOKENS.get(objective.name)
    if spec is None or spec.start in ("spawn", "withheld"):
        return []
    return [t for t in tokens_of(state, objective) if t.alive and t.pos is None]


def _placement_decision(
    state: GameState, objective: ObjectiveState
) -> Optional[PendingDecision]:
    spec = OBJECTIVE_TOKENS[objective.name]
    while True:
        waiting = _unplaced(state, objective)
        free = placement_tiles(state, objective)
        if not waiting or not free:
            return None
        # One legal tile is not a decision. Take it and come round again --
        # the next token may have a real choice once this one is down.
        if len(free) > 1:
            break
        place_token(state, waiting[0], free[0])
    seat = creator_seat(state, objective)
    left = min(len(waiting), len(free))
    return PendingDecision(
        kind="place_objective",
        seat=seat,
        prompt=(
            spec.prompt or f"{objective.name}: place a token"
        ) + (f" ({left} left)" if left > 1 else ""),
        options=[
            {"token": waiting[0].id, "x": p.x, "y": p.y} for p in free
        ],
        pick_min=left,
        pick_max=left,
        pick_kind="place",
    )


def place_token(state: GameState, token: TokenState, pos: Pos) -> None:
    token.pos = pos
    state.note(f"the {token.kind} is placed at ({pos.x},{pos.y})")


def _bomb_decision(
    state: GameState, objective: ObjectiveState
) -> Optional[PendingDecision]:
    """Dome Campus: "the attacker chooses one frame to be the bomb carrier".

    Nothing in the card makes the choice secret, and a bomb nobody can see is
    a bomb nobody can stop, so the carrier is public once chosen.
    """
    if objective.memo.get("carrier"):
        return None
    seat = other_seat(state, objective.owner)
    carriers = [
        f for f in state.frames.values()
        if f.seat == seat and f.alive and f.pos is not None
    ]
    if not carriers:
        return None
    if len(carriers) == 1:
        set_bomb_carrier(state, objective, carriers[0].id)
        return None
    return PendingDecision(
        kind="choose_frame",
        seat=seat,
        prompt="Stop the bomb: which of your frames carries it?",
        options=[{"frame": f.id, "name": f.spec.name} for f in carriers],
        pick_kind="frame",
    )


def set_bomb_carrier(
    state: GameState, objective: ObjectiveState, frame_id: str
) -> None:
    objective.memo["carrier"] = frame_id
    state.note(f"{frame_id} is carrying the bomb")


def setup_decision(state: GameState) -> Optional[PendingDecision]:
    """Anything an objective still wants once every frame is deployed.

    Both kinds of question wait for deployment on purpose: you hide a fugitive
    (or pick who runs the bomb in) knowing where the squads actually stand.
    """
    if state.board is None:
        return None
    for objective in state.objectives:
        decision = None
        if objective.name == "Dome Campus":
            decision = _bomb_decision(state, objective)
        elif objective.name in OBJECTIVE_TOKENS:
            decision = _placement_decision(state, objective)
        if decision is not None:
            return decision
    return None


# --------------------------------------------------------------------------
# Tokens that move
# --------------------------------------------------------------------------


def mobile_tokens(state: GameState) -> list[TokenState]:
    """Objective tokens their creator moves each turn (rules.tex:829)."""
    return [
        t for t in state.tokens.values()
        if t.alive and t.movement > 0 and t.pos is not None and t.owner is not None
    ]


def token_decision(
    state: GameState, top: Optional[int]
) -> Optional[PendingDecision]:
    """Move one token whose initiative has come round. `None` when none has.

    `top` is the highest initiative still waiting to act, so a gang with
    initiative 1 does not shuffle off before the cards that outrank it -- the
    same gate the drones use.
    """
    if state.board is None:
        return None
    for token in mobile_tokens(state):
        if token.moved_turn == state.turn:
            continue
        if top is not None and top > token.initiative:
            continue
        token.moved_turn = state.turn
        options = [
            dict(option, token=token.id)
            for option in state.walk_options(token, token.movement)
        ]
        if len(options) <= 1:
            continue
        return PendingDecision(
            kind="move_token",
            seat=token.owner,
            prompt=f"Move the {token.kind} (up to {token.movement})",
            options=options,
            pick_kind="move",
        )
    return None


def move_token(state: GameState, token: TokenState, pos: Pos) -> None:
    token.pos = pos
    state.note(f"the {token.kind} moves to ({pos.x},{pos.y})")
    latch_objectives(state)


# --------------------------------------------------------------------------
# Hooks the rest of the engine calls
# --------------------------------------------------------------------------


def _carried_tokens(state: GameState, frame_id: str) -> list[TokenState]:
    return [t for t in state.tokens.values() if t.carrier == frame_id and t.alive]


def token_label(token: TokenState) -> str:
    return {
        "shiny": "the Shiny Thing",
        "fugitive": "the fugitive",
        "relic": "the relic",
    }.get(token.kind, f"the {token.kind}")


def claim_token(state: GameState, token: TokenState) -> bool:
    """Whoever is standing on a loose token has it.

    "If a frame is in the same tile the token is in, that frame picks up the
    token" (rules.tex Tokens) -- a state of the board, not a trigger on
    entering it, which is why this is asked both when a frame arrives and when
    a token is dropped. Dropping is the case that matters: a melee hit knocks
    the relic into the tile the attacker is standing in, and "so a melee hit
    transfers ownership" is the rulebook's own note on it.

    The fugitive is the only token that names who may carry it ("can be held
    by allies"), and that is on the token as `holders`.
    """
    if not token.alive or token.carrier is not None or not token.carriable:
        return False
    if token.pos is None:
        return False
    frame = state.frame_at(token.pos)
    if frame is None or not frame.alive:
        return False
    if token.holders is not None and token.holders != frame.seat:
        return False
    token.carrier = frame.id
    state.note(f"{frame.id} picks up {token_label(token)}")
    return True


def on_move(state: GameState, frame: FrameState, old_pos: Optional[Pos]) -> None:
    """Pick up / drag carried tokens after a frame moves."""
    if frame.pos is None or state.board is None:
        return
    for token in list(state.tokens.values()):
        if token.pos == frame.pos:
            claim_token(state, token)
    for token in _carried_tokens(state, frame.id):
        token.pos = frame.pos
    latch_objectives(state)


def on_damage(
    state: GameState,
    defender: FrameState,
    attacker: Optional[FrameState],
    *,
    source_pos: Optional[Pos] = None,
) -> None:
    """A damaged frame drops whatever it is carrying, toward the damage.

    `source_pos` is where the damage came from, which is not always the
    attacker's own tile: a drone's shot belongs to the frame that summoned it
    but is fired from the drone, and the token drops toward the muzzle.
    """
    if source_pos is None and attacker is not None:
        source_pos = attacker.pos
    dropped = False
    for token in _carried_tokens(state, defender.id):
        token.carrier = None
        token.pos = _drop_tile(state, defender, source_pos)
        state.note(f"{defender.id} drops {token_label(token)}")
        # It may land at somebody's feet -- usually the attacker's, since a
        # melee hit comes from an adjacent tile.
        claim_token(state, token)
        dropped = True
    if dropped:
        latch_objectives(state)


def _drop_tile(
    state: GameState, defender: FrameState, source: Optional[Pos]
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
    if source is None:
        return sorted(candidates)[0]

    def nearness(pos: Pos) -> tuple[int, int]:
        # The game measures range as Chebyshev, but that leaves ties around a
        # diagonal source; squared Euclidean picks the genuinely nearest tile.
        return (
            state.board.distance(pos, source),
            (pos.x - source.x) ** 2 + (pos.y - source.y) ** 2,
        )

    return min(sorted(candidates), key=nearness)


def _egg_streaks(state: GameState, objective: ObjectiveState) -> None:
    streaks: dict[str, int] = objective.memo.setdefault("streaks", {})
    for frame in state.frames.values():
        standing = (
            frame.alive and frame.pos is not None and frame.pos in objective.tiles
        )
        streaks[frame.id] = streaks.get(frame.id, 0) + 1 if standing else 0
        if streaks[frame.id] >= 2 and objective.memo.get("scored") is None:
            objective.memo["scored"] = frame.seat


def _farm_charge(state: GameState, objective: ObjectiveState) -> None:
    """"For each frame that ends a turn on the farm, that team gets 1 charge"."""
    charge: dict[Team, int] = objective.memo.setdefault("charge", {})
    for frame in state.frames.values():
        if frame.alive and frame.pos is not None and frame.pos in objective.tiles:
            charge[frame.seat] = charge.get(frame.seat, 0) + 1


def _ritual_frames(
    state: GameState, objective: ObjectiveState, seat: Team
) -> list[FrameState]:
    """That seat's frames standing on the platforms, in a stable order."""
    return sorted(
        (
            f for f in state.frames.values()
            if f.seat == seat and f.alive and f.pos is not None
            and f.pos in objective.tiles
        ),
        key=lambda f: f.id,
    )


def _lake_ritual(state: GameState, objective: ObjectiveState) -> None:
    """"The first team to end a turn with a frame on both platforms".

    The relic is off the board until then. If both sides complete the ritual
    on the same turn nobody takes it -- there is no "first", and handing it to
    a seat by index would decide a two-point objective on seating order.

    Which frame ends up holding it is the winner's call ("held by one of those
    frames"), and it matters: the relic is dropped when its carrier is
    damaged, so the choice is which machine you are willing to have shot. So
    this only records the win; `cleanup_decision` asks the question.
    """
    tokens = tokens_of(state, objective)
    if not tokens or any(t.pos is not None or t.carrier for t in tokens):
        return
    if objective.memo.get("ritual") is not None:
        return
    winners = [
        seat for seat in state.seats
        if len({f.pos for f in _ritual_frames(state, objective, seat)})
        >= len(set(objective.tiles)) >= 2
    ]
    if len(winners) != 1:
        return
    objective.memo["ritual"] = winners[0]
    state.note(f"{team_name(winners[0])} completes the ritual")


def _give_relic(
    state: GameState, objective: ObjectiveState, frame: FrameState
) -> None:
    tokens = tokens_of(state, objective)
    if not tokens:
        return
    relic = tokens[0]
    relic.pos = frame.pos
    relic.carrier = frame.id
    objective.memo["ritual"] = None
    state.note(f"{frame.id} takes the relic")


def cleanup_decision(state: GameState) -> Optional[PendingDecision]:
    """A choice the end of the turn owes somebody, or `None`.

    Cleanup normally runs straight through, but the Lake Ritual hands out a
    token and the card says which frame holds it is the winner's choice. Asked
    from the driver rather than from inside `cleanup_phase` so the turn does
    not roll over with the question unanswered -- and so it still gets asked
    when the ritual is completed on the last turn of the game.
    """
    for objective in state.objectives:
        seat = objective.memo.get("ritual")
        if seat is None:
            continue
        options = _ritual_frames(state, objective, int(seat))
        if not options:
            objective.memo["ritual"] = None      # they are all dead now
            continue
        if len(options) == 1:
            _give_relic(state, objective, options[0])
            continue
        return PendingDecision(
            kind="choose_frame",
            seat=int(seat),
            prompt="The Lake Ritual: which frame carries the relic?",
            options=[{"frame": f.id, "name": f.spec.name} for f in options],
            pick_kind="frame",
        )
    return None


def take_relic(state: GameState, frame: FrameState) -> None:
    """Answer to `cleanup_decision`: this frame holds the relic."""
    for objective in state.objectives:
        if objective.memo.get("ritual") is None:
            continue
        if frame in _ritual_frames(state, objective, int(objective.memo["ritual"])):
            _give_relic(state, objective, frame)
            return


def _bomb_arrival(state: GameState, objective: ObjectiveState) -> None:
    """"If this frame ends a turn on the site, attackers score"."""
    if objective.memo.get("scored") is not None:
        return
    frame = state.frames.get(str(objective.memo.get("carrier") or ""))
    if frame is None or not frame.alive or frame.pos is None:
        return
    if frame.pos in objective.tiles:
        objective.memo["scored"] = frame.seat


def end_of_turn(state: GameState) -> None:
    """Everything an objective counts per turn, then latch what is now met."""
    for objective in state.objectives:
        if objective.latched is not None:
            continue
        if objective.name == "The Egg":
            _egg_streaks(state, objective)
        elif objective.name == "Solar Farm":
            _farm_charge(state, objective)
        elif objective.name == "Lake Crosses":
            _lake_ritual(state, objective)
        elif objective.name == "Dome Campus":
            _bomb_arrival(state, objective)
    latch_objectives(state)
