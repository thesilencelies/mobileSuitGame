"""Shared type contract for the NetFrame playtest engine.

This module is the seam between the spatial layer (board/terrain/LoS), the combat
and state layer, the AI and the server. Every workstream imports from here.

Treat it as frozen: add fields if you genuinely need them, but do not rename or
repurpose what is already here without saying so in your report.

Nothing in this module may import from any other engine module -- it sits at the
bottom of the dependency graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal, Mapping, Optional, Protocol, Sequence

# --------------------------------------------------------------------------
# Zones and damage
# --------------------------------------------------------------------------

Zone = Literal["High", "Mid", "Low"]

#: Canonical ordering, high to low. Elevation shifts move an attack along this
#: tuple: toward index 0 when the attacker is higher, toward index 2 when lower.
ZONES: tuple[Zone, Zone, Zone] = ("High", "Mid", "Low")

DamageType = Literal["cut", "pierce", "impact", "projectile", "energy"]

#: Which zone's "last hit" penalty maps to which stat (rules.tex:583).
#: High -> -1 initiative, Mid -> -1 card drawn, Low -> -1 movement.
LAST_HIT_PENALTY: Mapping[Zone, str] = {
    "High": "initiative",
    "Mid": "cards",
    "Low": "movement",
}

# --------------------------------------------------------------------------
# Tuning constants
# --------------------------------------------------------------------------

#: Turns in a game (rules.tex:223).
TURNS_PER_GAME = 5

#: Cards drawn during planning before modifiers (rules.tex:373).
BASE_DRAW = 7

#: Actions committed face down per frame per turn (rules.tex:374).
ACTIONS_PER_TURN = 2

#: How armour converts to durability. See SPEC.md "Open rules question".
#: "kill_at_armour"  -> damage >= armour destroys; last-hit penalty at armour-1.
#:                      This matches the worked example at rules.tex:591.
#: "kill_above_armour" -> damage > armour destroys; penalty at armour.
#:                      This matches the prose at rules.tex:578 and simulation/.
ARMOUR_KILLS_AT: Literal["kill_at_armour", "kill_above_armour"] = "kill_at_armour"

#: Status magnitude is fixed regardless of counter count (rules.tex:792); the
#: counter count is only the duration.
STATUS_MAGNITUDE = 2

# --------------------------------------------------------------------------
# Statuses
# --------------------------------------------------------------------------

StatusKind = Literal[
    "stunned", "stimmed",    # -/+ initiative
    "dazed", "lucid",        # -/+ cards drawn
    "slowed", "boosted",     # -/+ movement
    "revealed",              # no opposite
]

#: Debuff -> buff pairs. Opposites annihilate on application (rules.tex:803).
STATUS_OPPOSITES: Mapping[StatusKind, StatusKind] = {
    "stunned": "stimmed", "stimmed": "stunned",
    "dazed": "lucid", "lucid": "dazed",
    "slowed": "boosted", "boosted": "slowed",
}

# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class Pos:
    """A tile coordinate. Origin is the top-left of the assembled board."""

    x: int
    y: int


@dataclass(frozen=True)
class Tile:
    """One square of the battlefield, parsed from a terrain card cell.

    `elevation` is 0-3 (0 = ground). `impassable` and `obstacle` come from the
    `im` and `obs` codes; `objective` marks an `obj` cell and `token_spawn` a
    `tkn` cell. `terrain_card` is the name of the card this tile came from, so
    objectives can find their own tiles and the client can draw card borders.
    """

    pos: Pos
    elevation: int = 0
    impassable: bool = False
    obstacle: bool = False
    objective: bool = False
    token_spawn: bool = False
    terrain_card: str = ""


class BoardProtocol(Protocol):
    """What the combat layer and the AI may assume about the board.

    Implemented by `engine.board.Board` (workstream B1). Everything here is a
    pure query -- the board never mutates and never knows about cards or turns.
    Frame positions are passed in as `occupied` rather than stored, so the same
    board object serves any number of hypothetical positions (which is what lets
    the AI search moves cheaply).
    """

    width: int
    height: int

    def tile(self, pos: Pos) -> Tile: ...

    def in_bounds(self, pos: Pos) -> bool: ...

    def neighbours(self, pos: Pos) -> Iterable[Pos]:
        """The up-to-8 adjacent tiles, diagonals included (rules.tex:285)."""

    def distance(self, a: Pos, b: Pos) -> int:
        """Chebyshev distance -- range is reckoned as a square (rules.tex:286)."""

    def reachable(
        self,
        start: Pos,
        budget: int,
        *,
        occupied: frozenset[Pos] = frozenset(),
        flying: bool = False,
    ) -> Mapping[Pos, int]:
        """Tiles reachable within `budget` steps, mapped to their cost.

        Climbing costs 1 extra per elevation and cannot stop part-way; descending
        is free. Obstacles, impassable tiles and occupied tiles cannot be entered.
        `flying` ignores obstacles and elevation costs (rules.tex:968).
        """

    def path(
        self,
        start: Pos,
        goal: Pos,
        budget: int,
        *,
        occupied: frozenset[Pos] = frozenset(),
        flying: bool = False,
    ) -> Optional[Sequence[Pos]]:
        """A cheapest legal path, or None if `goal` is not reachable."""

    def has_line_of_sight(
        self,
        attacker: Pos,
        target: Pos,
        *,
        occupied: frozenset[Pos] = frozenset(),
        flying_attacker: bool = False,
    ) -> bool:
        """Permissive LoS: clear if *any* line between the tiles is unobstructed.

        Obstructions (rules.tex:426): impassable terrain; terrain higher than the
        attacker; obstacles adjacent to the target; terrain higher than the target
        adjacent to the target. A tile holding a frame counts one elevation higher.
        """


# --------------------------------------------------------------------------
# Cards
# --------------------------------------------------------------------------

CardType = Literal["weapon", "basic", "booster", "pilot", "drone", "frame"]

#: Keywords parsed out of the Text column. Both `\kw` and `\fullkw` contain the
#: bare keyword, so a substring test catches either form.
Keyword = Literal[
    "feint", "guardbreak", "committed", "closequarters", "knockback", "reload",
    "onhit", "flying", "shield", "deathstrike",
]


@dataclass(frozen=True)
class Card:
    """A card as the engine needs it. `key` is `"{Group}_{Name}"`.

    That key is the deck-CSV entry, the client's lookup key and the card image
    filename stem, so it must stay exactly that shape.
    """

    key: str
    name: str
    group: str
    faction: str
    card_type: CardType

    #: Printed initiative. Usually one value, but `Quick Step` is "8,3" and acts
    #: at each of them if it has not been consumed -- hence a tuple.
    initiative: tuple[int, ...]

    #: Movement modifier, added to the frame's base movement for this action.
    movement: int

    attacks: Mapping[Zone, int]          # damage marks per zone
    ranges: Mapping[Zone, int]           # 0 = melee (must be adjacent)
    dtypes: Mapping[Zone, Optional[DamageType]]
    blocks: Mapping[Zone, int]           # 0 none, 1 normal, >=2 super block

    text: str = ""
    keywords: frozenset[str] = frozenset()
    knockback: int = 0                   # X from Knockback(X), 0 if absent

    #: None means `\infty` (permanent), 0 means no persistence, else turn count.
    persistence: Optional[int] = 0

    #: Drone cards only (Drone actions.csv).
    drone_health: int = 0
    drone_movement: int = 0

    #: Card art, relative to `pictures/`. The rendered PNG is `{key}.png`.
    image: str = ""

    @property
    def is_attack(self) -> bool:
        return any(v > 0 for v in self.attacks.values())

    @property
    def attack_zones(self) -> frozenset[str]:
        return frozenset(z for z in ZONES if self.attacks[z] > 0)

    @property
    def block_zones(self) -> frozenset[str]:
        return frozenset(z for z in ZONES if self.blocks[z] > 0)

    @property
    def super_block_zones(self) -> frozenset[str]:
        return frozenset(z for z in ZONES if self.blocks[z] >= 2)

    @property
    def is_ranged(self) -> bool:
        """True if any attacked zone carries a range (rules.tex:414)."""
        return any(self.ranges[z] > 0 for z in ZONES if self.attacks[z] > 0)


@dataclass(frozen=True)
class FrameSpec:
    """A frame's printed stats, from Frames.csv."""

    name: str
    faction: str
    movement: int
    weapon_slots: int
    booster_slots: int
    deck_size: int
    armour: Mapping[Zone, int]           # Top/Side/Low -> High/Mid/Low
    ability_text: str = ""
    keywords: frozenset[str] = frozenset()   # flying, shield, deathstrike
    shield: int = 0                          # X from Shield(X)
    image: str = ""


# --------------------------------------------------------------------------
# Game structure
# --------------------------------------------------------------------------

Team = int          # seat index; 0 is the human by convention
Phase = Literal["setup", "planning", "action", "cleanup", "finished"]

#: One word per seat. A frame's id is built from it, so the team a frame is on
#: is legible in every mention of it -- and the two words are the two colours
#: the client already paints the seats in.
TEAM_NAMES: tuple[str, ...] = ("Blue", "Red")


def team_name(seat: Team) -> str:
    return TEAM_NAMES[seat % len(TEAM_NAMES)]


def frame_id_for(seat: Team, model: str, ordinal: Optional[int] = None) -> str:
    """A frame's id, which is also the only name anything ever calls it.

    `"Blue Kuwagata"`, or `"Blue Kuwagata 2"` when that seat fields more than
    one of the model. Two of a model is legal -- the rules ask for one deck per
    frame and a shared faction, and say nothing about variety -- so the model
    name alone is not an identity, and a log line naming one is ambiguous
    exactly when it matters most. Building the team and the ordinal into the id
    means every layer (log, prompt, client, AI) inherits an unambiguous name
    without any of them having to compute one.
    """
    base = f"{team_name(seat)} {model}"
    return base if ordinal is None else f"{base} {ordinal}"

DecisionKind = Literal[
    "commit_actions",   # choose ACTIONS_PER_TURN cards from the drawn hand
    "choose_actor",     # which of this seat's tied cards resolves next
    "resolve_order",    # order of movement / effect / attack
    "move",             # choose a destination
    "attack_target",    # choose target frame or token
    "choose_block",     # which remaining card blocks (compulsory when possible)
    "effect_choice",    # a prompt from card text
    "echo_card",        # Echoes of the fallen
    "deploy",           # setup: place a frame
    "place_objective",  # setup: choose an objective's slot in a row
]


@dataclass(frozen=True)
class Command:
    """One decision, from a human seat or the AI -- the only way state advances.

    `payload` is decision-specific and validated by the engine; anything the
    engine did not offer in `PendingDecision.options` must be rejected.
    """

    kind: DecisionKind
    seat: Team
    payload: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PendingDecision:
    """What the engine is waiting for. `None` on the state means it can advance."""

    kind: DecisionKind
    seat: Team
    prompt: str
    options: Sequence[Mapping[str, object]]
    #: Which frame the decision is about, when it is about one.
    frame_id: Optional[str] = None
    #: How many options must be picked, for the decisions that take a *set* of
    #: them rather than one. `commit_actions` is the only one today, and its
    #: range is not a constant: Hyper raises the maximum to three. A client
    #: that assumes two is a client that cannot play the card, so the engine
    #: states the range rather than leaving it to be inferred.
    pick_min: int = 1
    pick_max: int = 1
    #: What a tile decision is asking for, when it is asking for tiles.
    #: `"move"` sends something already on the board to that tile; `"place"`
    #: puts something new on it. They read completely differently to a player
    #: -- "where do I go" against "where does this go" -- and the client
    #: colours them apart, so the engine says which rather than leaving it to
    #: be guessed from the option shape.
    pick_kind: str = ""


@dataclass(frozen=True)
class GameConfig:
    """Everything needed to start a game."""

    player_decks: Sequence[str]      # deck names/paths, one per frame
    ai_decks: Sequence[str]
    seed: Optional[int] = None
    frames_per_side: int = 3
    ai_params: Mapping[str, object] = field(default_factory=dict)
    #: Terrain decks per seat; defaults to a shuffle of decks/deck_terrain_*.csv.
    terrain_decks: Optional[Mapping[Team, str]] = None
