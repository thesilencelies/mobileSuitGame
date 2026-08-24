"""Board geometry: adjacency, range, movement and line of sight (workstream B1).

Implements :class:`~playtest.engine.types.BoardProtocol`. Everything here is a
pure query over tiles -- the board never mutates and knows nothing about cards,
turns or frames. Frame positions arrive as ``occupied`` sets so the same board
answers any number of hypothetical positions, which is what lets the AI search
moves cheaply.

Imports nothing from the engine except ``types``. Assembly takes plain
``Tile`` grids (see :meth:`Board.from_tile_grids`) rather than terrain cards, so
this module stays independent of ``terrain.py``; ``setup.py`` glues the two.
"""

from __future__ import annotations

import heapq
from dataclasses import replace
from typing import Iterable, Iterator, Mapping, Optional, Sequence

from .types import Pos, Tile

#: A card's tiles, indexed ``[row][col]``.
TileGrid = Sequence[Sequence[Tile]]

# --------------------------------------------------------------------------
# Line-of-sight tuning
# --------------------------------------------------------------------------

#: Sample points per axis per tile when testing the permissive LoS rule. Each
#: tile contributes ``n * n`` candidate endpoints, so ``n**4`` candidate lines
#: are available (short-circuited on the first clear one). ``1`` degenerates to
#: the naive centre-to-centre test.
LOS_SAMPLES_PER_AXIS = 5

#: Sample points sit this far inside the tile edge, so every candidate endpoint
#: is strictly interior and a line never starts or ends exactly on a grid line.
LOS_EDGE_INSET = 0.02

_EPS = 1e-9


class Board:
    """An assembled battlefield.

    ``width``/``height`` are in tiles. Tiles are stored row-major with the
    origin at the top left, y growing downwards (see ``types.Pos``).
    """

    __slots__ = ("width", "height", "_tiles")

    def __init__(self, width: int, height: int, tiles: Sequence[Tile]) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("board must have positive extent")
        if len(tiles) != width * height:
            raise ValueError(
                f"expected {width * height} tiles for a {width}x{height} board, "
                f"got {len(tiles)}"
            )
        self.width = width
        self.height = height
        self._tiles: tuple[Tile, ...] = tuple(tiles)

    # -- construction ------------------------------------------------------

    @classmethod
    def from_tile_grids(cls, card_rows: Sequence[Sequence[TileGrid]]) -> "Board":
        """Assemble a board from a rectangular layout of card tile grids.

        ``card_rows[i][j]`` is the ``[row][col]`` tile grid of the card in card
        row ``i``, card column ``j``, already rotated if it belongs to the
        opponent. Every grid must have the same shape. Each tile's ``pos`` is
        rewritten to its board coordinate.
        """
        if not card_rows or not card_rows[0]:
            raise ValueError("layout must contain at least one card")
        card_h = len(card_rows[0][0])
        card_w = len(card_rows[0][0][0])
        cols = len(card_rows[0])
        for i, row in enumerate(card_rows):
            if len(row) != cols:
                raise ValueError("layout rows must all be the same length")
            for j, grid in enumerate(row):
                if len(grid) != card_h or any(len(line) != card_w for line in grid):
                    raise ValueError(f"card at ({i},{j}) has a mismatched grid size")

        width = cols * card_w
        height = len(card_rows) * card_h
        tiles: list[Optional[Tile]] = [None] * (width * height)
        for i, row in enumerate(card_rows):
            for j, grid in enumerate(row):
                for r in range(card_h):
                    for c in range(card_w):
                        x, y = j * card_w + c, i * card_h + r
                        tiles[y * width + x] = replace(grid[r][c], pos=Pos(x, y))
        return cls(width, height, [t for t in tiles if t is not None])

    # -- basic queries -----------------------------------------------------

    def tile(self, pos: Pos) -> Tile:
        if not self.in_bounds(pos):
            raise IndexError(f"{pos} is off the {self.width}x{self.height} board")
        return self._tiles[pos.y * self.width + pos.x]

    def in_bounds(self, pos: Pos) -> bool:
        return 0 <= pos.x < self.width and 0 <= pos.y < self.height

    def tiles(self) -> Iterator[Tile]:
        return iter(self._tiles)

    def positions(self) -> Iterator[Pos]:
        for y in range(self.height):
            for x in range(self.width):
                yield Pos(x, y)

    def neighbours(self, pos: Pos) -> tuple[Pos, ...]:
        """The up-to-8 adjacent tiles, diagonals included (rules.tex:285)."""
        out = []
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                q = Pos(pos.x + dx, pos.y + dy)
                if self.in_bounds(q):
                    out.append(q)
        return tuple(out)

    def distance(self, a: Pos, b: Pos) -> int:
        """Chebyshev distance -- range is reckoned as a square (rules.tex:286)."""
        return max(abs(a.x - b.x), abs(a.y - b.y))

    def is_adjacent(self, a: Pos, b: Pos) -> bool:
        return a != b and self.distance(a, b) == 1

    def in_range(self, a: Pos, b: Pos, rng: int) -> bool:
        return self.distance(a, b) <= rng

    # -- movement ----------------------------------------------------------

    def step_cost(self, frm: Pos, to: Pos, *, flying: bool = False) -> Optional[int]:
        """Cost of one 8-way step, or ``None`` if the step is illegal.

        Movement is counted in steps (rules.tex:288). A step costs 1, plus 1 per
        elevation *level climbed* (rules.tex:404): entering an elevation-2 tile
        from the ground costs 3. Descending any number of levels is free, so a
        step down costs 1 however far it drops.

        "You cannot stop halfway up" is read as: the climb is paid on entry and
        is all-or-nothing -- a frame with 2 movement left cannot spend it part-
        way into a 3-cost climb and end there. Because every tile has a single
        definite elevation, there is no in-between tile to stand on, so this is
        a clarification rather than an extra constraint: Dijkstra over these
        edge weights with a hard budget already refuses partial climbs.

        Obstacles and impassable terrain cannot be moved through
        (rules.tex:405). ``flying`` frames "do not spend movement to cross
        obstacles or move between floors" (rules.tex:968), so they pay a flat 1
        per step and may cross -- and stop on -- obstacle tiles. Impassable
        terrain still stops everyone.
        """
        dest = self.tile(to)
        if dest.impassable:
            return None
        if dest.obstacle and not flying:
            return None
        if flying:
            return 1
        climb = dest.elevation - self.tile(frm).elevation
        return 1 + climb if climb > 0 else 1

    def _dijkstra(
        self,
        start: Pos,
        budget: int,
        occupied: frozenset[Pos],
        flying: bool,
        goal: Optional[Pos] = None,
    ) -> tuple[dict[Pos, int], dict[Pos, Pos]]:
        blocked = frozenset(occupied) - {start}
        dist: dict[Pos, int] = {start: 0}
        prev: dict[Pos, Pos] = {}
        if budget < 0:
            return dist, prev
        heap: list[tuple[int, Pos]] = [(0, start)]
        while heap:
            cost, pos = heapq.heappop(heap)
            if cost > dist.get(pos, cost + 1):
                continue
            if goal is not None and pos == goal:
                break
            for nxt in self.neighbours(pos):
                if nxt in blocked:
                    continue
                step = self.step_cost(pos, nxt, flying=flying)
                if step is None:
                    continue
                new = cost + step
                if new <= budget and new < dist.get(nxt, new + 1):
                    dist[nxt] = new
                    prev[nxt] = pos
                    heapq.heappush(heap, (new, nxt))
        return dist, prev

    def reachable(
        self,
        start: Pos,
        budget: int,
        *,
        occupied: frozenset[Pos] = frozenset(),
        flying: bool = False,
    ) -> Mapping[Pos, int]:
        """Tiles reachable within ``budget`` steps, mapped to their cost.

        Includes ``start`` at cost 0 (a frame may always stay put). ``start`` is
        removed from ``occupied`` so callers can pass every frame position.
        Frames block movement, so occupied tiles can be neither entered nor
        crossed.
        """
        dist, _ = self._dijkstra(start, budget, occupied, flying)
        return dist

    def path(
        self,
        start: Pos,
        goal: Pos,
        budget: int,
        *,
        occupied: frozenset[Pos] = frozenset(),
        flying: bool = False,
    ) -> Optional[Sequence[Pos]]:
        """A cheapest legal path from ``start`` to ``goal`` inclusive, or None."""
        if start == goal:
            return (start,)
        dist, prev = self._dijkstra(start, budget, occupied, flying, goal=goal)
        if goal not in dist:
            return None
        out = [goal]
        while out[-1] != start:
            out.append(prev[out[-1]])
        out.reverse()
        return tuple(out)

    def move_cost(
        self,
        start: Pos,
        goal: Pos,
        budget: int,
        *,
        occupied: frozenset[Pos] = frozenset(),
        flying: bool = False,
    ) -> Optional[int]:
        """Cheapest cost of reaching ``goal``, or ``None`` if out of reach."""
        return self.reachable(start, budget, occupied=occupied, flying=flying).get(goal)

    # -- line of sight -----------------------------------------------------

    def effective_elevation(self, pos: Pos, occupied: Iterable[Pos] = ()) -> int:
        """Tile elevation, +1 if a frame stands there (rules.tex:434).

        Used for *intervening* tiles only. The attacker's and target's own
        reference heights are their bare tile elevations -- the worked figures
        require it: in rules.tex:441 an elevation-1 tile blocks an attacker
        standing on the ground, which it would not if the attacker counted as
        elevation 1 itself.
        """
        return self.tile(pos).elevation + (1 if pos in occupied else 0)

    def line_of_sight_blockers(
        self,
        attacker: Pos,
        target: Pos,
        *,
        occupied: frozenset[Pos] = frozenset(),
        flying_attacker: bool = False,
        flying_target: bool = False,
    ) -> frozenset[Pos]:
        """Tiles that would obstruct a line from ``attacker`` to ``target``.

        The four obstruction kinds (rules.tex:426):

        * impassable terrain -- anywhere on the line;
        * terrain higher than the *attacker* -- anywhere on the line;
        * obstacles *adjacent to the target*;
        * terrain higher than the *target*, *adjacent to the target*.

        A tile holding a frame counts one elevation higher. Obstacles do not
        block LoS to or from a ``Flying`` frame (rules.tex:968).

        Only the bounding box of the two tiles is considered: a straight segment
        between two points of that box never leaves it, so nothing outside can
        ever be crossed. The attacker's and target's own tiles never obstruct.
        """
        a_elev = self.tile(attacker).elevation
        t_elev = self.tile(target).elevation
        ignore_obstacles = flying_attacker or flying_target
        out: set[Pos] = set()
        for y in range(min(attacker.y, target.y), max(attacker.y, target.y) + 1):
            for x in range(min(attacker.x, target.x), max(attacker.x, target.x) + 1):
                pos = Pos(x, y)
                if pos == attacker or pos == target:
                    continue
                tile = self.tile(pos)
                elev = tile.elevation + (1 if pos in occupied else 0)
                adjacent_to_target = self.is_adjacent(pos, target)
                if tile.impassable:
                    out.add(pos)
                elif elev > a_elev:
                    out.add(pos)
                elif adjacent_to_target and tile.obstacle and not ignore_obstacles:
                    out.add(pos)
                elif adjacent_to_target and elev > t_elev:
                    out.add(pos)
        return frozenset(out)

    def has_line_of_sight(
        self,
        attacker: Pos,
        target: Pos,
        *,
        occupied: frozenset[Pos] = frozenset(),
        flying_attacker: bool = False,
        flying_target: bool = False,
        samples: Optional[int] = None,
    ) -> bool:
        """Permissive LoS: clear if *any* line between the tiles is unobstructed.

        The rule is "draw a line starting from anywhere on the attacking frame
        to anywhere on the target" (rules.tex:424), so the whole tile counts as
        the frame's extent, not just its centre.

        **Approximation.** Exact tile-to-tile visibility is a 4-parameter
        problem; instead each tile is sampled on an ``n x n`` grid of interior
        points (``n`` = :data:`LOS_SAMPLES_PER_AXIS`, inset from the edges by
        :data:`LOS_EDGE_INSET` so no sample lies on a grid line), and every
        attacker-sample to target-sample segment is tested. A segment is
        obstructed when it crosses the *interior* of an obstructing tile;
        grazing an edge or clipping a corner is not a crossing, which is what
        makes "shoot along the row" resolve the way the first worked figure
        does. Sampling is checked centre-first then corners-first and stops at
        the first clear line.

        The approximation is one-sided: every line it accepts is a genuinely
        unobstructed line, so it never invents line of sight. It can only miss
        one -- a gap so narrow that no sampled pair threads it. At n=5 the
        sampled endpoints include the tile corners, which is what any
        single-tile-wide gap needs.

        **Erratum.** The second worked figure (rules.tex:459) disagrees with
        this, and the author has ruled that the rule is right and the figure is
        wrong. Its right-hand shot is drawn through an obstacle sitting beside
        the target and called blocked, but a line from the far corner of the
        attacker's tile reaches the target's side edge without ever entering
        that obstacle, so that target is in fact **visible**. The obstruction
        model here is line-based for all four obstruction kinds, and the other
        five verdicts across the three figures come out right.
        """
        if not (self.in_bounds(attacker) and self.in_bounds(target)):
            return False
        if attacker == target:
            return True

        blockers = self.line_of_sight_blockers(
            attacker,
            target,
            occupied=occupied,
            flying_attacker=flying_attacker,
            flying_target=flying_target,
        )
        if not blockers:
            return True

        cells = tuple((float(p.x), float(p.y)) for p in blockers)
        n = LOS_SAMPLES_PER_AXIS if samples is None else samples
        for ax, ay in _sample_points(attacker, n):
            for bx, by in _sample_points(target, n):
                if not any(
                    _segment_crosses_cell(ax, ay, bx, by, cx, cy) for cx, cy in cells
                ):
                    return True
        return False


# --------------------------------------------------------------------------
# Geometry helpers
# --------------------------------------------------------------------------


def _axis_fractions(n: int) -> tuple[float, ...]:
    """``n`` sample offsets across a unit tile, strictly inside it."""
    if n <= 1:
        return (0.5,)
    lo, hi = LOS_EDGE_INSET, 1.0 - LOS_EDGE_INSET
    return tuple(lo + (hi - lo) * i / (n - 1) for i in range(n))


def _sample_points(pos: Pos, n: int) -> tuple[tuple[float, float], ...]:
    """Sample points inside ``pos``, centre first then the four corners.

    Ordering matters only for speed: the common cases (a clear centre line, or a
    line that has to hug a corner) are tried before the rest.
    """
    fr = _axis_fractions(n)
    pts = [(pos.x + fx, pos.y + fy) for fy in fr for fx in fr]
    head = [(pos.x + 0.5, pos.y + 0.5)]
    if n > 1:
        head += [
            (pos.x + fx, pos.y + fy) for fy in (fr[0], fr[-1]) for fx in (fr[0], fr[-1])
        ]
    seen: set[tuple[float, float]] = set()
    out = []
    for p in head + pts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return tuple(out)


def _segment_crosses_cell(
    x0: float, y0: float, x1: float, y1: float, cx: float, cy: float
) -> bool:
    """Does the segment cross the *interior* of the unit cell at ``(cx, cy)``?

    Liang-Barsky clipping against the open box ``(cx, cx+1) x (cy, cy+1)``. A
    segment that runs exactly along an edge, or that touches only a corner,
    yields a zero-length overlap and is not a crossing.
    """
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in (
        (-dx, x0 - cx),
        (dx, cx + 1.0 - x0),
        (-dy, y0 - cy),
        (dy, cy + 1.0 - y0),
    ):
        if p == 0.0:
            if q <= 0.0:
                return False
        else:
            r = q / p
            if p < 0.0:
                if r > t1:
                    return False
                if r > t0:
                    t0 = r
            else:
                if r < t0:
                    return False
                if r < t1:
                    t1 = r
    return (t1 - t0) > _EPS
