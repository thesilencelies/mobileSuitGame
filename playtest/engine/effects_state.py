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
        if not allied and is_cloaked(state, other):
            continue        # it is not on the table as far as the enemy knows
        gap = distance(state, frame.pos, other.pos)
        if gap is None or gap > reach:
            continue
        out.append(other)
    out.sort(key=lambda f: (f.seat == frame.seat, f.id))
    return out


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
