"""Scratch state and shared queries for card effects.

Pilot and drone text needs three kinds of memory, and each has a home:

* **This turn only** -- `FrameState.turn_flags`, which the engine already
  clears at the start of every planning phase. Snipers aim's range bonus and
  Relentless Assault's repeat marker live there.
* **Across turns** -- the card itself. A card with a Persistence value is set
  aside at cleanup and discarded when it expires (`resolve.cleanup_phase`), so
  "is Fog of war still up?" is answered by looking for the card in the frame's
  `aside` pile rather than by inventing a parallel timer that could drift out
  of step with the card on the table. That is what `card_active` does.
* **Everything else** -- one namespaced bag on the state, reached through
  `bag()`. Deliberately a single dict rather than fields scattered through
  `state.py`, which several workstreams are editing at once.

The bag is a plain dict of plain data so `GameState.clone()`'s deep copy keeps
`apply_command` pure, and so nothing in here can hold a stale reference to a
frame or card object across a clone.
"""

from __future__ import annotations

from typing import Any, Optional

from .state import FrameState, GameState, TokenState
from .types import Pos, Team

#: Attribute on `GameState` holding the effects bag. Declared as a field in
#: `state.py`; `bag()` still creates it on demand so this module keeps working
#: if that field is ever moved or renamed.
BAG_ATTR = "fx"


def bag(state: GameState) -> dict[str, Any]:
    """The effects scratch bag, created on first use."""
    current = getattr(state, BAG_ATTR, None)
    if not isinstance(current, dict):
        current = {}
        setattr(state, BAG_ATTR, current)
    return current


def slot(state: GameState, name: str) -> dict[str, Any]:
    """One named sub-dict of the bag."""
    return bag(state).setdefault(name, {})


# --------------------------------------------------------------------------
# "Is this card still doing something?"
# --------------------------------------------------------------------------


def card_active(
    state: GameState,
    frame: FrameState,
    key: str,
    *,
    this_turn: bool = True,
    later_turns: bool = True,
) -> bool:
    """True while `key` is in play in front of `frame`.

    `this_turn` covers the turn the card resolved -- it is still in the
    committed row, face up and marked resolved. `later_turns` covers the turns
    it spends in the `aside` pile as a persistent card. A card printed
    "Next turn: ..." is asked for with `this_turn=False`; one printed "this
    turn and next" is asked for with both.
    """
    if later_turns:
        for uid in frame.aside:
            inst = state.cards.get(uid)
            if inst is not None and inst.key == key:
                return True
    if this_turn:
        for uid in frame.committed:
            inst = state.cards.get(uid)
            if (
                inst is not None
                and inst.key == key
                and inst.location == "committed"
                and inst.resolved
            ):
                return True
    return False


def any_frame_with(
    state: GameState,
    key: str,
    *,
    seat: Optional[Team] = None,
    this_turn: bool = True,
    later_turns: bool = True,
) -> list[FrameState]:
    """Every living frame with `key` in play, optionally limited to one seat."""
    return [
        frame
        for frame in state.frames.values()
        if frame.alive
        and (seat is None or frame.seat == seat)
        and card_active(
            state, frame, key, this_turn=this_turn, later_turns=later_turns
        )
    ]


# --------------------------------------------------------------------------
# Tokens the effects create
# --------------------------------------------------------------------------

#: Token kinds this module owns. `barricade` is already understood by
#: `GameState.occupied`, which is why it blocks movement without a board change.
BARRICADE = "barricade"
GRAVITY_WELL = "gravitywell"
PORTAL = "portal"
DRONE = "drone"
IMAGE = "image"
#: Psychic Storm's weather, Rebound's mirror and Cage Fight's walls. The cage
#: blocks movement the way a barricade does, which is why `GameState.occupied`
#: names both.
STORM = "storm"
REBOUND = "rebound"
CAGE = "cage"

#: Token kinds that are *units* rather than scenery: they act on their own and
#: so are what "every unit within 5" (Psychic Storm) means beside the frames.
#: A structure an objective put on the board -- the Tower, a reactor -- and
#: anything a frame can pick up and carry are not units.
UNIT_KINDS = (DRONE,)


def image_records(state: GameState) -> dict:
    """`frame id -> {real, tokens, at}` for every frame hiding among images."""
    return slot(state, "images")


def is_cloaked(state: GameState, frame: FrameState) -> bool:
    """True while Ephemeral Images is hiding this frame among its decoys.

    Lives here rather than in `effects` so the shared queries below can honour
    it without a circular import: a hidden frame must not turn up in any list
    an *enemy* card offers, or the list itself gives its position away.
    """
    record = image_records(state).get(frame.id)
    return bool(record and frame.alive and frame.pos is not None)


def image_positions(state: GameState, frame: FrameState) -> list[Pos]:
    """Where this frame's live Ephemeral Images stand, in board order.

    Empty when the frame is not hiding among any -- which is what makes
    `origins` below fall back to the frame's own tile.
    """
    record = image_records(state).get(frame.id)
    if not record or not frame.alive:
        return []
    spots = []
    for token_id in record.get("tokens", ()):
        token = state.tokens.get(token_id)
        if token is not None and token.alive and token.pos is not None:
            spots.append(token.pos)
    return sorted(set(spots), key=lambda p: (p.y, p.x))


def origins(state: GameState, frame: FrameState) -> list[Pos]:
    """Every tile this frame's own actions may be measured from.

    Its own tile, normally. Behind Ephemeral Images it is the images': "these
    tokens use this frame's actions", and each of them acts from where it
    stands, so anything an action counts -- range, sight, "within N" -- may be
    counted from whichever image suits. That is the whole reason the decoys
    are worth putting down for anything but concealment.
    """
    spots = image_positions(state, frame)
    if spots:
        return spots
    return [frame.pos] if frame.pos is not None else []


def reach_to(
    state: GameState, frame: FrameState, pos: Optional[Pos]
) -> Optional[int]:
    """The shortest distance from any of `frame`'s origins to `pos`."""
    gaps = [
        gap for gap in (distance(state, origin, pos) for origin in origins(state, frame))
        if gap is not None
    ]
    return min(gaps) if gaps else None


def gap_between(
    state: GameState, actor: FrameState, other: FrameState
) -> Optional[int]:
    """Shortest distance between two frames, counting the images of both.

    A frame behind Ephemeral Images is on the table as three pieces, and each
    of them "is a frame in itself for interactions and targeting" -- so it is
    in reach of something if any of its images is, and it reaches something if
    any of its images does.
    """
    gaps = [
        gap
        for pos in origins(state, other)
        for gap in (reach_to(state, actor, pos),)
        if gap is not None
    ]
    return min(gaps) if gaps else None


def spawn_token(
    state: GameState,
    kind: str,
    pos: Optional[Pos],
    *,
    hp: int = 0,
    owner: Optional[Team] = None,
) -> TokenState:
    token = TokenState(
        id=state.next_uid("t"),
        kind=kind,
        pos=pos,
        hp=hp,
        max_hp=hp,
        owner=owner,
    )
    state.tokens[token.id] = token
    return token


def is_unit(token: TokenState) -> bool:
    """True for a token that acts on its own, rather than being scenery.

    Drones, and the objective tokens that move under their own power (a
    Riverside gang, a Car Park refugee). The Tower and the reactors are
    buildings, and the Shiny Thing is luggage -- none of them is a "unit" that
    a card sweeping an area is talking about.
    """
    return token.kind in UNIT_KINDS or token.movement > 0


def tokens_of_kind(state: GameState, kind: str) -> list[TokenState]:
    return [
        t for t in state.tokens.values()
        if t.kind == kind and t.alive and t.pos is not None
    ]


# --------------------------------------------------------------------------
# Geometry helpers shared by several cards
# --------------------------------------------------------------------------


def distance(state: GameState, a: Optional[Pos], b: Optional[Pos]) -> Optional[int]:
    if a is None or b is None or state.board is None:
        return None
    return state.board.distance(a, b)


def frames_within(
    state: GameState,
    frame: FrameState,
    reach: int,
    *,
    side: str = "any",
    include_self: bool = False,
) -> list[FrameState]:
    """Living frames within `reach` of `frame`.

    `side` is `"ally"`, `"enemy"` or `"any"`. Ordered enemies-first then by id
    so the option list a card offers is stable across replays.
    """
    out: list[FrameState] = []
    for other in state.frames.values():
        if not other.alive or other.pos is None:
            continue
        if other.id == frame.id and not include_self:
            continue
        allied = other.seat == frame.seat
        if side == "ally" and not allied:
            continue
        if side == "enemy" and allied:
            continue
        # A cloaked frame is not skipped: its images are what an enemy card
        # picks (`effects._frame_options` offers them, `_target_frame` maps the
        # answer back here), and it is in reach if any of them is.
        gap = gap_between(state, frame, other)
        if gap is None or gap > reach:
            continue
        out.append(other)
    out.sort(key=lambda f: (f.seat == frame.seat, f.id))
    return out


def free_tiles_from(
    state: GameState, frame: FrameState, reach: int
) -> list[Pos]:
    """`free_tiles` measured from any of `frame`'s origins, in board order.

    Behind Ephemeral Images a card that puts something down "within N" may
    measure that N from whichever image suits -- one storm, anywhere in range
    of any of them -- so the legal tiles are the union.
    """
    seen: set[Pos] = set()
    for origin in origins(state, frame):
        seen.update(free_tiles(state, origin, reach))
    return sorted(seen, key=lambda p: (p.y, p.x))


def free_tiles(
    state: GameState, origin: Optional[Pos], reach: int, *, include_origin: bool = False
) -> list[Pos]:
    """Unoccupied, enterable tiles within `reach` of `origin`, in board order."""
    board = state.board
    if board is None or origin is None:
        return []
    taken = state.occupied()
    token_tiles = {t.pos for t in state.tokens.values() if t.alive and t.pos is not None}
    out: list[Pos] = []
    for y in range(board.height):
        for x in range(board.width):
            pos = Pos(x, y)
            if pos == origin and not include_origin:
                continue
            if board.distance(origin, pos) > reach:
                continue
            tile = board.tile(pos)
            if tile.impassable or tile.obstacle:
                continue
            if pos in taken or pos in token_tiles:
                continue
            out.append(pos)
    return out
