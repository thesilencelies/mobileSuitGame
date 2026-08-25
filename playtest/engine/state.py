"""Game state: frames, cards in play, statuses, damage, tokens and VP.

Everything mutable in the game lives here. `GameState.clone()` is the basis
of `apply_command`'s purity guarantee: it deep-copies the whole state but
shares the immutable board and card catalogue.

Randomness goes through `GameState.rng` and nothing else, so a game replays
exactly from its seed.
"""

from __future__ import annotations

import copy
import random
import secrets
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

from .types import (
    ACTIONS_PER_TURN,
    ARMOUR_KILLS_AT,
    BASE_DRAW,
    STATUS_MAGNITUDE,
    STATUS_OPPOSITES,
    Card,
    FrameSpec,
    PendingDecision,
    Phase,
    Pos,
    StatusKind,
    Team,
    Zone,
    ZONES,
)

STATUS_KINDS: tuple[StatusKind, ...] = (
    "stunned", "stimmed", "dazed", "lucid", "slowed", "boosted", "revealed",
)

#: Where a card instance currently is.
Location = str  # "deck" | "hand" | "committed" | "discard" | "aside"


# --------------------------------------------------------------------------
# Durability (see SPEC.md "Open rules question")
# --------------------------------------------------------------------------


def zone_destroyed(damage: int, armour: int) -> bool:
    """True when this zone's damage has destroyed the frame."""
    if ARMOUR_KILLS_AT == "kill_at_armour":
        return damage >= armour
    return damage > armour


def zone_at_last_hit(damage: int, armour: int) -> bool:
    """True when this zone is one hit from destruction (rules.tex:583)."""
    if ARMOUR_KILLS_AT == "kill_at_armour":
        return damage == armour - 1
    return damage == armour


# --------------------------------------------------------------------------
# Card instances
# --------------------------------------------------------------------------


@dataclass
class CardInstance:
    """One physical copy of a card, tracked through the piles."""

    uid: str
    key: str
    owner: str                      # frame id
    location: Location = "deck"
    face_down: bool = True
    resolved: bool = False
    #: Index into `Card.initiative`; a card acts once per entry it still has.
    init_index: int = 0
    #: Turns of persistence left once the card has resolved. None = permanent.
    persist_left: Optional[int] = 0
    #: Echoes of the fallen: set sideways next to another frame's actions,
    #: blocks for it and does nothing else.
    is_echo: bool = False
    #: When this card is a Reload marker, the weapon group awaiting reload.
    reload_for: str = ""


# --------------------------------------------------------------------------
# Tokens and objectives
# --------------------------------------------------------------------------


@dataclass
class TokenState:
    """A board token. Attackable like a frame but never blocks (rules.tex:778)."""

    id: str
    kind: str
    pos: Optional[Pos]
    hp: int = 0
    max_hp: int = 0
    alive: bool = True
    #: Seat that brought the objective this token belongs to, if any.
    owner: Optional[Team] = None
    objective: str = ""
    #: Frame currently carrying it (Shiny Thing).
    carrier: Optional[str] = None

    @property
    def attackable(self) -> bool:
        return self.alive and self.max_hp > 0 and self.pos is not None


@dataclass
class ObjectiveState:
    """One scored objective. `owner` is the defender -- who brought the card."""

    name: str
    owner: Team
    defend: int
    attack: int
    tiles: tuple[Pos, ...] = ()
    token_ids: tuple[str, ...] = ()
    #: Seat that has locked in the score, for latching objectives.
    latched: Optional[Team] = None
    #: Per-objective scratch space (the Egg's consecutive-turn counters, ...).
    memo: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Frames
# --------------------------------------------------------------------------


@dataclass
class FrameState:
    id: str
    seat: Team
    spec: FrameSpec
    pos: Optional[Pos] = None
    alive: bool = True

    damage: dict[str, int] = field(
        default_factory=lambda: {z: 0 for z in ZONES}
    )
    statuses: dict[str, int] = field(
        default_factory=lambda: {k: 0 for k in STATUS_KINDS}
    )
    shields: int = 0

    deck: list[str] = field(default_factory=list)       # index 0 == top
    discard: list[str] = field(default_factory=list)
    hand: list[str] = field(default_factory=list)
    #: This turn's cards -- face-down, revealed and resolved alike. This is
    #: exactly the "remaining cards" set that blocking draws from.
    committed: list[str] = field(default_factory=list)
    #: Persistent cards set aside: they neither resolve nor block.
    aside: list[str] = field(default_factory=list)

    #: Weapon group -> uid of the Reload marker waiting on it.
    reloading: dict[str, str] = field(default_factory=dict)
    #: Cleared every turn; frame abilities and card effects stash flags here.
    turn_flags: dict[str, Any] = field(default_factory=dict)
    #: Deathstrike: the turn at the end of which the frame is removed.
    deathstrike_until: Optional[int] = None
    #: Whether this frame has moved this turn (Sniper Rifle's stillness bonus).
    moved_this_turn: bool = False
    #: Kuwagata's once-per-game planning mulligan.
    mulligan_used: bool = False

    # -- derived stats ---------------------------------------------------

    @property
    def armour(self) -> Mapping[str, int]:
        return self.spec.armour

    def zone_last_hit(self, zone: Zone) -> bool:
        return zone_at_last_hit(self.damage[zone], self.spec.armour[zone])

    @property
    def is_destroyed(self) -> bool:
        return any(
            zone_destroyed(self.damage[z], self.spec.armour[z]) for z in ZONES
        )

    def status_mod(self, debuff: StatusKind, buff: StatusKind) -> int:
        """Net modifier from an opposing status pair, in fixed magnitudes."""
        value = 0
        if self.statuses.get(buff, 0) > 0:
            value += STATUS_MAGNITUDE
        if self.statuses.get(debuff, 0) > 0:
            value -= STATUS_MAGNITUDE
        return value

    @property
    def base_movement(self) -> int:
        move = self.spec.movement + self.status_mod("slowed", "boosted")
        if self.zone_last_hit("Low"):
            move -= 1
        move += int(self.turn_flags.get("movement_bonus", 0))
        return max(0, move)

    @property
    def initiative_mod(self) -> int:
        mod = self.status_mod("stunned", "stimmed")
        if self.zone_last_hit("High"):
            mod -= 1
        return mod

    @property
    def draw_count(self) -> int:
        count = BASE_DRAW + self.status_mod("dazed", "lucid")
        if self.zone_last_hit("Mid"):
            count -= 1
        return max(0, count)


# --------------------------------------------------------------------------
# In-flight resolution bookkeeping
# --------------------------------------------------------------------------


@dataclass
class AttackTarget:
    """One frame or token caught by an attack, with its own block decision."""

    kind: str                                   # "frame" | "token"
    id: str
    #: Zone -> damage, after range filtering and elevation shift.
    zones: dict[str, int] = field(default_factory=dict)
    #: Zones still needing a block decision (Guard Break resolves per zone).
    pending_zones: list[str] = field(default_factory=list)
    #: Zones that have been blocked.
    blocked: list[str] = field(default_factory=list)
    #: Uids already spent blocking this attack (a card blocks once per attack).
    used_blockers: list[str] = field(default_factory=list)
    done: bool = False


@dataclass
class AttackInProgress:
    """An attack that has been declared and is waiting on block decisions.

    Splash text ("Hits all adjacent enemies") adds extra targets; each is
    resolved in turn and gets its own compulsory block check.
    """

    attacker_id: str
    uid: str
    targets: list[AttackTarget] = field(default_factory=list)
    index: int = 0
    guard_break: bool = False
    feint: bool = False
    #: What the log should call the thing swinging, when it is not the frame
    #: itself -- a drone attacks with its summoner's card but is not it.
    via: str = ""

    @property
    def current(self) -> Optional[AttackTarget]:
        if 0 <= self.index < len(self.targets):
            return self.targets[self.index]
        return None


@dataclass
class Resolution:
    """The card currently resolving, and how far through it we are."""

    frame_id: str
    uid: str
    #: Remaining steps, in the controller's chosen order.
    steps: list[str] = field(default_factory=list)
    attack: Optional[AttackInProgress] = None
    #: Scratch for multi-part effects (`effect_choice` round trips).
    effect_state: dict[str, Any] = field(default_factory=dict)
    #: This card was spent reloading its weapon: it moves and does nothing
    #: else, and must not re-arm the Reload it just cleared.
    spent_reloading: bool = False


# --------------------------------------------------------------------------
# The state
# --------------------------------------------------------------------------


@dataclass
class GameState:
    game_id: str
    rng: random.Random
    catalogue: Mapping[str, Card]
    board: Any                                  # BoardProtocol
    frames: dict[str, FrameState] = field(default_factory=dict)
    cards: dict[str, CardInstance] = field(default_factory=dict)
    tokens: dict[str, TokenState] = field(default_factory=dict)
    objectives: list[ObjectiveState] = field(default_factory=list)

    seats: tuple[Team, ...] = (0, 1)
    turn: int = 1
    phase: Phase = "setup"
    priority: Team = 0
    pending: Optional[PendingDecision] = None
    resolution: Optional[Resolution] = None

    #: Frames destroyed, per seat that did the destroying.
    kills: dict[Team, int] = field(default_factory=dict)
    log: list[dict] = field(default_factory=list)
    #: Operator-only log. Never reaches `view_for`; safe for seeds and other
    #: things a player must not see.
    private_log: list[dict] = field(default_factory=list)
    #: Setup/planning bookkeeping the state machine walks through.
    queue: list[Any] = field(default_factory=list)
    #: Initiative-tie bookkeeping: the value being contested and the index into
    #: `seat_cycle()` of the seat that resolves next at that value.
    #: The seed this game was built from, kept for server-side reproducibility
    #: and replay. **Never serialise it.** The deck CSVs ship with the app, so
    #: seed + deck lists lets an observer replay `rng` and reconstruct every
    #: shuffle -- the opponent's deck order and future draws. `view_for`
    #: whitelists the keys it emits, so this is private by construction; the
    #: log is the one wholesale channel, hence `note_private`.
    seed: Optional[int] = None

    #: Per-game secret used to derive the opaque ids `view_for` ships in place
    #: of the uid of a card whose identity is redacted. Deliberately *not*
    #: drawn from `rng`: uids are allocated in deck-file order, so if this were
    #: seed-derived an observer holding the deck CSVs (they ship with the app)
    #: and the seed could rebuild the mapping and read face-down commitments.
    view_salt: str = field(default_factory=lambda: secrets.token_hex(16))
    tie_value: Optional[int] = None
    tie_index: int = 0
    #: Scratch space for card effects (`engine.effects_state.bag`). One
    #: namespaced dict rather than a field per effect, so pilot/drone text can
    #: keep its bookkeeping without spreading through this module. Plain data
    #: only, so `clone()` stays a clean deep copy.
    fx: dict[str, Any] = field(default_factory=dict)
    _uid_counter: int = 0

    # -- purity ----------------------------------------------------------

    def clone(self) -> "GameState":
        """A deep copy that shares the immutable board and card catalogue."""
        memo: dict[int, Any] = {}
        if self.board is not None:
            memo[id(self.board)] = self.board
        memo[id(self.catalogue)] = self.catalogue
        return copy.deepcopy(self, memo)

    # -- ids -------------------------------------------------------------

    def next_uid(self, prefix: str = "c") -> str:
        self._uid_counter += 1
        return f"{prefix}{self._uid_counter}"

    # -- lookups ---------------------------------------------------------

    def card(self, uid: str) -> Card:
        return self.catalogue[self.cards[uid].key]

    def instance(self, uid: str) -> CardInstance:
        return self.cards[uid]

    def frames_of(self, seat: Team, *, alive_only: bool = True) -> list[FrameState]:
        return [
            f for f in self.frames.values()
            if f.seat == seat and (f.alive or not alive_only)
        ]

    def enemies_of(self, seat: Team, *, alive_only: bool = True) -> list[FrameState]:
        return [
            f for f in self.frames.values()
            if f.seat != seat and (f.alive or not alive_only)
        ]

    def frame_at(self, pos: Pos) -> Optional[FrameState]:
        for f in self.frames.values():
            if f.alive and f.pos == pos:
                return f
        return None

    def occupied(self, *, exclude: Optional[str] = None) -> frozenset[Pos]:
        """Tiles blocked by frames -- what `BoardProtocol` wants for movement."""
        out = {
            f.pos for f in self.frames.values()
            if f.alive and f.pos is not None and f.id != exclude
        }
        # An Ephemeral Image counts as occupied too. Two of the three are only
        # illusions, but this set is what movement *and* line of sight are
        # reckoned against -- so if they did not, an enemy could find the frame
        # by noticing which of the three tiles it could not walk into, or which
        # one broke a line of sight. It costs the images a real screening
        # effect, which is the price of the concealment being airtight.
        out |= {
            t.pos for t in self.tokens.values()
            if t.alive and t.pos is not None and t.kind in ("barricade", "image")
        }
        return frozenset(p for p in out if p is not None)

    def elevation(self, pos: Optional[Pos]) -> int:
        if pos is None or self.board is None:
            return 0
        try:
            return self.board.tile(pos).elevation
        except Exception:
            return 0

    # -- logging ---------------------------------------------------------

    def note(self, text: str) -> None:
        """Append to the **public** log.

        `view_for` ships this wholesale to both seats, so anything written
        here is public by construction. Never name a face-down card, a deck's
        contents or the seed. Use `note_private` for those.
        """
        self.log.append({"turn": self.turn, "text": text})

    def note_private(self, text: str) -> None:
        """Append to the operator-only log, which `view_for` never includes."""
        self.private_log.append({"turn": self.turn, "text": text})

    # -- seat order ------------------------------------------------------

    def seat_cycle(self) -> tuple[Team, ...]:
        """Seats in clockwise order starting from the priority marker."""
        seats = tuple(sorted(self.seats))
        if self.priority not in seats:
            return seats
        start = seats.index(self.priority)
        return seats[start:] + seats[:start]

    def rotate_priority(self) -> None:
        """One step anticlockwise at cleanup (rules.tex:604)."""
        seats = tuple(sorted(self.seats))
        idx = seats.index(self.priority) if self.priority in seats else 0
        self.priority = seats[(idx - 1) % len(seats)]


# --------------------------------------------------------------------------
# Deck operations
# --------------------------------------------------------------------------


def reshuffle(state: GameState, frame: FrameState) -> None:
    """Empty deck -> shuffle the discard pile and make it the deck."""
    if not frame.discard:
        return
    pile = list(frame.discard)
    state.rng.shuffle(pile)
    frame.deck = pile
    frame.discard = []
    for uid in frame.deck:
        state.cards[uid].location = "deck"
    state.note(f"{frame.id} reshuffles its discard pile")


def draw(state: GameState, frame: FrameState, count: int) -> list[str]:
    """Draw `count` cards, reshuffling the discard pile when the deck runs out."""
    drawn: list[str] = []
    for _ in range(count):
        if not frame.deck:
            reshuffle(state, frame)
        if not frame.deck:
            break
        uid = frame.deck.pop(0)
        inst = state.cards[uid]
        inst.location = "hand"
        inst.face_down = True
        inst.resolved = False
        inst.init_index = 0
        frame.hand.append(uid)
        drawn.append(uid)
    return drawn


def move_card(state: GameState, uid: str, dest: Location) -> None:
    """Move a card between piles, keeping both ends consistent.

    Every pile is searched, not just the owner's: an Echo of the fallen sits
    in a *surviving* frame's committed row while still belonging to the dead
    frame's deck, and must go back to that frame's discard pile.
    """
    inst = state.cards[uid]
    frame = state.frames[inst.owner]
    for other in state.frames.values():
        for pile in (other.deck, other.hand, other.committed,
                     other.discard, other.aside):
            if uid in pile:
                pile.remove(uid)
    inst.location = dest
    if dest == "deck":
        frame.deck.insert(0, uid)
    elif dest == "hand":
        frame.hand.append(uid)
    elif dest == "committed":
        frame.committed.append(uid)
    elif dest == "aside":
        frame.aside.append(uid)
    else:
        inst.face_down = False
        frame.discard.append(uid)


def discard_card(state: GameState, uid: str) -> None:
    inst = state.cards[uid]
    if inst.is_echo:
        # An echo belongs to a dead frame's deck; it goes to that discard pile.
        move_card(state, uid, "discard")
        inst.is_echo = False
        return
    move_card(state, uid, "discard")


# --------------------------------------------------------------------------
# Statuses
# --------------------------------------------------------------------------


def apply_status(
    state: GameState, frame: FrameState, kind: StatusKind, count: int = 1
) -> None:
    """Add `count` counters, annihilating against the opposite status first."""
    if count <= 0 or not frame.alive:
        return
    opposite = STATUS_OPPOSITES.get(kind)
    if opposite:
        cancelled = min(count, frame.statuses.get(opposite, 0))
        if cancelled:
            frame.statuses[opposite] -= cancelled
            count -= cancelled
    if count:
        frame.statuses[kind] = frame.statuses.get(kind, 0) + count
    state.note(f"{frame.id} gets {kind}")


def tick_statuses(frame: FrameState) -> None:
    """Remove one counter of each type -- end of planning (rules.tex:391)."""
    for kind in STATUS_KINDS:
        if frame.statuses.get(kind, 0) > 0:
            frame.statuses[kind] -= 1


# --------------------------------------------------------------------------
# Damage, destruction and repair
# --------------------------------------------------------------------------


def deal_damage(
    state: GameState,
    frame: FrameState,
    zone: Zone,
    amount: int,
    *,
    source: Optional[FrameState] = None,
    absorb: bool = True,
) -> int:
    """Apply damage to one zone. Returns the damage actually taken.

    A shield counter absorbs the whole instance regardless of size
    (rules.tex `Shield (X)`), so it returns 0 in that case.

    `absorb=False` skips the shield check, for callers that have already
    resolved the shield once for a whole multi-zone attack. Use
    `deal_attack_damage` rather than passing this by hand.
    """
    if amount <= 0 or not frame.alive:
        return 0
    if absorb and frame.shields > 0:
        frame.shields -= 1
        state.note(f"{frame.id} loses a shield counter instead of damage")
        return 0
    frame.damage[zone] = frame.damage.get(zone, 0) + amount
    state.note(f"{frame.id} takes {amount} {zone} damage")
    # "Frames [...] drop it on damage" -- any damage, from any source.
    from . import objectives as _objectives

    _objectives.on_damage(state, frame, source)
    check_destruction(state, frame, killer=source)
    return amount


def deal_attack_damage(
    state: GameState,
    frame: FrameState,
    zones: Mapping[str, int],
    *,
    source: Optional[FrameState] = None,
) -> int:
    """Apply one attack's damage across every zone it landed in.

    A shield counter cancels the **attack**, not the zone: one counter takes
    the full brunt however many zones landed. That matters most under Guard
    Break, where a single attack lands in up to three zones at once -- it
    still costs the defender exactly one counter, not one per zone.
    """
    total = sum(amount for amount in zones.values() if amount > 0)
    if total <= 0 or not frame.alive:
        return 0
    if frame.shields > 0:
        frame.shields -= 1
        state.note(
            f"{frame.id} loses a shield counter instead of the whole attack"
        )
        return 0
    dealt = 0
    for zone in ZONES:
        amount = zones.get(zone, 0)
        if amount > 0:
            dealt += deal_damage(
                state, frame, zone, amount, source=source, absorb=False
            )
    return dealt


def damage_token(
    state: GameState, token: TokenState, amount: int
) -> int:
    if amount <= 0 or not token.alive:
        return 0
    token.hp -= amount
    if token.hp <= 0:
        token.hp = 0
        token.alive = False
        state.note(f"{token.kind} token destroyed")
    return amount


def add_shield(state: GameState, frame: FrameState, amount: int = 1) -> None:
    """Shield(X): a frame may hold at most one more than its initial value."""
    cap = frame.spec.shield + 1
    frame.shields = min(cap, frame.shields + amount)


def repair(state: GameState, frame: FrameState, amount: int) -> None:
    """Remove `amount` damage, spread from the most-damaged zone down."""
    left = amount
    while left > 0:
        worst = max(ZONES, key=lambda z: frame.damage[z])
        if frame.damage[worst] <= 0:
            break
        frame.damage[worst] -= 1
        left -= 1
    if amount != left:
        state.note(f"{frame.id} repairs {amount - left}")
    if frame.deathstrike_until is not None and not frame.is_destroyed:
        frame.deathstrike_until = None
        state.note(f"{frame.id} is repaired back out of Deathstrike")


def check_destruction(
    state: GameState, frame: FrameState, *, killer: Optional[FrameState] = None
) -> bool:
    """Destroy the frame if any zone is over its armour. Honours Deathstrike."""
    if not frame.alive or not frame.is_destroyed:
        return False
    if "deathstrike" in frame.spec.keywords and frame.deathstrike_until is None:
        # Fights on until the end of the *next* turn (rules.tex Frame Keywords).
        frame.deathstrike_until = state.turn + 1
        state.note(f"{frame.id} would be destroyed -- Deathstrike holds it")
        return False
    if frame.deathstrike_until is not None:
        return False
    destroy_frame(state, frame, killer=killer)
    return True


def destroy_frame(
    state: GameState, frame: FrameState, *, killer: Optional[FrameState] = None
) -> None:
    frame.alive = False
    frame.pos = None
    frame.deathstrike_until = None
    for uid in list(frame.committed) + list(frame.hand) + list(frame.aside):
        move_card(state, uid, "discard")
    for token in state.tokens.values():
        if token.carrier == frame.id:
            token.carrier = None
    scorer = killer.seat if killer is not None else None
    if scorer is None:
        for seat in state.seats:
            if seat != frame.seat:
                scorer = seat
                break
    if scorer is not None and scorer != frame.seat:
        state.kills[scorer] = state.kills.get(scorer, 0) + 1
    state.note(f"{frame.id} is destroyed")


# --------------------------------------------------------------------------
# Victory points
# --------------------------------------------------------------------------


def victory_points(state: GameState) -> dict[Team, int]:
    """1 per enemy frame defeated + the value of each objective scored."""
    from . import objectives as _objectives

    points = {seat: state.kills.get(seat, 0) for seat in state.seats}
    for objective in state.objectives:
        seat, value = _objectives.objective_score(state, objective)
        if seat is not None:
            points[seat] = points.get(seat, 0) + value
    return points
