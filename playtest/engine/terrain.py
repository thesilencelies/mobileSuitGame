"""Terrain card parsing (workstream B1).

Reads the repo-root ``Terrain_square.csv`` into :class:`TerrainCard` objects: a
4-row x 3-column grid of :class:`~playtest.engine.types.Tile` plus the objective
metadata (defend/attack points, rules text, token count).

This module imports nothing from the engine except ``types`` -- it is pure data
parsing over geometry so the AI and the tests can use it standalone.

Orientation
-----------
The CSV columns are ``tile_<row>_<col>``. The printed card renders row 0 at the
**bottom**: ``terrain_cards.py`` places cell ``tile_{r}_{c}`` at
``square_center(col=c, row=r)`` and TikZ's y axis grows upwards, so CSV row 3 is
the top of the printed card.

:class:`Pos` puts the origin at the top-left of the board with y growing
*downwards*, so ``TerrainCard.grid`` is stored top-row-first in **board** order:

    grid[i][j]  ==  csv cell tile_{3-i}_{j}          (unrotated)

Seat 0 sits at the bottom of the board (largest y, matching the deployment row
in SPEC.md's client JSON, ``{"x":4,"y":15}``), so seat 0's cards are unrotated
and seat 1's two rows are dealt rotated 180 degrees (rules.tex:290, and the
setup figure at rules.tex:305 where the opponent's rows read upside-down):

    rotated[i][j]  ==  grid[3-i][2-j]  ==  csv cell tile_{i}_{2-j}

Cell codes
----------
Space separated and combinable (rules.tex:255): ``e1``/``e2``/``e3`` elevation,
``im`` impassable, ``obs`` obstacle, ``obj`` objective, ``tkn`` token spawn. An
empty cell is ground (elevation 0). Unknown tokens are ignored, matching
``terrain_cards.py``'s ``STYLE_DICT.get(element, {})`` -- see ``UNKNOWN_CODES``
for the ones that actually occur in the shipped CSV.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterator, Mapping, Optional, Sequence

from .types import Pos, Tile

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

#: A terrain card is always this many tiles (rules.tex:254, Terrain_square.csv).
CARD_ROWS = 4
CARD_COLS = 3

REPO_ROOT = Path(__file__).resolve().parents[2]
TERRAIN_CSV = REPO_ROOT / "Terrain_square.csv"

ELEVATION_CODES: Mapping[str, int] = {"e1": 1, "e2": 2, "e3": 3}
IMPASSABLE_CODE = "im"
OBSTACLE_CODE = "obs"
OBJECTIVE_CODE = "obj"
TOKEN_CODE = "tkn"

#: Rows that are legends rather than playable terrain (SPEC.md: "`BehindText` in
#: the Helpcard row is a legend artifact -- ignore it").
LEGEND_CARDS = frozenset({"Helpcard"})

#: Tokens present in the shipped CSV that are not real tile codes. ``BehindText``
#: is the Helpcard legend; ``N/A`` and the bare ``e`` are data typos. All are
#: ignored (the printed card renders them as plain ground too).
UNKNOWN_CODES = frozenset({"BehindText", "N/A", "e"})


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def parse_cell(code: str, pos: Pos, card_name: str = "") -> Tile:
    """Parse one ``tile_r_c`` cell into a :class:`Tile` at ``pos``."""
    elevation = 0
    impassable = obstacle = objective = token_spawn = False
    for token in (code or "").split():
        if token in ELEVATION_CODES:
            elevation = max(elevation, ELEVATION_CODES[token])
        elif token == IMPASSABLE_CODE:
            impassable = True
        elif token == OBSTACLE_CODE:
            obstacle = True
        elif token == OBJECTIVE_CODE:
            objective = True
        elif token == TOKEN_CODE:
            token_spawn = True
        # anything else is ignored, as the renderer does
    return Tile(
        pos=pos,
        elevation=elevation,
        impassable=impassable,
        obstacle=obstacle,
        objective=objective,
        token_spawn=token_spawn,
        terrain_card=card_name,
    )


@dataclass(frozen=True)
class TerrainCard:
    """One terrain card: a 4x3 grid of tiles plus its objective metadata.

    ``grid`` is indexed ``[row][col]`` in board order -- row 0 is the row that
    ends up at the smallest board y. Each tile's ``pos`` is card-local
    (``Pos(col, row)``); :meth:`playtest.engine.board.Board.from_tile_grids`
    rewrites them to board coordinates during assembly.
    """

    name: str
    grid: tuple[tuple[Tile, ...], ...]
    defend_points: int = 0
    attack_points: int = 0
    token_count: int = 0
    rules_text: str = ""
    image: str = ""
    #: True once :meth:`rotated_180` has been applied (the opponent's rows).
    rotated: bool = False

    # -- construction ------------------------------------------------------

    @classmethod
    def from_csv_row(cls, row: Mapping[str, str]) -> "TerrainCard":
        name = (row.get("Name") or "").strip()
        grid = tuple(
            tuple(
                parse_cell(row.get(f"tile_{CARD_ROWS - 1 - r}_{c}", ""), Pos(c, r), name)
                for c in range(CARD_COLS)
            )
            for r in range(CARD_ROWS)
        )
        return cls(
            name=name,
            grid=grid,
            defend_points=_as_int(row.get("Defend Points")),
            attack_points=_as_int(row.get("Attack Points")),
            token_count=_as_int(row.get("Tokens")),
            rules_text=(row.get("Rules") or "").strip(),
            image=(row.get("CardImg") or "").strip(),
        )

    # -- queries -----------------------------------------------------------

    @property
    def rows(self) -> int:
        return len(self.grid)

    @property
    def cols(self) -> int:
        return len(self.grid[0]) if self.grid else 0

    @property
    def is_objective(self) -> bool:
        """Objectives are the terrain cards that carry points (rules.tex:243)."""
        return bool(self.defend_points or self.attack_points)

    def tile(self, row: int, col: int) -> Tile:
        return self.grid[row][col]

    def tiles(self) -> Iterator[Tile]:
        for line in self.grid:
            yield from line

    def cells_where(self, attr: str) -> tuple[Pos, ...]:
        return tuple(t.pos for t in self.tiles() if getattr(t, attr))

    @property
    def objective_cells(self) -> tuple[Pos, ...]:
        """Card-local positions of the ``obj`` cells."""
        return self.cells_where("objective")

    @property
    def token_cells(self) -> tuple[Pos, ...]:
        """Card-local positions of the ``tkn`` cells."""
        return self.cells_where("token_spawn")

    # -- transformation ----------------------------------------------------

    def rotated_180(self) -> "TerrainCard":
        """This card turned through 180 degrees, for the opponent's two rows."""
        rows, cols = self.rows, self.cols
        grid = tuple(
            tuple(
                replace(self.grid[rows - 1 - r][cols - 1 - c], pos=Pos(c, r))
                for c in range(cols)
            )
            for r in range(rows)
        )
        return replace(self, grid=grid, rotated=not self.rotated)


def _as_int(value: Optional[str]) -> int:
    try:
        return int((value or "0").strip() or 0)
    except ValueError:
        return 0


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def load_terrain_cards(
    path: Path | str = TERRAIN_CSV,
    *,
    include_legends: bool = False,
) -> dict[str, TerrainCard]:
    """Load ``Terrain_square.csv`` into ``{name: TerrainCard}``.

    Rows with ``PrintID`` of ``0`` and the legend rows (:data:`LEGEND_CARDS`)
    are skipped unless ``include_legends`` is set.
    """
    cards: dict[str, TerrainCard] = {}
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            name = (row.get("Name") or "").strip()
            if not name:
                continue
            if not include_legends and name in LEGEND_CARDS:
                continue
            if (row.get("PrintID") or "1").strip() == "0":
                continue
            cards[name] = TerrainCard.from_csv_row(row)
    return cards


def objective_cards(cards: Mapping[str, TerrainCard]) -> dict[str, TerrainCard]:
    """The subset of ``cards`` that are objectives (SPEC.md lists 8)."""
    return {n: c for n, c in cards.items() if c.is_objective}


def load_terrain_deck(
    names: Sequence[str],
    cards: Optional[Mapping[str, TerrainCard]] = None,
) -> list[TerrainCard]:
    """Resolve a list of bare card names into cards, preserving order.

    Duplicate names are allowed (``deck_terrain_assault.csv`` lists ``T Tower``
    twice) and yield independent copies.
    """
    catalogue = load_terrain_cards() if cards is None else cards
    missing = [n for n in names if n not in catalogue]
    if missing:
        raise KeyError(f"unknown terrain card(s): {sorted(set(missing))}")
    return [catalogue[n] for n in names]
