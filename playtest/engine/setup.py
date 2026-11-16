"""Battlefield setup: dealing terrain, placing objectives, deployment (B1).

Implements rules.tex:424 "Setup":

    Each player builds a grid of 2 rows of (number of frames + 1) from their
    terrain deck. First, each player places objectives in each of their rows --
    one for every 4 frames a side plus one (nframes // 4 + 1). The remaining slots
    in the grid are then filled by dealing out the rest of the terrain cards.
    [...] Each player takes it in turns to put one of their frames on nearest edge
    of their terrain cards.

For 3v3 that is 4 cards wide by 4 cards tall, and since a terrain card is 3x4
tiles the board is **12 x 16 tiles**.

Each player brings two decks (rules.tex:339): ``decks/deck_terrain_<name>.csv``
and ``decks/deck_objective_<name>.csv``. Terrain decks should have 4 more terrain
cards than used, and objective decks 2 more objectives than used (for 3v3, 10
terrain and 4 objectives). If a deck is smaller than required for larger battles,
the dealer loops it.

Seat 0 owns the bottom half (largest y) and its cards are unrotated; seat 1 owns
the top half and its two rows are dealt rotated 180 degrees, which is what makes
the opponent's terrain read upside-down in the setup figure at rules.tex:460.

Imports only ``types``, ``terrain`` and ``board`` -- no game state, no cards.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence

from .board import Board
from .terrain import REPO_ROOT, TerrainCard, load_terrain_cards, load_terrain_deck
from .types import Pos, Team

DECKS_DIR = REPO_ROOT / "decks"

#: Card rows each player lays out (rules.tex:424).
ROWS_PER_SEAT = 2

#: Objectives per row for 3v3 (legacy constant, rules.tex:426).
OBJECTIVES_PER_SEAT = ROWS_PER_SEAT

#: Standard deck sizes each player brings for 3v3 (rules.tex:339).
#: 4 more terrain cards than used (6 + 4 = 10) and 2 more objectives than
#: used (2 + 2 = 4).
TERRAIN_DECK_SIZE = 10
OBJECTIVE_DECK_SIZE = 4


def cards_per_row(frames_per_side: int) -> int:
    """Cards in one row: number of frames + 1 (rules.tex:424)."""
    return frames_per_side + 1


def objectives_per_row(frames_per_side: int) -> int:
    """Objectives in one row: one for every 4 frames a side plus one (rules.tex:426)."""
    return (frames_per_side // 4) + 1


def objectives_per_seat(frames_per_side: int) -> int:
    """Total objectives placed for one seat across all its rows."""
    return ROWS_PER_SEAT * objectives_per_row(frames_per_side)


def seat_card_rows(seat: Team, *, seats: int = 2) -> tuple[int, ...]:
    """The board card-row indices owned by ``seat``, top to bottom.

    Seat 0 takes the bottom block so its deployment row is the bottom edge of
    the board, matching SPEC.md's client JSON (a seat-0 frame at ``y = 15``).
    """
    _check_seats(seats)
    block = (seats - 1 - seat) if seat < seats else 0
    base = block * ROWS_PER_SEAT
    return tuple(range(base, base + ROWS_PER_SEAT))


def is_rotated(seat: Team) -> bool:
    """Every seat but seat 0 lays its terrain facing itself, i.e. rotated."""
    return seat != 0


def _check_seats(seats: int) -> None:
    if seats != 2:
        raise NotImplementedError("battlefield assembly currently assumes 2 seats")


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Placement:
    """One terrain card in its slot on the assembled board."""

    card: TerrainCard
    seat: Team
    card_row: int
    card_col: int

    @property
    def rotated(self) -> bool:
        return self.card.rotated

    def origin(self) -> Pos:
        """Board position of this card's top-left tile."""
        return Pos(self.card_col * self.card.cols, self.card_row * self.card.rows)

    def board_pos(self, local: Pos) -> Pos:
        o = self.origin()
        return Pos(o.x + local.x, o.y + local.y)


@dataclass(frozen=True)
class ObjectiveInfo:
    """An objective in play, resolved to board coordinates.

    ``owner`` is the defender -- the player who brought the card (rules.tex:244).
    ``defend_points`` is their score, ``attack_points`` the other side's.
    """

    name: str
    owner: Team
    defend_points: int
    attack_points: int
    token_count: int
    rules_text: str
    #: Board positions of the card's ``obj`` cells (the objective itself).
    tiles: tuple[Pos, ...]
    #: Board positions of the ``tkn`` cells (where tokens start).
    token_tiles: tuple[Pos, ...]
    #: Every tile of the objective card.
    card_tiles: tuple[Pos, ...]


@dataclass(frozen=True)
class Battlefield:
    """Everything setup produces, ready for the state layer to build on."""

    board: Board
    placements: tuple[Placement, ...]
    objectives: tuple[ObjectiveInfo, ...]
    deployment: Mapping[Team, tuple[Pos, ...]]


# --------------------------------------------------------------------------
# Deck handling
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SeatDecks:
    """What one player brings: a terrain deck and a separate objective deck.

    "Each player must bring a terrain deck of 10 cards, and an objective deck of
    5 cards. Objectives (terrain cards that indicate objective scores) can only
    be in the objectives deck" (rules.tex:253). The two deck files are the
    source of truth for which card is which; :func:`validate_deck_pair` checks
    them against the points columns.

    Both decks are deliberately larger than one battle draws: a 3v3 places 2
    objectives out of 5 and fills 8 slots from the 10-card terrain deck, so the
    fill is a shuffled draw, not "use all of them".
    """

    terrain: tuple[TerrainCard, ...]
    objectives: tuple[TerrainCard, ...]

    @classmethod
    def from_mixed(cls, deck: Sequence[TerrainCard]) -> "SeatDecks":
        """Split one combined list by the points columns (legacy/ad-hoc decks)."""
        objectives, plain = split_objectives(deck)
        return cls(terrain=tuple(plain), objectives=tuple(objectives))

    @classmethod
    def coerce(cls, value: "SeatDecks | Sequence[TerrainCard]") -> "SeatDecks":
        return value if isinstance(value, cls) else cls.from_mixed(list(value))


def split_objectives(
    deck: Sequence[TerrainCard],
) -> tuple[list[TerrainCard], list[TerrainCard]]:
    """Split a mixed list into ``(objectives, plain terrain)`` by its points.

    No longer the primary path -- the ``decks/deck_terrain_*`` /
    ``decks/deck_objective_*`` file split is -- but it still backs
    :func:`validate_deck_pair` and ad-hoc decks built in code.
    """
    objectives = [c for c in deck if c.is_objective]
    plain = [c for c in deck if not c.is_objective]
    return objectives, plain


def validate_deck_pair(
    decks: SeatDecks,
    *,
    strict_size: bool = False,
    frames_per_side: int = 3,
) -> list[str]:
    """Problems with a deck pair, empty if it is legal (rules.tex:339).

    Always checks that no scoring card sits in the terrain deck and that every
    objective-deck card scores. ``strict_size`` additionally demands the
    rulebook's deck sizes for the given number of frames: 4 more terrain cards
    than used and 2 more objectives than used.
    """
    problems = []
    for card in decks.terrain:
        if card.is_objective:
            problems.append(f"{card.name} scores points but is in the terrain deck")
    for card in decks.objectives:
        if not card.is_objective:
            problems.append(f"{card.name} scores nothing but is in the objective deck")
    if strict_size:
        expected_obj = objectives_per_seat(frames_per_side) + 2
        expected_terrain = (
            ROWS_PER_SEAT
            * (cards_per_row(frames_per_side) - objectives_per_row(frames_per_side))
        ) + 4
        if len(decks.terrain) != expected_terrain:
            problems.append(
                f"terrain deck has {len(decks.terrain)} cards, expected {expected_terrain}"
            )
        if len(decks.objectives) < expected_obj:
            problems.append(
                f"objective deck has {len(decks.objectives)} cards, "
                f"expected at least {expected_obj}"
            )
    return problems


def _loop_pool(
    cards: Sequence[TerrainCard], needed: int, rng: random.Random
) -> list[TerrainCard]:
    """Draw `needed` cards from `cards`, looping and reshuffling if needed."""
    if not cards:
        raise ValueError("cannot deal from an empty deck")
    pool = list(cards)
    rng.shuffle(pool)
    result: list[TerrainCard] = []
    while len(result) < needed:
        result.extend(pool)
        pool = list(cards)
        rng.shuffle(pool)
    return result[:needed]


def _deck_path(name: str, kind: str) -> Path:
    """Resolve a deck name or path to a file under ``decks/``."""
    candidates = [
        Path(name),
        DECKS_DIR / name,
        DECKS_DIR / f"{name}.csv",
        DECKS_DIR / f"deck_{kind}_{name}.csv",
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError(f"no {kind} deck matching {name!r}")


def terrain_deck_path(name: str) -> Path:
    return _deck_path(name, "terrain")


def objective_deck_path(name: str) -> Path:
    return _deck_path(name, "objective")


def _read_deck(path: Path, cards: Optional[Mapping[str, TerrainCard]]) -> list[TerrainCard]:
    with open(path, encoding="utf-8") as handle:
        names = [line.strip() for line in handle if line.strip()]
    return load_terrain_deck(names, cards if cards is not None else load_terrain_cards())


def load_terrain_deck_file(
    name: str, cards: Optional[Mapping[str, TerrainCard]] = None
) -> list[TerrainCard]:
    """Load one ``decks/deck_terrain_*.csv`` (one bare card name per line)."""
    return _read_deck(terrain_deck_path(name), cards)


def load_objective_deck_file(
    name: str, cards: Optional[Mapping[str, TerrainCard]] = None
) -> list[TerrainCard]:
    """Load one ``decks/deck_objective_*.csv`` (one bare card name per line)."""
    return _read_deck(objective_deck_path(name), cards)


def load_deck_pair(
    name: str,
    cards: Optional[Mapping[str, TerrainCard]] = None,
    *,
    objectives: Optional[str] = None,
) -> SeatDecks:
    """Load the ``deck_terrain_<name>`` / ``deck_objective_<name>`` pair.

    ``objectives`` names a different objective deck if a player mixes archetypes.
    """
    if cards is None:
        cards = load_terrain_cards()
    return SeatDecks(
        terrain=tuple(load_terrain_deck_file(name, cards)),
        objectives=tuple(load_objective_deck_file(objectives or name, cards)),
    )


def available_deck_pairs() -> tuple[str, ...]:
    """Archetype names that have both a terrain and an objective deck file."""
    terrain = {p.stem[len("deck_terrain_"):] for p in DECKS_DIR.glob("deck_terrain_*.csv")}
    objective = {p.stem[len("deck_objective_"):] for p in DECKS_DIR.glob("deck_objective_*.csv")}
    return tuple(sorted(terrain & objective))


# --------------------------------------------------------------------------
# Dealing
# --------------------------------------------------------------------------


def deal_battlefield(
    decks: Mapping[Team, "SeatDecks | Sequence[TerrainCard]"],
    *,
    rng: Optional[random.Random] = None,
    frames_per_side: int = 3,
    objective_slots: Optional[Mapping[Team, Sequence[int]]] = None,
) -> Battlefield:
    """Deal both halves of the battlefield and assemble the board.

    ``decks[seat]`` is that seat's :class:`SeatDecks`. Each player takes one
    objective per row from their shuffled objective deck and fills the rest of
    their grid from their shuffled terrain deck -- both are drawn from, not
    exhausted (rules.tex:339). A bare list is accepted and split by its points
    columns, for decks assembled in code.

    ``objective_slots[seat]`` gives the card column chosen for the objective in
    each of that seat's rows, in board row order -- the "chooses where to place
    it within that row" decision. Omitted seats get a random slot from ``rng``.

    Deterministic given ``rng``: it is the only source of randomness.
    """
    rng = rng or random.Random()
    seats = tuple(sorted(decks))
    _check_seats(len(seats))
    width = cards_per_row(frames_per_side)

    layout: dict[tuple[int, int], tuple[Team, TerrainCard]] = {}
    objective_cards: dict[tuple[int, int], tuple[Team, TerrainCard]] = {}

    for seat in seats:
        seat_decks = SeatDecks.coerce(decks[seat])
        problems = validate_deck_pair(seat_decks)
        if problems:
            raise ValueError(f"seat {seat}: {'; '.join(problems)}")
        if not seat_decks.objectives:
            raise ValueError(f"seat {seat} has no objective cards")
        if not seat_decks.terrain:
            raise ValueError(f"seat {seat} has no terrain cards")

        rows = seat_card_rows(seat, seats=len(seats))
        obj_count = objectives_per_row(frames_per_side)
        obj_needed = len(rows) * obj_count
        plain_needed = len(rows) * width - obj_needed

        seat_objectives = _loop_pool(seat_decks.objectives, obj_needed, rng)
        seat_plain = _loop_pool(seat_decks.terrain, plain_needed, rng)

        chosen_slots = objective_slots.get(seat) if objective_slots else None

        for row_idx, row in enumerate(rows):
            row_specified: list[int] = []
            if chosen_slots is not None and row_idx < len(chosen_slots):
                item = chosen_slots[row_idx]
                if isinstance(item, (list, tuple, set)):
                    row_specified = [int(c) for c in item]
                else:
                    row_specified = [int(item)]

            for col in row_specified:
                if not 0 <= col < width:
                    raise ValueError(f"objective slot {col} outside a row of {width}")
            if len(set(row_specified)) != len(row_specified):
                raise ValueError(f"duplicate objective slots in row: {row_specified}")

            if len(row_specified) > obj_count:
                row_specified = row_specified[:obj_count]
            needed_cols = obj_count - len(row_specified)
            if needed_cols > 0:
                available_cols = [c for c in range(width) if c not in row_specified]
                row_specified.extend(rng.sample(available_cols, needed_cols))

            for col in row_specified:
                card = seat_objectives.pop(0)
                layout[(row, col)] = (seat, card)
                objective_cards[(row, col)] = (seat, card)

        for row in rows:
            for col in range(width):
                if (row, col) not in layout:
                    layout[(row, col)] = (seat, seat_plain.pop(0))

    rows_total = len(seats) * ROWS_PER_SEAT
    placements: list[Placement] = []
    grid_rows: list[list[Sequence[Sequence[object]]]] = []
    for row in range(rows_total):
        grid_row = []
        for col in range(width):
            seat, card = layout[(row, col)]
            if is_rotated(seat) != card.rotated:
                card = card.rotated_180()
            placements.append(Placement(card=card, seat=seat, card_row=row, card_col=col))
            grid_row.append(card.grid)
        grid_rows.append(grid_row)

    board = Board.from_tile_grids(grid_rows)  # type: ignore[arg-type]

    objectives_out = tuple(
        _objective_info(p)
        for p in placements
        if (p.card_row, p.card_col) in objective_cards
    )
    deployment = {seat: deployment_tiles(board, seat, seats=len(seats)) for seat in seats}
    return Battlefield(
        board=board,
        placements=tuple(placements),
        objectives=objectives_out,
        deployment=deployment,
    )


def _objective_info(placement: Placement) -> ObjectiveInfo:
    card = placement.card
    return ObjectiveInfo(
        name=card.name,
        owner=placement.seat,
        defend_points=card.defend_points,
        attack_points=card.attack_points,
        token_count=card.token_count,
        rules_text=card.rules_text,
        tiles=tuple(placement.board_pos(p) for p in card.objective_cells),
        token_tiles=tuple(placement.board_pos(p) for p in card.token_cells),
        card_tiles=tuple(placement.board_pos(t.pos) for t in card.tiles()),
    )


# --------------------------------------------------------------------------
# Deployment
# --------------------------------------------------------------------------


def deployment_row(board: Board, seat: Team, *, seats: int = 2) -> int:
    """The board y of a seat's own outermost tile row (its "nearest edge")."""
    _check_seats(seats)
    return board.height - 1 if seat == 0 else 0


def deployment_tiles(board: Board, seat: Team, *, seats: int = 2) -> tuple[Pos, ...]:
    """Legal deployment tiles for ``seat``: its outermost tile row.

    Impassable and obstacle tiles are excluded -- a frame cannot move through
    them (rules.tex:451), so it cannot start on one either.
    """
    y = deployment_row(board, seat, seats=seats)
    return tuple(
        Pos(x, y)
        for x in range(board.width)
        if not (board.tile(Pos(x, y)).impassable or board.tile(Pos(x, y)).obstacle)
    )


def deployment_order(frames_per_side: int, *, first_seat: Team = 0, seats: int = 2) -> tuple[Team, ...]:
    """Seats in deployment order -- alternating, one frame at a time.

    "Each player takes it in turns to put one of their frames on nearest edge
    [...] After all frames are deployed this way the player who deployed first
    receives the priority marker" (rules.tex:347).
    """
    _check_seats(seats)
    order = [(first_seat + i) % seats for i in range(seats)]
    return tuple(order[i % seats] for i in range(frames_per_side * seats))
