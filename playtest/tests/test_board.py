"""Board geometry: adjacency, movement costs and line of sight (workstream B1).

The three line-of-sight figures in rules.tex (lines 488-538) are reproduced
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
# Adjacency and range (rules.tex:278)
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
    assert len(within) == 25  # (2*2+1)^2, rules.tex:279
    assert board.distance(Pos(0, 0), Pos(3, 1)) == 3


def test_out_of_bounds():
    board = board_from_codes([["", ""], ["", ""]])
    assert not board.in_bounds(Pos(-1, 0))
    assert not board.in_bounds(Pos(2, 0))
    with pytest.raises(IndexError):
        board.tile(Pos(2, 0))


# --------------------------------------------------------------------------
# Movement (rules.tex:446-451)
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
    """The climb is paid on entry and is all-or-nothing (rules.tex:450).

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
# Line of sight -- the three worked figures (rules.tex:488-538)
# --------------------------------------------------------------------------


def test_los_figure_one_sports_field(cards):
    """rules.tex:492. A on the ground, another frame beside it.

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
    """rules.tex:512. An obstacle only blocks when it is next to the target.

    Both shots pass through an obstacle. The left target is in sight because its
    obstacle is a tile away and so is not an obstruction at all. The right
    target is blocked by the obstacle sitting right beside it.
    """
    board = single_card_board(cards, "Waste Camp")
    attacker = printed(0, 0)
    left, right = printed(0, 3), printed(2, 3)

    assert board.has_line_of_sight(attacker, left)
    assert board.line_of_sight_blockers(attacker, left) == frozenset()

    assert not board.has_line_of_sight(attacker, right)
    # the figure's red cross: the obstacle beside the right target
    assert printed(2, 2) in board.line_of_sight_blockers(attacker, right)


def test_los_figure_two_right_target_stays_blocked_however_finely_sampled(cards):
    """The verdict the source-permissive reading used to get wrong.

    While the line could also end anywhere on the target, a line from A's far
    corner to the target's *side* slipped past the obstacle and made this target
    visible, contradicting the figure. Pinning the far end to the target's
    centre (rules.tex:474) closes that gap: every line from anywhere inside A's
    tile to that one point crosses the obstacle. Only a line from the exact
    corner of A's tile grazes the obstacle's corner, and a frame's extent is the
    open tile, so no sampling density admits it.
    """
    board = single_card_board(cards, "Waste Camp")
    attacker, right = printed(0, 0), printed(2, 3)
    for n in (1, 3, 5, 9, 21, 81):
        assert not board.has_line_of_sight(attacker, right, samples=n)


def test_los_figure_three_warehouse(cards):
    """rules.tex:528. Terrain higher than the target behaves the same way.

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


def test_los_is_permissive_at_the_source():
    """A blocker on the centre line that a line from A's corner misses.

    Both lines end at the same point -- the target's centre -- so this is the
    source permissiveness of rules.tex:474 doing the work, not the far end.
    """
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


def test_the_line_must_end_at_the_target_centre():
    """The far end is a point, not an extent (rules.tex:474).

    Waste Camp's geometry in the abstract: an obstacle beside the target that a
    line to the target's near *edge* would slip past, but a line to its centre
    cannot. Shown on the segment primitive, since only the centre is reachable
    through the public predicate.
    """
    board = board_from_codes([
        ["", "", ""],
        ["", "", "obs"],
        ["", "", ""],
        ["", "", ""],
    ])
    attacker, target = Pos(0, 3), Pos(2, 0)
    assert board.line_of_sight_blockers(attacker, target) == frozenset({Pos(2, 1)})

    corner = (0.98, 3.02)          # the most favourable point of A's tile
    centre = (target.x + 0.5, target.y + 0.5)
    near_edge = (target.x + 0.02, target.y + 0.02)
    assert _segment_crosses_cell(*corner, *centre, 2, 1)        # the rule's line
    assert not _segment_crosses_cell(*corner, *near_edge, 2, 1)  # the old one
    assert not board.has_line_of_sight(attacker, target)


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
    """rules.tex:967: obstacles do not block LoS to or from a flying frame."""
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


# --------------------------------------------------------------------------
# What movement costs, and what a refusal says
# --------------------------------------------------------------------------


def test_a_frame_on_a_tile_changes_nothing_about_what_movement_costs():
    """The +1 for a frame is a *line of sight* rule and only that.

    Reported the other way round from a screenshot -- movement looked as though
    it were charging for the frames standing about. It is not, and this pins it:
    the same walk costs the same with and without a frame in the middle of it.
    """
    from dataclasses import replace as _replace

    from playtest.engine.types import Tile

    W = H = 6
    tiles = [Tile(Pos(x, y)) for y in range(H) for x in range(W)]
    for x, y in ((3, 1), (3, 2), (3, 3)):
        tiles[y * W + x] = _replace(tiles[y * W + x], elevation=1)
    board = Board(W, H, tiles)

    bare = board.reachable(Pos(1, 2), 4)
    # A frame in the way is passed as `occupied`, which is what stops a *route*
    # -- it never changes the price of a step.
    with_frame = board.reachable(Pos(1, 2), 4, occupied=frozenset({Pos(0, 0)}))
    assert {p: c for p, c in bare.items() if p != Pos(0, 0)} == {
        p: c for p, c in with_frame.items() if p != Pos(0, 0)
    }
    assert board.step_cost(Pos(2, 2), Pos(3, 2)) == 2, "1 step + 1 level climbed"
    assert board.step_cost(Pos(3, 2), Pos(2, 2)) == 1, "coming down is free"


def test_a_movement_decision_prices_the_climbs_it_will_not_offer():
    """A tile refused for terrain says what it would have cost.

    Without this the player sees a gap in the green and has to infer the rule
    from an absence -- which is exactly how "the engine is charging me for
    something" gets believed. Tiles that are merely too far away are left out:
    they explain themselves.
    """
    from dataclasses import replace as _replace

    from playtest.engine import resolve as R
    from playtest.engine.state import Resolution
    from playtest.engine.types import Tile

    from ._helpers import add_frame, give, make_state

    W = H = 6
    tiles = [Tile(Pos(x, y)) for y in range(H) for x in range(W)]
    for y in (2, 3, 4):
        tiles[y * W + 1] = _replace(tiles[y * W + 1], elevation=2)
    state = make_state()
    state.board = Board(W, H, tiles)
    frame = add_frame(state, 0, "Kuwagata", Pos(2, 3))
    uid = give(state, frame, "Railgun_Hypervelocity Shot")
    card = state.catalogue["Railgun_Hypervelocity Shot"]
    state.resolution = Resolution(frame_id=frame.id, uid=uid, steps=["movement"])

    assert R._movement_decision(state, frame, card)
    pending = state.pending
    budget = pending.context["budget"]
    offered = {(o["x"], o["y"]) for o in pending.options}
    priced = {(o["x"], o["y"]): o["cost"] for o in pending.context["outOfReach"]}

    assert (1, 3) not in offered, "1 step + 2 levels is 3, and the budget is 2"
    assert priced[(1, 3)] == 3 > budget
    assert all(spot not in offered for spot in priced), "context is never an answer"
    assert not any(
        state.board.tile(Pos(*spot)).elevation == 0 for spot in priced
    ), "a tile that is only too far away explains itself and is left out"


def test_a_route_may_run_through_a_friendly_frame_but_not_stop_on_it():
    """"Tiles occupied by friendly frames can be passed through, but tiles
    occupied by enemy frames cannot, and a movement cannot end on an occupied
    tile" (rules.tex:452).

    The grid is a playtest report's, and the tile it turns on is the one behind
    the ally: with 2 movement it is only reachable *through* the ally, so if the
    pass-through is not working it is the one tile that disappears.
    """
    from dataclasses import replace as _replace

    from playtest.engine.types import Tile

    from ._helpers import add_frame, make_state

    elevations = [[0, 0, 0, 0],
                  [2, 1, 0, 1],
                  [2, 1, 0, 1],
                  [2, 1, 0, 1]]
    W = H = 4
    tiles = [Tile(Pos(x, y)) for y in range(H) for x in range(W)]
    for y in range(H):
        for x in range(W):
            tiles[y * W + x] = _replace(
                tiles[y * W + x], elevation=elevations[y][x])
    state = make_state()
    state.board = Board(W, H, tiles)
    actor = add_frame(state, 0, "VX4-Nautilus", Pos(1, 2))
    ally = add_frame(state, 0, "VX4-Nautilus", Pos(1, 1))

    costs = {(o["x"], o["y"]): o["cost"] for o in state.walk_options(actor, 2)}
    assert (1, 1) not in costs, "a move may not end on the ally"
    assert costs[(0, 0)] == 2, (
        "one step through the ally and one diagonal step off it"
    )
    assert costs[(1, 0)] == 2 and costs[(2, 0)] == 2
    # Climbing is 1 per level (rules.tex:450), so the elevation-2 column beside
    # an elevation-1 frame is 2 to enter -- reachable, exactly.
    assert costs[(0, 1)] == costs[(0, 2)] == costs[(0, 3)] == 2
    # The elevation-1 column on the far side is not: 1 to cross the flat tile
    # and 2 to climb.
    assert (3, 1) not in costs and (3, 2) not in costs and (3, 3) not in costs

    # Same board, but the frame in the way is an enemy: the tile behind it is
    # the one that goes.
    ally.seat = 1
    blocked = {(o["x"], o["y"]) for o in state.walk_options(actor, 2)}
    assert (0, 0) not in blocked, "an enemy frame cannot be passed through"


def test_the_gravity_well_is_why_a_step_can_cost_more_than_the_terrain():
    """The reported case, end to end: the well is what refuses the move.

    A frame on elevation 1 beside an elevation-2 column can step onto it for 2
    -- until a well to the east makes every westward step cost one more, at
    which point the whole column is out of reach with 2 movement and nothing
    about the terrain has changed.
    """
    from dataclasses import replace

    from playtest.engine import effects_state as fx
    from playtest.engine import resolve as R
    from playtest.engine.board import Board
    from playtest.engine.state import Resolution
    from playtest.engine.types import Pos, Tile

    from ._helpers import add_frame, give, make_state

    W, H = 7, 4
    tiles = [Tile(Pos(x, y)) for y in range(H) for x in range(W)]
    for y in range(1, 4):
        tiles[y * W + 0] = replace(tiles[y * W + 0], elevation=2)
        tiles[y * W + 1] = replace(tiles[y * W + 1], elevation=1)
    state = make_state()
    state.board = Board(W, H, tiles)
    actor = add_frame(state, 0, "VX4-Nautilus", Pos(1, 2))
    card = state.catalogue["Railgun_Hypervelocity Shot"]

    def offer():
        state.pending = None
        state.resolution = Resolution(
            frame_id=actor.id, uid=give(state, actor,
                                        "Railgun_Hypervelocity Shot"),
            steps=["movement"])
        assert R._movement_decision(state, actor, card)
        pending = state.pending
        return (
            {(o["x"], o["y"]): o["cost"] for o in pending.options},
            {(o["x"], o["y"]): o["cost"]
             for o in pending.context.get("outOfReach", [])},
        )

    reachable, _priced = offer()
    assert reachable[(0, 2)] == 2, "one step and one level of climb"

    fx.spawn_token(state, fx.GRAVITY_WELL, Pos(5, 2), owner=1)
    reachable, priced = offer()
    assert (0, 2) not in reachable, "stepping away from the well costs one more"
    assert priced[(0, 2)] == 3
    assert reachable[(2, 2)] == 1, "and stepping toward it does not"
