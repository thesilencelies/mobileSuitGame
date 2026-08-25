"""A read-only model of what one seat can see.

Everything in this module is built from the dict `view_for(state, seat)`
returns and from the static card catalogue (the same JSON the client fetches
from `GET /api/cards`). Nothing here touches `GameState`, so an agent built on
it *cannot* read another seat's hand, deck order or face-down commitments --
that information is simply not present in its inputs.

The one engine import is `engine.board.Board`, a pure geometry class with no
game state at all. It is constructed here from the tiles in the seat's own
view, so the AI's reachability and line-of-sight answers agree exactly with
the ones the engine will give -- without duplicating 200 lines of clipping
geometry that could then drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

from ..engine.board import Board
from ..engine.types import Pos, Tile, ZONES

# --------------------------------------------------------------------------
# Cards
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CardInfo:
    """One catalogue entry, in the shape the scorer wants."""

    key: str
    name: str
    group: str
    faction: str
    card_type: str
    initiative: tuple[int, ...]
    movement: int
    attacks: Mapping[str, int]
    ranges: Mapping[str, int]
    dtypes: Mapping[str, Optional[str]]
    blocks: Mapping[str, int]
    text: str
    keywords: frozenset[str]
    knockback: int
    persistence: Optional[int]
    #: True when the engine does not implement this card's text. The scorer
    #: models printed stats and never card text, so this does not change how a
    #: card is valued -- it marks text that is inert *in the engine*, which is
    #: what lets `scoring.carries_live_text` stop pruning a card the moment its
    #: effect actually starts working.
    not_implemented: bool = False

    @property
    def init(self) -> int:
        return self.initiative[0] if self.initiative else 0

    @property
    def is_attack(self) -> bool:
        return any(v > 0 for v in self.attacks.values())

    @property
    def attack_zones(self) -> tuple[str, ...]:
        return tuple(z for z in ZONES if self.attacks.get(z, 0) > 0)

    @property
    def block_zones(self) -> tuple[str, ...]:
        return tuple(z for z in ZONES if self.blocks.get(z, 0) > 0)

    @property
    def super_block_zones(self) -> tuple[str, ...]:
        return tuple(z for z in ZONES if self.blocks.get(z, 0) >= 2)

    @property
    def is_ranged(self) -> bool:
        return any(
            self.ranges.get(z, 0) > 0 for z in ZONES if self.attacks.get(z, 0) > 0
        )

    @property
    def is_melee(self) -> bool:
        return self.is_attack and not self.is_ranged

    @property
    def max_range(self) -> int:
        return max(
            (self.ranges.get(z, 0) for z in ZONES if self.attacks.get(z, 0) > 0),
            default=0,
        )

    @property
    def feint(self) -> bool:
        return "feint" in self.keywords

    @property
    def guard_break(self) -> bool:
        return "guardbreak" in self.keywords

    @property
    def committed_kw(self) -> bool:
        return "committed" in self.keywords

    @property
    def close_quarters(self) -> bool:
        return "closequarters" in self.keywords

    @property
    def reload(self) -> bool:
        return "reload" in self.keywords


def card_info(key: str, entry: Mapping[str, Any]) -> CardInfo:
    """Build a `CardInfo` from one `GET /api/cards` entry."""
    zeros = {z: 0 for z in ZONES}
    return CardInfo(
        key=key,
        name=str(entry.get("name", key)),
        group=str(entry.get("group", "")),
        faction=str(entry.get("faction", "")),
        card_type=str(entry.get("type", "")),
        initiative=tuple(int(v) for v in entry.get("initiative", (0,))) or (0,),
        movement=int(entry.get("movement", 0) or 0),
        attacks={**zeros, **{z: int(v or 0) for z, v in (entry.get("attacks") or {}).items()}},
        ranges={**zeros, **{z: int(v or 0) for z, v in (entry.get("ranges") or {}).items()}},
        dtypes={z: (entry.get("dtypes") or {}).get(z) for z in ZONES},
        blocks={**zeros, **{z: int(v or 0) for z, v in (entry.get("blocks") or {}).items()}},
        text=str(entry.get("text", "")),
        keywords=frozenset(entry.get("keywords") or ()),
        knockback=int(entry.get("knockback", 0) or 0),
        persistence=entry.get("persistence"),
        not_implemented="notImplemented" in entry,
    )


class Catalogue:
    """The static card list, indexed the handful of ways the AI needs."""

    def __init__(self, entries: Mapping[str, Mapping[str, Any]]) -> None:
        self.cards: dict[str, CardInfo] = {
            key: card_info(key, entry) for key, entry in entries.items()
        }
        # Register with the scorer's shared key -> card lookup, which the
        # known-blocker test needs. Import here to avoid a cycle.
        from . import scoring

        scoring.set_catalogue(self.cards)

    def __contains__(self, key: object) -> bool:
        return key in self.cards

    def get(self, key: str) -> Optional[CardInfo]:
        return self.cards.get(key)

    def __getitem__(self, key: str) -> CardInfo:
        return self.cards[key]

    def playable_for(self, faction: str) -> list[CardInfo]:
        """Every action card a deck of that faction could legally contain.

        Deck construction is faction-locked, so this is the public prior the
        AI uses for an opponent whose deck it has not seen yet -- exactly what
        a human playtester knows from having read the card list.
        """
        out = []
        for card in self.cards.values():
            if card.card_type == "frame":
                continue
            if card.faction and card.faction.lower() not in ("", "any", "neutral"):
                if card.faction.lower() != (faction or "").lower():
                    continue
            out.append(card)
        return out


# --------------------------------------------------------------------------
# Frames and the board
# --------------------------------------------------------------------------


@dataclass
class CardRef:
    """A card sitting in front of a frame, as this seat can see it."""

    uid: str
    key: Optional[str]          # None when it is face down and not ours
    resolved: bool
    face_down: bool
    echo: bool = False

    @property
    def known(self) -> bool:
        return self.key is not None


@dataclass
class FrameView:
    """One frame as it appears in the seat's view."""

    id: str
    seat: int
    name: str
    faction: str
    pos: Optional[Pos]
    elev: int
    alive: bool
    armour: Mapping[str, int]
    damage: Mapping[str, int]
    last_hit: Mapping[str, bool]
    movement: int
    shields: int
    statuses: Mapping[str, int]
    committed: list[CardRef]
    on_field: list[CardRef]
    aside: list[tuple[str, str]]
    deck_count: int
    discard_count: int
    hand: list[tuple[str, str]] = field(default_factory=list)
    #: Hiding behind Ephemeral Images. Its `pos` is then a guess (the centre of
    #: its images), never a fact -- see `Snapshot._guess_cloaked_positions`.
    cloaked: bool = False

    def remaining(self, zone: str) -> int:
        """Hits this zone can still take before the frame dies."""
        return max(0, int(self.armour.get(zone, 0)) - int(self.damage.get(zone, 0)))

    @property
    def health(self) -> Mapping[str, int]:
        return {z: self.remaining(z) for z in ZONES}

    @property
    def weakest(self) -> str:
        return min(ZONES, key=lambda z: self.remaining(z))

    @property
    def total_remaining(self) -> int:
        return sum(self.remaining(z) for z in ZONES)

    @property
    def flying(self) -> bool:
        return self.name in _FLYING_FRAMES

    def blockers(self) -> list[CardRef]:
        """Cards still in front of this frame that could be spent blocking."""
        return [c for c in self.committed + self.on_field]

    def hidden_blockers(self) -> int:
        return sum(1 for c in self.committed + self.on_field if not c.known)


#: Frame names with the Flying keyword. Public information from Frames.csv,
#: which the client also fetches (`GET /api/frames`).
_FLYING_FRAMES = frozenset({"Hannael"})
#: Frames that may not use ranged weapons at all.
NO_RANGED_FRAMES = frozenset({"Fenrir"})
#: Frame name -> flat bonus to the range of its ranged attacks.
RANGE_BONUS_FRAMES: Mapping[str, int] = {"J7R-Salaryman": 4}


def _card_refs(entries: Iterable[Mapping[str, Any]]) -> list[CardRef]:
    return [
        CardRef(
            uid=str(e.get("uid")),
            key=(str(e["key"]) if e.get("key") is not None else None),
            resolved=bool(e.get("resolved")),
            face_down=bool(e.get("faceDown")),
            echo=bool(e.get("echo")),
        )
        for e in entries
    ]


@dataclass
class ObjectiveView:
    name: str
    owner: int
    defend: int
    attack: int
    tiles: tuple[Pos, ...]
    status: str

    def value_for(self, seat: int) -> int:
        return self.defend if seat == self.owner else self.attack

    @property
    def settled(self) -> bool:
        return self.status.startswith("scored by")

    def scored_by(self) -> Optional[int]:
        if self.settled:
            try:
                return int(self.status.rsplit(" ", 1)[1])
            except (IndexError, ValueError):
                return None
        return None


@dataclass
class TokenView:
    id: str
    kind: str
    pos: Optional[Pos]
    hp: int
    max_hp: int
    alive: bool
    carrier: Optional[str]
    #: The frame an image or a drone belongs to, when the view says.
    frame: Optional[str] = None
    #: Set only on our own images: the one the frame is actually standing on.
    real: bool = False


class Snapshot:
    """One decision's worth of the world, parsed out of the seat's view dict."""

    def __init__(self, view: Mapping[str, Any], board: Optional[Board] = None) -> None:
        self.raw = view
        self.seat = int(view["seat"])
        self.turn = int(view.get("turn", 1))
        self.phase = str(view.get("phase", ""))
        self.priority = int(view.get("priority", 0))
        self.over = bool(view.get("over"))
        self.pending: Optional[Mapping[str, Any]] = view.get("pending")
        self.log: Sequence[Mapping[str, Any]] = view.get("log") or ()
        self.vp: Mapping[str, int] = view.get("vp") or {}
        #: `readouts.resolving` when the caller supplies it: which frame and
        #: which card the engine is part-way through, and the attack in flight.
        #: Absent from a bare `view_for`, so every reader must tolerate `None`.
        self.resolving: Optional[Mapping[str, Any]] = view.get("resolving")

        board_json = view.get("board") or {}
        self.board = board if board is not None else build_board(board_json)
        self.objectives = [
            ObjectiveView(
                name=str(o.get("name", "")),
                owner=int(o.get("owner", 0)),
                defend=int(o.get("defend", 0)),
                attack=int(o.get("attack", 0)),
                tiles=tuple(Pos(int(t[0]), int(t[1])) for t in (o.get("tiles") or ())),
                status=str(o.get("status", "")),
            )
            for o in (board_json.get("objectives") or ())
        ]
        self.tokens = [
            TokenView(
                id=str(t.get("id")),
                kind=str(t.get("kind", "")),
                pos=(Pos(int(t["pos"]["x"]), int(t["pos"]["y"])) if t.get("pos") else None),
                hp=int(t.get("hp", 0)),
                max_hp=int(t.get("maxHp", 0)),
                alive=bool(t.get("alive", True)),
                carrier=(str(t["carrier"]) if t.get("carrier") else None),
                frame=(str(t["frame"]) if t.get("frame") else None),
                real=bool(t.get("real")),
            )
            for t in (view.get("tokens") or ())
        ]

        self.frames: dict[str, FrameView] = {}
        for f in view.get("frames") or ():
            pos = f.get("pos")
            frame = FrameView(
                id=str(f.get("id")),
                seat=int(f.get("seat", 0)),
                name=str(f.get("name", "")),
                faction=str(f.get("faction", "")),
                pos=(Pos(int(pos["x"]), int(pos["y"])) if pos else None),
                elev=int(f.get("elev", 0) or 0),
                alive=bool(f.get("alive", True)),
                armour={z: int((f.get("armour") or {}).get(z, 0)) for z in ZONES},
                damage={z: int((f.get("damage") or {}).get(z, 0)) for z in ZONES},
                last_hit={z: bool((f.get("lastHit") or {}).get(z)) for z in ZONES},
                movement=int(f.get("movement", 0) or 0),
                shields=int(f.get("shields", 0) or 0),
                statuses=dict(f.get("statuses") or {}),
                committed=_card_refs(f.get("committed") or ()),
                on_field=_card_refs(f.get("onField") or ()),
                aside=[
                    (str(a.get("uid")), str(a.get("key")))
                    for a in (f.get("aside") or ())
                ],
                deck_count=int(f.get("deckCount", 0) or 0),
                discard_count=int(f.get("discardCount", 0) or 0),
                hand=[
                    (str(h.get("uid")), str(h.get("key")))
                    for h in (f.get("hand") or ())
                ],
            )
            frame.cloaked = bool(f.get("cloaked"))
            self.frames[frame.id] = frame
        self._guess_cloaked_positions()

    def _guess_cloaked_positions(self) -> None:
        """Stand a hidden frame in the middle of its own images.

        A frame behind Ephemeral Images arrives with no position at all, which
        would drop it out of every distance calculation the scorer makes -- it
        would stop being a threat and stop being worth approaching. The images
        are what can actually be seen, so their centre is the honest guess, and
        it keeps the rest of the AI working unchanged. Nothing is decided from
        it: the legal target list still only ever offers the images.
        """
        for frame in self.frames.values():
            if frame.pos is not None or not frame.alive or not frame.cloaked:
                continue
            spots = [
                t.pos for t in self.tokens
                if t.alive and t.pos is not None and t.frame == frame.id
            ]
            if not spots:
                continue
            frame.pos = Pos(
                round(sum(p.x for p in spots) / len(spots)),
                round(sum(p.y for p in spots) / len(spots)),
            )

    # -- convenience --------------------------------------------------------

    def frame(self, frame_id: Optional[str]) -> Optional[FrameView]:
        return self.frames.get(str(frame_id)) if frame_id else None

    def mine(self, *, alive_only: bool = True) -> list[FrameView]:
        return [
            f for f in self.frames.values()
            if f.seat == self.seat and (f.alive or not alive_only)
        ]

    def enemies(self, *, alive_only: bool = True) -> list[FrameView]:
        return [
            f for f in self.frames.values()
            if f.seat != self.seat and (f.alive or not alive_only)
        ]

    def occupied(self, exclude: Optional[str] = None) -> frozenset[Pos]:
        out = {
            f.pos for f in self.frames.values()
            if f.alive and f.pos is not None and f.id != exclude
        }
        out |= {
            t.pos for t in self.tokens
            if t.alive and t.pos is not None and t.kind == "barricade"
        }
        return frozenset(p for p in out if p is not None)

    def elevation(self, pos: Optional[Pos]) -> int:
        if pos is None or self.board is None:
            return 0
        try:
            return self.board.tile(pos).elevation
        except IndexError:
            return 0

    def distance(self, a: Optional[Pos], b: Optional[Pos]) -> int:
        if a is None or b is None or self.board is None:
            return 99
        return self.board.distance(a, b)

    def token_at(self, pos: Pos) -> Optional[TokenView]:
        for token in self.tokens:
            if token.alive and token.pos == pos:
                return token
        return None

    def objective_for_token(self, token: TokenView) -> Optional[ObjectiveView]:
        wanted = _TOKEN_OBJECTIVE.get(token.kind)
        if wanted is None:
            return None
        for obj in self.objectives:
            if obj.name == wanted:
                return obj
        return None


#: token kind -> the objective it belongs to (mirrors `objectives.OBJECTIVE_TOKENS`).
_TOKEN_OBJECTIVE: Mapping[str, str] = {
    "reactor": "Power Reactors",
    "shiny": "Shiny Thing",
    "fugitive": "Fugitive",
    "tower": "The Tower",
}


def build_board(board_json: Mapping[str, Any]) -> Optional[Board]:
    """Reconstruct the geometry object from the tiles in the seat's view."""
    width = int(board_json.get("width", 0) or 0)
    height = int(board_json.get("height", 0) or 0)
    raw = board_json.get("tiles") or []
    if width <= 0 or height <= 0 or len(raw) != width * height:
        return None
    grid: list[Optional[Tile]] = [None] * (width * height)
    for cell in raw:
        x, y = int(cell["x"]), int(cell["y"])
        grid[y * width + x] = Tile(
            pos=Pos(x, y),
            elevation=int(cell.get("elev", 0) or 0),
            impassable=bool(cell.get("impassable")),
            obstacle=bool(cell.get("obstacle")),
            objective=bool(cell.get("objective")),
            terrain_card=str(cell.get("card") or ""),
        )
    if any(t is None for t in grid):
        return None
    return Board(width, height, [t for t in grid if t is not None])
