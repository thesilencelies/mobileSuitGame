"""Terrain card parsing (workstream B1)."""

from __future__ import annotations

import pytest

from playtest.engine.terrain import (
    CARD_COLS,
    CARD_ROWS,
    TerrainCard,
    load_terrain_cards,
    load_terrain_deck,
    objective_cards,
    parse_cell,
)
from playtest.engine.types import Pos


@pytest.fixture(scope="module")
def cards() -> dict[str, TerrainCard]:
    return load_terrain_cards()


def printed(card: TerrainCard, col: int, row_from_bottom: int):
    """The tile the printed card shows at ``col``, counting rows from the bottom.

    ``terrain_cards.py`` renders CSV row 0 at the bottom of the card, while
    ``TerrainCard.grid`` is stored top row first, so the two are flipped.
    """
    return card.tile(CARD_ROWS - 1 - row_from_bottom, col)


# --------------------------------------------------------------------------
# Cell codes
# --------------------------------------------------------------------------


def test_empty_cell_is_ground():
    tile = parse_cell("", Pos(0, 0), "X")
    assert tile.elevation == 0
    assert not (tile.impassable or tile.obstacle or tile.objective or tile.token_spawn)
    assert tile.terrain_card == "X"


def test_codes_combine_within_a_cell():
    tile = parse_cell("obs tkn obj", Pos(1, 2), "Power Reactors")
    assert tile.obstacle and tile.token_spawn and tile.objective
    assert tile.elevation == 0
    assert tile.pos == Pos(1, 2)


def test_elevation_and_impassable_codes():
    assert parse_cell("e1", Pos(0, 0)).elevation == 1
    assert parse_cell("e2", Pos(0, 0)).elevation == 2
    assert parse_cell("e3 obs obj tkn", Pos(0, 0)).elevation == 3
    assert parse_cell("im", Pos(0, 0)).impassable


def test_unknown_codes_are_ignored_like_the_renderer():
    # 'N/A' (Sports Field, Park), 'e' (Suburb L) and 'BehindText' (Helpcard)
    # are data artefacts; terrain_cards.py draws them as plain ground.
    for code in ("N/A", "e", "BehindText"):
        tile = parse_cell(code, Pos(0, 0))
        assert tile.elevation == 0
        assert not (tile.impassable or tile.obstacle)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def test_every_card_is_four_by_three(cards):
    assert cards
    for card in cards.values():
        assert card.rows == CARD_ROWS
        assert card.cols == CARD_COLS
        assert all(t.terrain_card == card.name for t in card.tiles())


def test_helpcard_is_a_legend_not_a_card(cards):
    assert "Helpcard" not in cards
    assert "Helpcard" in load_terrain_cards(include_legends=True)


def test_eight_objectives_are_in_scope(cards):
    objs = objective_cards(cards)
    assert set(objs) == {
        "Power Reactors",
        "Shiny Thing",
        "Triangle",
        "Fugitive",
        "Holo Spires",
        "Church",
        "The Tower",
        "The Egg",
    }


def test_objective_metadata(cards):
    tower = cards["The Tower"]
    assert tower.is_objective
    assert (tower.defend_points, tower.attack_points, tower.token_count) == (2, 2, 1)
    assert "4 hitpoints" in tower.rules_text
    assert tower.image == "terrain_27.png"
    # The Tower's single objective/token cell is the e3 obstacle block.
    assert len(tower.objective_cells) == 1
    assert tower.objective_cells == tower.token_cells
    cell = tower.tile(tower.objective_cells[0].y, tower.objective_cells[0].x)
    assert cell.elevation == 3 and cell.obstacle


def test_plain_terrain_has_no_points(cards):
    assert not cards["Warehouse"].is_objective
    assert cards["Warehouse"].token_count == 0


# --------------------------------------------------------------------------
# Orientation: CSV row 0 is the printed bottom row
# --------------------------------------------------------------------------


def test_grid_is_stored_printed_top_row_first(cards):
    # Warehouse CSV rows, 0 first: e2 e2 e2 / e1 e2 e2 / e1 e1 . / . . .
    warehouse = cards["Warehouse"]
    assert [t.elevation for t in warehouse.grid[0]] == [0, 0, 0]  # printed top
    assert [t.elevation for t in warehouse.grid[1]] == [1, 1, 0]
    assert [t.elevation for t in warehouse.grid[2]] == [1, 2, 2]
    assert [t.elevation for t in warehouse.grid[3]] == [2, 2, 2]  # printed bottom
    assert printed(warehouse, 0, 0).elevation == 2
    assert printed(warehouse, 0, 3).elevation == 0


def test_local_positions_match_grid_indices(cards):
    card = cards["Warehouse"]
    for r in range(card.rows):
        for c in range(card.cols):
            assert card.tile(r, c).pos == Pos(c, r)


# --------------------------------------------------------------------------
# 180 degree rotation (the opponent's two rows)
# --------------------------------------------------------------------------


def test_rotation_reverses_both_axes(cards):
    card = cards["Warehouse"]
    turned = card.rotated_180()
    assert turned.rotated and not card.rotated
    for r in range(card.rows):
        for c in range(card.cols):
            original = card.tile(card.rows - 1 - r, card.cols - 1 - c)
            rotated = turned.tile(r, c)
            assert rotated.elevation == original.elevation
            assert rotated.obstacle == original.obstacle
            assert rotated.impassable == original.impassable
            assert rotated.objective == original.objective
            assert rotated.token_spawn == original.token_spawn
            # positions are re-stamped to the new grid slot
            assert rotated.pos == Pos(c, r)


def test_rotation_is_its_own_inverse(cards):
    for name in ("Warehouse", "The Tower", "Fugitive", "Holo Spires"):
        card = cards[name]
        there_and_back = card.rotated_180().rotated_180()
        assert there_and_back.grid == card.grid
        assert there_and_back.rotated == card.rotated


def test_rotation_moves_a_corner_feature_to_the_opposite_corner(cards):
    # Fugitive's objective sits in the printed second row, left column.
    fugitive = cards["Fugitive"]
    (obj,) = fugitive.objective_cells
    turned = fugitive.rotated_180()
    (turned_obj,) = turned.objective_cells
    assert turned_obj == Pos(fugitive.cols - 1 - obj.x, fugitive.rows - 1 - obj.y)


def test_rotation_preserves_metadata(cards):
    tower = cards["The Tower"].rotated_180()
    assert tower.name == "The Tower"
    assert tower.defend_points == 2 and tower.attack_points == 2
    assert tower.token_count == 1


# --------------------------------------------------------------------------
# Decks
# --------------------------------------------------------------------------


def test_deck_lookup_allows_duplicates(cards):
    deck = load_terrain_deck(["Warehouse", "Warehouse", "The Tower"], cards)
    assert [c.name for c in deck] == ["Warehouse", "Warehouse", "The Tower"]


def test_unknown_deck_entry_raises(cards):
    with pytest.raises(KeyError):
        load_terrain_deck(["Not A Card"], cards)
