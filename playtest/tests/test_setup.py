"""Battlefield setup: dealing, objective placement and deployment (B1)."""

from __future__ import annotations

import random

import pytest

from playtest.engine.setup import (
    OBJECTIVE_DECK_SIZE,
    TERRAIN_DECK_SIZE,
    Battlefield,
    SeatDecks,
    available_deck_pairs,
    cards_per_row,
    deal_battlefield,
    deployment_order,
    deployment_row,
    deployment_tiles,
    load_deck_pair,
    load_objective_deck_file,
    load_terrain_deck_file,
    seat_card_rows,
    split_objectives,
    validate_deck_pair,
)
from playtest.engine.terrain import TerrainCard, load_terrain_cards
from playtest.engine.types import Pos

PLAIN = [
    "Warehouse", "Box", "High Tower", "Park", "Slum road", "Suburb L",
    "Cross Junction", "Split Offices", "Wastes", "U homes",
]


@pytest.fixture(scope="module")
def cards() -> dict[str, TerrainCard]:
    return load_terrain_cards()


def deck(cards, objectives: list[str], terrain: list[str] | None = None) -> SeatDecks:
    """A seat's two decks, built in code so slots and cards are predictable."""
    return SeatDecks(
        terrain=tuple(cards[n] for n in (terrain if terrain is not None else PLAIN)),
        objectives=tuple(cards[n] for n in objectives),
    )


@pytest.fixture
def field(cards) -> Battlefield:
    decks = {
        0: deck(cards, ["The Tower", "The Egg"]),
        1: deck(cards, ["Church", "Holo Spires"]),
    }
    return deal_battlefield(
        decks,
        rng=random.Random(7),
        objective_slots={0: [1, 3], 1: [0, 4]},
    )


# --------------------------------------------------------------------------
# Grid shape (rules.tex:338)
# --------------------------------------------------------------------------


def test_three_versus_three_is_fifteen_by_sixteen(field):
    assert cards_per_row(3) == 5
    assert (field.board.width, field.board.height) == (15, 16)
    assert len(field.placements) == 20


def test_two_versus_two_is_twelve_by_sixteen(cards):
    decks = {0: deck(cards, ["The Tower", "The Egg"]), 1: deck(cards, ["Church", "Triangle"])}
    field = deal_battlefield(decks, rng=random.Random(1), frames_per_side=2)
    assert cards_per_row(2) == 4
    assert (field.board.width, field.board.height) == (12, 16)


def test_seats_own_two_rows_each_with_seat_zero_at_the_bottom():
    assert seat_card_rows(0) == (2, 3)
    assert seat_card_rows(1) == (0, 1)


def test_every_tile_knows_which_card_it_came_from(field):
    assert all(t.terrain_card for t in field.board.tiles())
    names = {t.terrain_card for t in field.board.tiles()}
    assert names == {p.card.name for p in field.placements}


# --------------------------------------------------------------------------
# Rotation of the opponent's half
# --------------------------------------------------------------------------


def test_opponent_rows_are_dealt_rotated(field):
    for placement in field.placements:
        assert placement.rotated == (placement.seat == 1)
        assert (placement.card_row in (0, 1)) == (placement.seat == 1)


def test_rotation_shows_up_in_board_tiles(cards, field):
    """A seat-1 card's tiles are its printed grid turned through 180 degrees."""
    placement = next(p for p in field.placements if p.seat == 1)
    original = cards[placement.card.name]
    origin = placement.origin()
    for r in range(original.rows):
        for c in range(original.cols):
            src = original.tile(original.rows - 1 - r, original.cols - 1 - c)
            laid = field.board.tile(Pos(origin.x + c, origin.y + r))
            assert laid.elevation == src.elevation
            assert laid.obstacle == src.obstacle
            assert laid.objective == src.objective


def test_seat_zero_cards_are_laid_upright(cards, field):
    placement = next(p for p in field.placements if p.seat == 0)
    original = cards[placement.card.name]
    origin = placement.origin()
    for r in range(original.rows):
        for c in range(original.cols):
            laid = field.board.tile(Pos(origin.x + c, origin.y + r))
            assert laid.elevation == original.tile(r, c).elevation


# --------------------------------------------------------------------------
# Objectives (rules.tex:340)
# --------------------------------------------------------------------------


def test_one_objective_per_row(field):
    assert len(field.objectives) == 4
    rows = sorted(
        p.card_row for p in field.placements
        if p.card.name in {o.name for o in field.objectives}
    )
    assert rows == [0, 1, 2, 3]


def test_objective_slots_are_honoured(field):
    slots = {
        (p.seat, p.card_row): p.card_col
        for p in field.placements
        if p.card.name in {o.name for o in field.objectives}
    }
    assert slots[(0, 2)] == 1 and slots[(0, 3)] == 3
    assert slots[(1, 0)] == 0 and slots[(1, 1)] == 4


def test_defender_is_whoever_brought_the_card(field):
    owners = {o.name: o.owner for o in field.objectives}
    assert owners["The Tower"] == 0 and owners["The Egg"] == 0
    assert owners["Church"] == 1 and owners["Holo Spires"] == 1


def test_objective_points_and_tokens_survive_placement(field):
    tower = next(o for o in field.objectives if o.name == "The Tower")
    assert (tower.defend_points, tower.attack_points) == (2, 2)
    assert tower.token_count == 1
    assert "hitpoints" in tower.rules_text
    assert len(tower.tiles) == 1 and len(tower.token_tiles) == 1
    assert tower.tiles == tower.token_tiles
    assert len(tower.card_tiles) == 12


def test_objective_tiles_are_board_coordinates(field):
    tower = next(o for o in field.objectives if o.name == "The Tower")
    placement = next(p for p in field.placements if p.card.name == "The Tower")
    origin = placement.origin()
    for pos in tower.tiles:
        assert field.board.tile(pos).objective
        assert origin.x <= pos.x < origin.x + 3
        assert origin.y <= pos.y < origin.y + 4
    # unrotated seat-0 card: The Tower's obj cell is printed row 2, right column
    assert tower.tiles[0] == Pos(origin.x + 2, origin.y + 1)


def test_rotated_objective_cells_move_to_the_opposite_corner(cards):
    decks = {
        0: deck(cards, ["Church", "Holo Spires"]),
        1: deck(cards, ["The Tower", "The Egg"]),
    }
    field = deal_battlefield(
        decks, rng=random.Random(3), objective_slots={0: [0, 0], 1: [0, 0]}
    )
    tower = next(o for o in field.objectives if o.name == "The Tower")
    placement = next(p for p in field.placements if p.card.name == "The Tower")
    origin = placement.origin()
    assert placement.seat == 1 and placement.rotated
    assert tower.tiles[0] == Pos(origin.x + 0, origin.y + 2)


def test_split_objectives_still_splits_a_mixed_list(cards):
    mixed = [cards[n] for n in ["The Tower", "The Egg"] + PLAIN]
    objectives, plain = split_objectives(mixed)
    assert [c.name for c in objectives] == ["The Tower", "The Egg"]
    assert len(plain) == len(PLAIN)
    # ...and a mixed list is still accepted by the dealer, split by its points
    pair = SeatDecks.from_mixed(mixed)
    assert pair.objectives == tuple(objectives) and pair.terrain == tuple(plain)


def test_too_few_objectives_is_an_error(cards):
    with pytest.raises(ValueError):
        deal_battlefield({0: deck(cards, ["The Tower"]), 1: deck(cards, ["Church", "Triangle"])},
                         rng=random.Random(0))


def test_too_few_terrain_cards_is_an_error(cards):
    short = deck(cards, ["The Tower", "The Egg"], terrain=["Warehouse", "Box", "Park"])
    with pytest.raises(ValueError):
        deal_battlefield({0: short, 1: deck(cards, ["Church", "Triangle"])},
                         rng=random.Random(0))


# --------------------------------------------------------------------------
# The two-deck format (rules.tex:253)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["assault", "control", "siege", "strike"])
def test_shipped_deck_pairs_are_legal(cards, name):
    pair = load_deck_pair(name, cards)
    assert len(pair.terrain) == TERRAIN_DECK_SIZE == 10
    assert len(pair.objectives) == OBJECTIVE_DECK_SIZE == 5
    assert validate_deck_pair(pair, strict_size=True) == []
    # the file split and the points columns agree
    assert not any(c.is_objective for c in pair.terrain)
    assert all(c.is_objective for c in pair.objectives)
    assert [c.name for c in pair.terrain] == load_terrain_deck_names(name)
    assert [c.name for c in pair.objectives] == [
        c.name for c in load_objective_deck_file(name, cards)
    ]


def load_terrain_deck_names(name: str) -> list[str]:
    return [c.name for c in load_terrain_deck_file(name)]


def test_all_four_archetypes_have_both_decks():
    assert available_deck_pairs() == ("assault", "control", "siege", "strike")


def test_validate_rejects_a_scoring_card_in_the_terrain_deck(cards):
    bad = SeatDecks(terrain=(cards["The Tower"],), objectives=(cards["Church"],))
    assert validate_deck_pair(bad) == [
        "The Tower scores points but is in the terrain deck"
    ]
    worse = SeatDecks(terrain=(cards["Warehouse"],), objectives=(cards["Warehouse"],))
    assert validate_deck_pair(worse) == [
        "Warehouse scores nothing but is in the objective deck"
    ]
    with pytest.raises(ValueError, match="terrain deck"):
        deal_battlefield({0: bad, 1: load_deck_pair("siege", cards)}, rng=random.Random(0))


# --------------------------------------------------------------------------
# Deployment (rules.tex:347)
# --------------------------------------------------------------------------


def test_deployment_rows_are_the_outer_edges(field):
    assert deployment_row(field.board, 0) == 15
    assert deployment_row(field.board, 1) == 0
    assert all(p.y == 15 for p in field.deployment[0])
    assert all(p.y == 0 for p in field.deployment[1])
    for seat in (0, 1):
        assert deployment_tiles(field.board, seat) == field.deployment[seat]


def test_deployment_skips_terrain_a_frame_cannot_stand_on(field):
    for seat in (0, 1):
        y = deployment_row(field.board, seat)
        allowed = set(field.deployment[seat])
        for x in range(field.board.width):
            tile = field.board.tile(Pos(x, y))
            standable = not (tile.impassable or tile.obstacle)
            assert (Pos(x, y) in allowed) is standable
        assert len(allowed) >= 3  # room for three frames


def test_deployment_alternates_one_frame_at_a_time():
    assert deployment_order(3) == (0, 1, 0, 1, 0, 1)
    assert deployment_order(3, first_seat=1) == (1, 0, 1, 0, 1, 0)
    assert deployment_order(2) == (0, 1, 0, 1)


# --------------------------------------------------------------------------
# Determinism and the shipped decks
# --------------------------------------------------------------------------


def test_dealing_is_deterministic_given_a_seed(cards):
    decks = {0: deck(cards, ["The Tower", "The Egg"]), 1: deck(cards, ["Church", "Triangle"])}
    a = deal_battlefield(decks, rng=random.Random(42))
    b = deal_battlefield(decks, rng=random.Random(42))
    c = deal_battlefield(decks, rng=random.Random(43))
    layout = lambda f: [(p.card.name, p.card_row, p.card_col) for p in f.placements]
    assert layout(a) == layout(b)
    assert layout(a) != layout(c)


@pytest.mark.parametrize("name", ["assault", "control", "siege", "strike"])
def test_shipped_deck_pairs_deal_a_three_versus_three(cards, name):
    """10 terrain + 5 objectives per player; a 3v3 draws 8 and 2 of them."""
    own = load_deck_pair(name, cards)
    field = deal_battlefield(
        {0: own, 1: load_deck_pair("strike", cards)}, rng=random.Random(5)
    )
    assert (field.board.width, field.board.height) == (15, 16)
    assert len(field.placements) == 20
    assert len(field.objectives) == 4

    mine = [p for p in field.placements if p.seat == 0]
    drawn_objectives = [o.name for o in field.objectives if o.owner == 0]
    assert len(drawn_objectives) == 2
    assert set(drawn_objectives) <= {c.name for c in own.objectives}
    # the decks are drawn from, not exhausted: 8 of 10 terrain, 2 of 5 objectives
    filler = [p.card.name for p in mine if p.card.name not in drawn_objectives]
    assert len(filler) == 8
    assert len(set(filler)) == 8
    assert set(filler) < {c.name for c in own.terrain}


def test_the_fill_is_a_shuffled_draw_not_the_whole_deck(cards):
    """Different seeds must draw different subsets of the 10-card terrain deck."""
    decks = {0: load_deck_pair("assault", cards), 1: load_deck_pair("siege", cards)}
    drawn = set()
    for seed in range(8):
        field = deal_battlefield(decks, rng=random.Random(seed))
        names = frozenset(
            p.card.name for p in field.placements
            if p.seat == 0 and not p.card.is_objective
        )
        assert len(names) == 8
        drawn.add(names)
    assert len(drawn) > 1


def test_every_slot_is_filled_exactly_once(field):
    slots = [(p.card_row, p.card_col) for p in field.placements]
    assert len(set(slots)) == len(slots) == 20
    assert all(field.board.tile(p) is not None for p in field.board.positions())
