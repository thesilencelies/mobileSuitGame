"""Terrain that hurts whatever ends a turn on it.

Most terrain cards are just ground, and the ones that carry points are
objectives (`objectives.py`). A card can be neither: the Railway prints a rule
in the `Rules` column and scores nothing at all -- "Any frame that ends a turn
on the rails takes \\smallenergy low". Nothing scores, but something happens,
and this is where that kind of card lives.

The rails are the card's own ``tkn`` cells, so nothing here has to be told
where they are. :class:`~playtest.engine.types.Tile` already carries the card
it came from and which of its cells the card marked, so both copies on the
battlefield -- including the opponent's, dealt rotated 180 degrees -- fall out
of the terrain for free.

A frame killed by terrain still counts as "defeated" for the opposing side's
victory point, the same as any other death the engine cannot attribute to a
killer (`state.destroy_frame`). The alternative -- a death nobody scores --
would make walking onto the rails at 1 hit point a way to deny a point.

Pure data and queries over a `Tile`: `resolve.cleanup_phase` is what actually
applies it, and the AI imports this table so it can price the ground it is
about to stand on off the same rule rather than a second copy of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from .types import DamageType, Tile, Zone


@dataclass(frozen=True)
class Hazard:
    """What a hazardous terrain card does to a frame that ends a turn on it."""

    #: The terrain card's name, as `Terrain_square.csv` prints it.
    card: str
    #: What to call the dangerous part, for the log and the tile read-out.
    what: str
    zone: Zone = "Low"
    amount: int = 1
    #: Cosmetic: the engine models no resistances, so this only reads out.
    damage_type: DamageType = "energy"
    #: Which of the card's cells are dangerous, named by the `Tile` flag that
    #: marks them. The Railway's rails are its `tkn` cells.
    cells: str = "token_spawn"


#: Terrain card -> its hazard. Cards with points are objectives instead.
HAZARDS: Mapping[str, Hazard] = {
    "Railway": Hazard(card="Railway", what="the rails"),
}


def hazard_for(tile: Optional[Tile]) -> Optional[Hazard]:
    """The hazard on this tile, if it is one of a hazardous card's own cells."""
    if tile is None:
        return None
    hazard = HAZARDS.get(tile.terrain_card)
    if hazard is None or not getattr(tile, hazard.cells, False):
        return None
    return hazard


def describe(hazard: Hazard) -> str:
    """One line for a player looking at the tile."""
    return (
        f"{hazard.what}: {hazard.amount} {hazard.damage_type} "
        f"{hazard.zone} at the end of a turn"
    )
