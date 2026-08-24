"""Board geometry: adjacency, movement costs and line of sight (workstream B1).

The three line-of-sight figures in rules.tex (lines 438-488) are reproduced
here against the real terrain cards they are drawn on.
"""

from __future__ import annotations

import pytest

from playtest.engine.board import Board, _segment_crosses_cell
from playtest.engine.terrain import CARD_ROWS, load_terrain_cards, parse_cell
from playtest.engine.types import Pos


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def board_from_codes(rows: list[list[str]]) -> Board:
    """Build a board from terrain cell codes, row 0 at the top (y = 0)."""
    height, width = len(rows), len(rows[0])
    tiles = [
        parse_cell(rows[y][x], Pos(x, y), "test")
        for y in range(height)
        for x in range(width)
    ]
    return Board(width, height, tiles)


@pytest.fixture(scope="module")
def cards():
    return load_terrain_cards()


def single_card_board(cards, name: str) -> Board:
    return Board.from_tile_grids([[cards[name].grid]])


def printed(col: int, row_from_bottom: int) -> Pos:
    """A position given in printed-card coordinates (rows counted from the bottom).

    The rulebook figures are drawn in the terrain card's own TikZ coordinates,
    where row 0 is at the bottom; the board's y axis grows downwards.
    """
    return Pos(col, CARD_ROWS - 1 - row_from_bottom)


# --------------------------------------------------------------------------
# Adjacency and range (rules.tex:285)
# --------------------------------------------------------------------------


def test_adjacency_is_eight_way():
    board = board_from_codes([["", "", ""], ["", "", ""], ["", "", ""]])
    assert set(board.neighbours(Pos(1, 1))) == {
        Pos(0, 0), Pos(1, 0), Pos(2, 0),
        Pos(0, 1),            Pos(2, 1),
        Pos(0, 2), Pos(1, 2), Pos(2, 2),
    }


def test_diagonal_neighbour_is_adjacent():
    board = board_from_codes([["", ""], ["", ""]])
    assert board.is_adjacent(Pos(0, 0), Pos(1, 1))
    assert board.distance(Pos(0, 0), Pos(1, 1)) == 1


def test_neighbours_are_clipped_at_the_edge():
    board = board_from_codes([["", "", ""], ["", "", ""], ["", "", ""]])
    assert set(board.neighbours(Pos(0, 0))) == {Pos(1, 0), Pos(0, 1), Pos(1, 1)}


def test_range_is_a_square():
    board = board_from_codes([["" for _ in range(7)] for _ in range(7)])
    centre = Pos(3, 3)
    within = {p for p in board.positions() if board.distance(centre, p) <= 2}
    assert len(within) == 25  # (2*2+1)^2, rules.tex:286
    assert board.distance(Pos(0, 0), Pos(3, 1)) == 3


def test_out_of_bounds():
    board = board_from_codes([["", ""], ["", ""]])
    assert not board.in_bounds(Pos(-1, 0))
    assert not board.in_bounds(Pos(2, 0))
    with pytest.raises(IndexError):
        board.tile(Pos(2, 0))


# --------------------------------------------------------------------------
# Movement (rules.tex:396-406)
# --------------------------------------------------------------------------


def test_flat_movement_costs_one_per_step_including_diagonals():
    board = board_from_codes([["" for _ in range(5)] for _ in range(5)])
    reach = board.reachable(Pos(2, 2), 1)
    assert reach[Pos(2, 2)] == 0
    assert all(reach[p] == 1 for p in board.neighbours(Pos(2, 2)))
    assert len(reach) == 9


def test_climbing_costs_one_extra_per_level():
    board = board_from_codes([["", "e1", "e2", "e3"]])
    reach = board.reachable(Pos(0, 0), 10)
    assert reach[Pos(1, 0)] == 2   # ground -> e1
    assert reach[Pos(2, 0)] == 4   # + e1 -> e2
    assert reach[Pos(3, 0)] == 6   # + e2 -> e3
    # and a single big step is cheaper than the staircase
    assert board.step_cost(Pos(0, 0), Pos(1, 0)) == 2
    assert board.reachable(Pos(0, 0), 10)[Pos(1, 0)] == 2


def test_descending_any_number_of_levels_is_free():
    board = board_from_codes([["e3", "", "e3"]])
    # e3 -> ground costs one plain step, whatever the drop
    assert board.step_cost(Pos(0, 0), Pos(1, 0)) == 1
    # ...but the same edge climbed costs 1 + 3
    assert board.step_cost(Pos(1, 0), Pos(2, 0)) == 4
    assert board.reachable(Pos(0, 0), 1) == {Pos(0, 0): 0, Pos(1, 0): 1}


def test_climb_descend_asymmetry_round_trip():
    board = board_from_codes([["", "e2", ""]])
    up = board.reachable(Pos(0, 0), 3)
    assert up[Pos(1, 0)] == 3          # 1 step + 2 levels
    down = board.reachable(Pos(1, 0), 3)
    assert down[Pos(0, 0)] == 1        # falling off is free
    assert down[Pos(2, 0)] == 1


def test_cannot_stop_part_way_up():
    """The climb is paid on entry and is all-or-nothing (rules.tex:404).

    A frame with 2 movement standing next to an elevation-2 tile cannot spend
    what it has to get half way -- the tile costs 3 or nothing.
    """
    board = board_from_codes([["", "e2"]])
    assert Pos(1, 0) not in board.reachable(Pos(0, 0), 2)
    assert board.path(Pos(0, 0), Pos(1, 0), 2) is None
    assert board.reachable(Pos(0, 0), 3)[Pos(1, 0)] == 3
    assert board.path(Pos(0, 0), Pos(1, 0), 3) == (Pos(0, 0), Pos(1, 0))


def test_obstacles_and_impassable_cannot_be_entered_or_crossed():
    board = board_from_codes([["", "obs", ""], ["", "im", ""], ["", "obs", ""]])
    reach = board.reachable(Pos(0, 1), 6)
    assert Pos(1, 0) not in reach and Pos(1, 1) not in reach
    # the whole right-hand column is walled off
    assert all(Pos(2, y) not in reach for y in range(3))


def test_frames_block_movement():
    board = board_from_codes([["", "", ""], ["", "", ""], ["", "", ""]])
    occupied = frozenset({Pos(1, 0), Pos(1, 1), Pos(1, 2)})
    reach = board.reachable(Pos(0, 1), 6, occupied=occupied)
    assert all(Pos(2, y) not in reach for y in range(3))
    # the mover's own tile in `occupied` does not trap it
    reach2 = board.reachable(Pos(0, 1), 1, occupied=occupied | {Pos(0, 1)})
    assert Pos(0, 0) in reach2


def test_flying_ignores_obstacles_and_elevation():
    board = board_from_codes([["", "obs", "e3", "im"]])
    reach = board.reachable(Pos(0, 0), 2, flying=True)
    assert reach[Pos(1, 0)] == 1   # crosses (and may stop on) obstacles
    assert reach[Pos(2, 0)] == 2   # no elevation surcharge
    assert Pos(3, 0) not in reach  # impassable still stops everyone


def test_path_takes_the_cheapest_route():
    board = board_from_codes([
        ["", "e3", ""],
        ["", "e3", ""],
        ["", "", ""],
    ])
    path = board.path(Pos(0, 0), Pos(2, 0), 10)
    assert path is not None
    assert path[0] == Pos(0, 0) and path[-1] == Pos(2, 0)
    assert Pos(1, 0) not in path and Pos(1, 1) not in path
    assert board.move_cost(Pos(0, 0), Pos(2, 0), 10) == len(path) - 1 == 4
    assert board.path(Pos(0, 0), Pos(2, 0), 3) is None


def test_path_to_self_is_trivial():
    board = board_from_codes([["", ""]])
    assert board.path(Pos(0, 0), Pos(0, 0), 0) == (Pos(0, 0),)


# --------------------------------------------------------------------------
# Line of sight -- the three worked figures (rules.tex:438-488)
# --------------------------------------------------------------------------


def test_los_figure_one_sports_field(cards):
    """rules.tex:441. A on the ground, another frame beside it.

    Top target in sight; middle target blocked by the intervening frame (a tile
    with a frame counts one elevation higher); bottom target blocked by an
    elevation-1 tile, which is higher than the attacker.
    """
    board = single_card_board(cards, "Sports Field")
    attacker = printed(0, 2)
    occupied = frozenset({printed(1, 2)})           # the neutral frame in the way

    top, middle, bottom = printed(2, 3), printed(2, 2), printed(2, 0)
    assert board.has_line_of_sight(attacker, top, occupied=occupied)
    assert not board.has_line_of_sight(attacker, middle, occupied=occupied)
    assert not board.has_line_of_sight(attacker, bottom, occupied=occupied)

    # the figure's red crosses: the frame, and the e1 tile higher than A
    assert printed(1, 2) in board.line_of_sight_blockers(
        attacker, middle, occupied=occupied
    )
    assert printed(1, 1) in board.line_of_sight_blockers(
        attacker, bottom, occupied=occupied
    )
    # without the neutral frame the middle target is plainly visible
    assert board.has_line_of_sight(attacker, middle)


def test_los_figure_two_waste_camp(cards):
    """rules.tex:459. An obstacle only blocks when it is next to the target.

    The left shot passes through an obstacle two tiles from its target, which is
    therefore not an obstruction at all, and the target is in sight -- the
    figure's lesson, and its verdict.
    """
    board = single_card_board(cards, "Waste Camp")
    attacker = printed(0, 0)
    left = printed(0, 3)

    assert board.has_line_of_sight(attacker, left)
    assert board.line_of_sight_blockers(attacker, left) == frozenset()
    # the obstacle beside the *right* target is an obstruction for that shot
    assert printed(2, 2) in board.line_of_sight_blockers(attacker, printed(2, 3))


def test_los_figure_two_right_target_is_visible_erratum(cards):
    """rules.tex:459's right-hand verdict is wrong; the rule wins.

    The figure draws one line from A through the obstacle beside the target and
    calls the target out of sight. rules.tex:424 lets the line start "anywhere
    on the attacking frame" and end "anywhere on the target", and a line from
    the corner of A's tile reaches the target's near edge without ever entering
    that obstacle -- so the target is visible. The author has confirmed the rule
    is right and the figure is in error.
    """
    board = single_card_board(cards, "Waste Camp")
    attacker, right = printed(0, 0), printed(2, 3)

    assert board.has_line_of_sight(attacker, right)
    # what the figure actually depicts: the centre line alone is blocked
    assert not board.has_line_of_sight(attacker, right, samples=1)


def test_los_figure_three_warehouse(cards):
    """rules.tex:477. Terrain higher than the target behaves the same way.

    A stands on the raised block. The right target is in sight -- the high tile
    on that line is not next to it. The left target is blocked by the raised
    tile right beside it.
    """
    board = single_card_board(cards, "Warehouse")
    attacker = printed(1, 0)
    assert board.tile(attacker).elevation == 2

    right, left = printed(2, 3), printed(0, 3)
    assert board.has_line_of_sight(attacker, right)
    assert not board.has_line_of_sight(attacker, left)

    # the figure's red cross sits on the e1 tile beside the left target
    blockers = board.line_of_sight_blockers(attacker, left)
    assert printed(0, 2) in blockers
    # ...while the e2 tiles the right shot crosses are neither higher than the
    # attacker nor next to the target
    assert printed(2, 1) not in board.line_of_sight_blockers(attacker, right)


# --------------------------------------------------------------------------
# Line of sight -- rules the figures do not cover
# --------------------------------------------------------------------------


def test_los_is_permissive_not_centre_to_centre():
    """A blocker on the centre line that a corner-to-corner line misses."""
    board = board_from_codes([
        ["", "", "", ""],
        ["", "e1", "", ""],
        ["", "", "", ""],
    ])
    attacker, target = Pos(0, 1), Pos(3, 0)
    assert board.line_of_sight_blockers(attacker, target) == frozenset({Pos(1, 1)})
    # the centre-to-centre line clips the raised tile...
    assert not board.has_line_of_sight(attacker, target, samples=1)
    # ...but a line from the attacker's far corner leaves the row before it
    assert board.has_line_of_sight(attacker, target)


def test_impassable_blocks_anywhere_on_the_line():
    board = board_from_codes([
        ["", "", ""],
        ["im", "im", "im"],
        ["", "", ""],
    ])
    assert not board.has_line_of_sight(Pos(0, 2), Pos(2, 0))
    assert board.has_line_of_sight(Pos(0, 2), Pos(2, 2))


def test_terrain_higher_than_the_attacker_blocks_anywhere_on_the_line():
    board = board_from_codes([["", "", "e1", "", ""]])
    # a wall of e1 across a flat row stops a ground-level attacker outright
    assert not board.has_line_of_sight(Pos(0, 0), Pos(4, 0))
    # standing on it, the same tile no longer counts as higher
    high = board_from_codes([["e1", "", "e1", "", ""]])
    assert high.has_line_of_sight(Pos(0, 0), Pos(4, 0))


def test_high_terrain_only_blocks_when_next_to_the_target():
    board = board_from_codes([["e2", "", "e1", "", "", ""]])
    attacker = Pos(0, 0)  # elevation 2, so the e1 tile is not higher than A
    assert board.has_line_of_sight(attacker, Pos(5, 0))   # e1 is 3 tiles away
    assert not board.has_line_of_sight(attacker, Pos(3, 0))  # e1 is adjacent


def test_a_frame_counts_one_elevation_higher():
    board = board_from_codes([["", "", ""]])
    assert board.has_line_of_sight(Pos(0, 0), Pos(2, 0))
    assert not board.has_line_of_sight(
        Pos(0, 0), Pos(2, 0), occupied=frozenset({Pos(1, 0)})
    )


def test_flying_ignores_obstacles_for_line_of_sight():
    """rules.tex:968: obstacles do not block LoS to or from a flying frame."""
    board = board_from_codes([["", "", "obs", ""]])
    attacker, target = Pos(0, 0), Pos(3, 0)
    assert not board.has_line_of_sight(attacker, target)
    assert board.has_line_of_sight(attacker, target, flying_attacker=True)
    assert board.has_line_of_sight(attacker, target, flying_target=True)


def test_line_of_sight_is_directional():
    """It is not symmetric: "higher than the attacker" depends on who shoots."""
    board = board_from_codes([["e2", "e1", "", ""]])
    high, low = Pos(0, 0), Pos(3, 0)
    assert board.has_line_of_sight(high, low)       # A on e2 sees over the e1
    assert not board.has_line_of_sight(low, high)   # from the ground it is a wall


def test_a_tile_always_sees_itself():
    board = board_from_codes([["im"]])
    assert board.has_line_of_sight(Pos(0, 0), Pos(0, 0))


# --------------------------------------------------------------------------
# The segment primitive
# --------------------------------------------------------------------------


def test_segment_crossing_uses_the_open_cell():
    # straight through the middle
    assert _segment_crosses_cell(0.5, 0.5, 2.5, 0.5, 1, 0)
    # running exactly along the cell's top edge is not a crossing
    assert not _segment_crosses_cell(0.5, 1.0, 2.5, 1.0, 1, 0)
    # clipping a single corner is not a crossing
    assert not _segment_crosses_cell(0.5, 1.5, 1.5, 0.5, 0, 0)
    assert not _segment_crosses_cell(0.5, 0.5, 1.5, 1.5, 1, 0)
    # ...but the diagonal of the cell is
    assert _segment_crosses_cell(0.5, 0.5, 2.5, 2.5, 1, 1)
