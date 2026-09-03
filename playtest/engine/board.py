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

#: Sample points per axis across the *attacker's* tile. The line's far end is
#: always the target's centre point (rules.tex:474), so a query tests ``n * n``
#: candidate lines, short-circuited on the first clear one. ``1`` degenerates to
#: the naive centre-to-centre test.
LOS_SAMPLES_PER_AXIS = 5

#: Source samples sit this far inside the tile edge, so every candidate line
#: starts strictly inside the attacker's tile rather than on its boundary.
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
        """The up-to-8 adjacent tiles, diagonals included (rules.tex:278)."""
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
        """Chebyshev distance -- range is reckoned as a square (rules.tex:279)."""
        return max(abs(a.x - b.x), abs(a.y - b.y))

    def is_adjacent(self, a: Pos, b: Pos) -> bool:
        return a != b and self.distance(a, b) == 1

    def in_range(self, a: Pos, b: Pos, rng: int) -> bool:
        return self.distance(a, b) <= rng

    # -- movement ----------------------------------------------------------

    def step_cost(
        self, frm: Pos, to: Pos, *, flying: bool = False, climb_free: bool = False
    ) -> Optional[int]:
        """Cost of one 8-way step, or ``None`` if the step is illegal.

        Movement is counted in steps (rules.tex:280). A step costs 1, plus 1 per
        elevation *level climbed* (rules.tex:450): entering an elevation-2 tile
        from the ground costs 3. Descending any number of levels is free, so a
        step down costs 1 however far it drops.

        "You cannot stop halfway up" is read as: the climb is paid on entry and
        is all-or-nothing -- a frame with 2 movement left cannot spend it part-
        way into a 3-cost climb and end there. Because every tile has a single
        definite elevation, there is no in-between tile to stand on, so this is
        a clarification rather than an extra constraint: Dijkstra over these
        edge weights with a hard budget already refuses partial climbs.

        Obstacles and impassable terrain cannot be moved through
        (rules.tex:451). ``flying`` frames "do not spend movement to cross
        obstacles or move between floors" (rules.tex:967), so they pay a flat 1
        per step and may cross -- and stop on -- obstacle tiles. Impassable
        terrain still stops everyone.

        ``climb_free`` is the *other half* of flying on its own: the climb is
        free but obstacles still block, which is what "ignores elevation
        penalties" (`Booster_Jump`) buys. A frame that is both keeps flying's
        wider licence.
        """
        dest = self.tile(to)
        if dest.impassable:
            return None
        if dest.obstacle and not flying:
            return None
        if flying or climb_free:
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
        climb_free: bool = False,
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
                step = self.step_cost(pos, nxt, flying=flying, climb_free=climb_free)
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
        climb_free: bool = False,
    ) -> Mapping[Pos, int]:
        """Tiles reachable within ``budget`` steps, mapped to their cost.

        Includes ``start`` at cost 0 (a frame may always stay put). ``start`` is
        removed from ``occupied`` so callers can pass every frame position.
        Frames block movement, so occupied tiles can be neither entered nor
        crossed. ``climb_free`` drops the elevation surcharge -- see
        `step_cost`.
        """
        dist, _ = self._dijkstra(start, budget, occupied, flying,
                                 climb_free=climb_free)
        return dist

    def path(
        self,
        start: Pos,
        goal: Pos,
        budget: int,
        *,
        occupied: frozenset[Pos] = frozenset(),
        flying: bool = False,
        climb_free: bool = False,
    ) -> Optional[Sequence[Pos]]:
        """A cheapest legal path from ``start`` to ``goal`` inclusive, or None."""
        if start == goal:
            return (start,)
        dist, prev = self._dijkstra(start, budget, occupied, flying, goal=goal,
                                    climb_free=climb_free)
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
        """Tile elevation, +1 if a frame stands there (rules.tex:485).

        Used for *intervening* tiles only. The attacker's and target's own
        reference heights are their bare tile elevations -- the worked figures
        require it: in rules.tex:492 an elevation-1 tile blocks an attacker
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

        The four obstruction kinds (rules.tex:477):

        * impassable terrain -- anywhere on the line;
        * terrain higher than the *attacker* -- anywhere on the line;
        * obstacles *adjacent to the target*;
        * terrain higher than the *target*, *adjacent to the target*.

        A tile holding a frame counts one elevation higher. Obstacles do not
        block LoS to or from a ``Flying`` frame (rules.tex:967).

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
        """LoS: clear if *any* line from the attacker's tile to the target's
        centre is unobstructed.

        The rule is "draw a line starting from anywhere on the attacking frame
        to the center of the target" (rules.tex:474), so it is permissive at the
        source -- the attacker's whole tile counts as its extent -- but pinned
        at the far end to one point, the centre of the target's tile. All four
        obstruction kinds are line-based: an obstruction matters only when the
        line actually crosses it.

        **Approximation.** The source extent is sampled on an ``n x n`` grid of
        interior points (``n`` = :data:`LOS_SAMPLES_PER_AXIS`, inset from the
        edges by :data:`LOS_EDGE_INSET` so no sample sits on a grid line), and
        each sample-to-target-centre segment is tested. A segment is obstructed
        when it crosses the *interior* of an obstructing tile; grazing an edge
        or clipping a corner is not a crossing, which is what makes "shoot along
        the row" resolve the way the first worked figure does. Samples are tried
        centre first, then corners, stopping at the first clear line.

        The approximation is one-sided: every line it accepts is a genuinely
        unobstructed line, so it never invents line of sight. It can only miss
        one -- a source point too fine for the grid to land on. At n=5 the
        samples include the tile corners, which is where the extreme lines are.

        All three worked figures (rules.tex:488-538) agree with this model; see
        ``tests/test_board.py`` for all six of their verdicts.
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
        tx, ty = target.x + 0.5, target.y + 0.5   # "the center of the target"
        for ax, ay in _sample_points(attacker, n):
            if not any(
                _segment_crosses_cell(ax, ay, tx, ty, cx, cy) for cx, cy in cells
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
