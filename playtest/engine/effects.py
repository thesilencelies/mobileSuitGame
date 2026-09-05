"""Card-text effect registry.

Everything a card's `Text` column asks for that is not a keyword lands here:
the weapon/basic/booster/frame text, the `On Hit:` / `On Block:` riders, and
the pilot and drone cards.

Three shapes of effect, and each has a different hook:

* **An effect step.** `EFFECT_STEPS[key]` runs during the card's effect step
  and may return a `PendingDecision` when the controller has a choice to make.
  The answer comes back to `apply_effect_choice`, which may park another
  decision by setting `state.pending` -- that is how "put up to 3 barricades"
  asks three times.
* **A passive query.** Persistent cards (Fog of war, Master duelist, Net
  Strength ...) do nothing at their own effect step; the attack pipeline
  *asks* whether they are up, via the small hook functions near the bottom of
  this module. Nothing pushes a flag that could go stale: `card_active` looks
  for the physical card in the frame's `aside` pile, which the cleanup phase
  already maintains against the printed Persistence value.
* **A follow-up.** Things that happen outside a card's own resolution --
  a drone's turn, Teleport's initiative-4 reposition, Ace Reflexes' move after
  being hit -- are offered by `followup_decision`, which the driver calls
  whenever it is between cards.

Anything a card asks for that is not recognised lands in `DeferredEffect`, so
nothing is ever quietly dropped: the UI flags it and the AI refuses to price
it in.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

from . import effects_state as fx
from .state import (
    AttackInProgress,
    AttackTarget,
    FrameState,
    GameState,
    TokenState,
    add_shield,
    apply_status,
    damage_token,
    deal_damage,
    discard_card,
    draw,
    move_card,
    record_movement,
    repair,
)
from .types import (
    ACTIONS_PER_TURN,
    Card,
    PendingDecision,
    Pos,
    StatusKind,
    Team,
    ZONES,
)

# --------------------------------------------------------------------------
# Text patterns
# --------------------------------------------------------------------------

_STATUSES = ("stunned", "stimmed", "dazed", "lucid", "slowed", "boosted", "revealed")

_ON_HIT_RE = re.compile(r"on hit:(.*?)(?:\\\\|$)", re.I | re.S)
_ON_BLOCK_RE = re.compile(r"on block:(.*?)(?:\\\\|$)", re.I | re.S)
_STATUS_RE = re.compile(
    r"(\d+)?\s*\\?(?:full|small)?(" + "|".join(_STATUSES) + r")\b", re.I
)
_REPAIR_RE = re.compile(r"repair\s+(\d+)", re.I)
_MV_BONUS_RE = re.compile(r"other actions this turn get\s*\+?(\d+)\s*mv", re.I)
_RANGE_PENALTY_RE = re.compile(r"ranged attacks target at\s*-(\d+)\s*range", re.I)
_DIDNT_MOVE_RE = re.compile(r"deals\s*\+\s*\\?\w*\{?(\d+)\}?.*did\s*n.?t move", re.I)
_SELECT_MOVE_RE = re.compile(
    r"select an opposing frame within\s*(\d+)\s*:\s*move them\s*(\d+)", re.I
)
_WITHIN_RE = re.compile(r"within\s*(\d+)", re.I)
#: "Summon two attack dogs" -- how many of the thing the card makes. Spelled
#: out on every card that has one, so the words are part of the pattern.
_COUNT_RE = re.compile(r"\b(?:summon|create|put)\s+(?:up to\s+)?(\d+|one|two|three)\b", re.I)
_COUNT_WORDS = {"one": 1, "two": 2, "three": 3}

#: Keywords the attack pipeline handles, in any of their printed spellings
#: (`\kw`, `\fullkw`, `\smallkw` -- the backslashes are stripped first).
_KEYWORD_WORDS = (
    "feint", "guardbreak", "guard break", "committed", "closequarters",
    "close quarters", "knockback", "reload", "dronetext",
)

#: Whole phrases handled elsewhere in the engine rather than at the effect
#: step: splash targeting, the initiative list, deck-construction locks.
_HANDLED_PHRASES = (
    re.compile(r"(also )?hits all adjacent enemies", re.I),
    re.compile(r"also hits any enemies adjacent to the target", re.I),
    re.compile(r"hits all targets in range", re.I),
    re.compile(r"must (attack|move) before (attack|mov)\w*", re.I),
    re.compile(r"acts twice[^\\]*", re.I),
    re.compile(r"can only be used by[^\\]*", re.I),
)


def _parse_statuses(text: str) -> list[tuple[StatusKind, int]]:
    out: list[tuple[StatusKind, int]] = []
    for count, kind in _STATUS_RE.findall(text or ""):
        out.append((kind.lower(), int(count) if count else 1))
    return out


def _reach_from_text(text: str, default: int) -> int:
    match = _WITHIN_RE.search(text or "")
    return int(match.group(1)) if match else default


def _reaches_from_text(text: str, *defaults: int) -> tuple[int, ...]:
    """Every "within N" the card prints, in order, padded with `defaults`.

    Two cards name two different distances in one sentence -- Displace picks a
    frame "within 5" and puts it down "within 8", Set the trap moves an ally
    "within 5" and reveals enemies "within 3" -- so reading only the first is
    reading half the card.
    """
    found = [int(n) for n in _WITHIN_RE.findall(text or "")]
    return tuple(
        found[i] if i < len(found) else default
        for i, default in enumerate(defaults)
    )


def _count_from_text(text: str, default: int) -> int:
    """How many the card makes: "Summon two attack dogs" -> 2."""
    match = _COUNT_RE.search(text or "")
    if match is None:
        return default
    word = match.group(1).lower()
    return _COUNT_WORDS.get(word, int(word) if word.isdigit() else default)


# --------------------------------------------------------------------------
# Card keys
# --------------------------------------------------------------------------

ACCELERATE = "Booster_Accelerate"
JUMP = "Booster_Jump"
BOOMERANG = "Booster_Boomerang"
EXPLOSIVE_EXIT = "Booster_Explosive Exit"

RELENTLESS = "Bruiser_Relentless Assault"
INTIMIDATE = "Bruiser_Intimidate"
NET_STRENGTH = "Bruiser_Net Strength"
LOCKDOWN = "Bruiser_Lockdown"
BIND = "Bruiser_Bind"
SUPLEX = "Bruiser_Suplex"

EPHEMERAL = "Mystic_Ephemeral Images"
TELEPORT = "Mystic_Teleport"
UTTER_DARKNESS = "Mystic_Utter darkness"
ENCODE = "Mystic_Encode the future"
PSYCHIC_STORM = "Mystic_Psychic Storm"
DOOM = "Mystic_Doom"

BROADCAST = "Tactician_Tactical broadcast"
FOG_OF_WAR = "Tactician_Fog of war"
SET_THE_TRAP = "Tactician_Set the trap"
OUTFOX = "Tactician_Outfox"
DISPLACE = "Tactician_Displace"
ENNERVATE = "Tactician_Ennervate"

HYPER = "Wunderkid_Hyper"
NET_SPEED = "Wunderkid_Net Speed"
PORTAL = "Wunderkid_Portal"
ACE_REFLEXES = "Wunderkid_Ace Reflexes"
PARALLEL_ACTION = "Wunderkid_Parallel Action"
SHOWBOATING = "Wunderkid_Showboating"

REPAIRS = "Engineer_Battlefield Repairs"
BARRICADE = "Engineer_Barricade"
GRAVITY_WELL = "Engineer_Gravity Well"
PRECISION_TUNING = "Engineer_Precision Tuning"
SYSTEM_OVERRIDE = "Engineer_System Override"
SENSORY_OVERLOAD = "Engineer_Sensory Overload"

COMBO_STRIKE = "Specialist_Combo strike"
SNIPERS_AIM = "Specialist_Snipers aim"
MASTER_DUELIST = "Specialist_Master duelist"
PRACTICED = "Specialist_Practiced Technique"
REBOUND = "Specialist_Rebound"
CAGE_FIGHT = "Specialist_Cage Fight"

#: Gravity Well's radius and per-step cost (from the card text).
GRAVITY_RADIUS = 5
GRAVITY_PENALTY = 1

#: Barricade tokens per card, and Utter darkness' radius.
BARRICADE_COUNT = 3
DARKNESS_RADIUS = 5

#: Psychic Storm: how far the weather reaches and what it does to everything
#: standing in it, once per turn.
STORM_RADIUS = 5
STORM_DAMAGE = 1
STORM_ZONE = "High"

#: Doom: how far a marked frame has to run to shake it off, and what it takes
#: at the end of the next turn if it does not.
DOOM_ESCAPE = 3
DOOM_DAMAGE = 3
DOOM_ZONE = "High"

#: Rebound: how far the mirror sees for the frame that put it down.
REBOUND_RADIUS = 4

#: Cage Fight: the box is 5x5, so its walls are the ring at exactly this
#: distance from the centre and both fighters stand inside the 3x3 it encloses.
CAGE_RADIUS = 2

#: Sensory Overload: what a jammed frame's ranged attacks can still reach.
OVERLOAD_RANGE_CAP = 2

#: Boomerang: how far from the anchor to look for somewhere to stand when
#: something has parked on the tile the frame was snapping back to.
BOOMERANG_SEARCH = 3

#: Cards whose attack is made by a summoned token, not by the frame that
#: played them (rules.tex "Drones": the drone takes the action on the card).
_DELEGATED_ATTACK = frozenset({"drone"})


# --------------------------------------------------------------------------
# Deferred effects
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DeferredEffect:
    """A card effect the engine does not implement.

    The engine raises nothing and does nothing, but records the fact in the
    log and exposes it through `view_for` so the client can show "this card's
    text is not implemented yet" rather than pretending it worked.
    """

    key: str
    text: str

    @property
    def reason(self) -> str:
        return f"{self.key}: card text not implemented -- {self.text}"


def _residual_text(text: str) -> str:
    """What is left of a Text column once everything the engine already
    handles outside the effect step has been struck out."""
    stripped = (text or "").lower()
    stripped = re.sub(r"\{[^}]*\}", " ", stripped)      # \knockback{2} -> the X
    stripped = re.sub(r"\\+", " ", stripped)            # drop LaTeX backslashes
    for pattern in _HANDLED_PHRASES:
        stripped = pattern.sub(" ", stripped)
    for word in _KEYWORD_WORDS:
        # `\fullfeint` and `\smallcut` arrive here as "fullfeint"/"smallcut".
        stripped = re.sub(rf"\b(full|small)?{re.escape(word)}\b", " ", stripped)
    return stripped


def is_keyword_only(card: Card) -> bool:
    """True when the whole Text column is things handled outside the effect step."""
    return not re.search(r"[a-z]", _residual_text(card.text))


def effect_kind(card: Card) -> str:
    """`"none"`, `"handled"` or `"deferred"` for this card's effect step."""
    if _effect_handler(card) is not None:
        return "handled"
    text = (card.text or "").lower()
    if not text.strip():
        return "none"
    if _DIDNT_MOVE_RE.search(text):
        return "none"          # applied as an attack damage bonus
    if _ON_HIT_RE.search(text) or _ON_BLOCK_RE.search(text):
        # Riders, applied by the attack pipeline, not an effect step of their
        # own -- unless there is other text alongside them.
        remainder = _ON_HIT_RE.sub(" ", _ON_BLOCK_RE.sub(" ", text))
        return "none" if not re.search(r"[a-z]", _residual_text(remainder)) else "deferred"
    return "none" if is_keyword_only(card) else "deferred"


def deferred_effects(catalogue: Mapping[str, Card]) -> dict[str, DeferredEffect]:
    """Every card whose text the engine does not implement."""
    return {
        key: DeferredEffect(key, card.text)
        for key, card in catalogue.items()
        if effect_kind(card) == "deferred"
    }


#: "Must attack before moving" (Explosive Exit), read generically so a card
#: printing the mirror constraint needs no engine change. `move` maps to the
#: `movement` step; anything else is named as the step is.
_STEP_ORDER_RE = re.compile(r"must (attack|move) before (attack|mov)\w*", re.I)

#: "get + 4 range" / "deal 1 extra damage" -- Snipers aim's two numbers, read
#: off the card so a balance pass in `Pilot actions.csv` needs no engine change.
_EXTRA_RANGE_RE = re.compile(r"\+\s*(\d+)\s*range", re.I)
_EXTRA_DAMAGE_RE = re.compile(r"deals?\s+(\d+)\s+extra\s+damage", re.I)
_STEP_NAMES = {"attack": "attack", "move": "movement", "mov": "movement"}

#: Cards whose effect step has to be in force before the frame moves, which
#: the card implies rather than prints. Jump changes what a step up costs and
#: says "all movement this turn"; Boomerang has to note where the action
#: started before the action carries the frame away from it.
_EFFECT_BEFORE_MOVE = frozenset({JUMP, BOOMERANG})


def step_orders(card: Card, steps: Sequence[str]) -> list[list[str]]:
    """Every order of a card's resolution steps that the card allows.

    The controller normally picks the order (rules.tex: an action's movement,
    effect and attack may be taken in any order). A card that says otherwise
    narrows the list, and when only one order survives there is nothing left
    to ask.
    """
    rules: list[tuple[str, str]] = []
    for first, second in _STEP_ORDER_RE.findall(card.text or ""):
        before = _STEP_NAMES.get(first.lower())
        after = _STEP_NAMES.get(second.lower())
        if before and after and before != after:
            rules.append((before, after))
    if card.key in _EFFECT_BEFORE_MOVE:
        rules.append(("effect", "movement"))

    allowed = []
    for perm in itertools.permutations(steps):
        order = list(perm)
        if all(
            before not in order or after not in order
            or order.index(before) < order.index(after)
            for before, after in rules
        ):
            allowed.append(order)
    return allowed


def has_effect_step(
    card: Card,
    state: Optional[GameState] = None,
    frame: Optional[FrameState] = None,
) -> bool:
    """Does this card need an effect step in its resolution?

    `state`/`frame` are optional: a card with no text of its own still needs
    one when the frame is holding an effect that fires on its *next* action
    (Combo strike), so the resolution has somewhere to ask the question.
    """
    if effect_kind(card) in ("handled", "deferred"):
        return True
    if state is not None and frame is not None:
        return _armed_rider(state, frame, card) is not None
    return False


def delegates_attack(card: Card) -> bool:
    """True when the card's printed attack is made by a token it summons.

    A drone card's attack/block numbers are the *drone's*, so the frame that
    plays it must not swing with them -- it summons, and the drone attacks on
    its own turn (rules.tex:826).
    """
    return card.card_type in _DELEGATED_ATTACK


# --------------------------------------------------------------------------
# Decision plumbing
# --------------------------------------------------------------------------

EffectFn = Callable[[GameState, FrameState, str], Optional[PendingDecision]]

#: `handler(state, frame, choice, ctx)`. `frame` is the frame the decision was
#: *about* (which is not always the frame whose card is resolving -- Intimidate
#: asks each victim), and `ctx` is whatever the asking side stashed.
ChoiceFn = Callable[[GameState, FrameState, Mapping[str, Any], Mapping[str, Any]], None]


def _ask(
    state: GameState,
    handler: str,
    *,
    seat: Team,
    prompt: str,
    options: Sequence[Mapping[str, Any]],
    frame_id: str,
    ctx: Optional[Mapping[str, Any]] = None,
    pick_min: int = 1,
    pick_max: int = 1,
    pick_kind: str = "",
) -> PendingDecision:
    """Build an `effect_choice` and record which handler answers it.

    `pick_min`/`pick_max` are how many answers this effect still wants in
    total, not how many this one decision takes -- the engine always asks for
    one at a time. An effect that places three barricades says three here, and
    the client uses it to let the player mark all three on the board before
    committing any of them, instead of stopping to confirm after each.
    """
    fx.bag(state)["await"] = {"handler": handler, "ctx": dict(ctx or {})}
    return PendingDecision(
        kind="effect_choice",
        seat=seat,
        prompt=prompt,
        options=[dict(option) for option in options],
        frame_id=frame_id,
        pick_min=pick_min,
        pick_max=pick_max,
        pick_kind=pick_kind,
    )


def _frame_options(
    state: GameState, frames: Sequence[FrameState]
) -> list[dict[str, Any]]:
    """The frames a card may pick, as options -- images and all.

    "Each image should be treated as a frame in itself for interactions and
    targeting", so a frame behind Ephemeral Images is not offered as itself:
    each of its images is an option of its own, indistinguishable from the
    others. Whatever the card does then lands on the frame (`_target_frame`),
    which is what makes a debuff aimed at a decoy still stick.

    Offered the same way to both sides. The owner knows which of the three it
    is standing on and the enemy does not, but that is a fact about the view,
    not about what may be aimed at.
    """
    out: list[dict[str, Any]] = []
    for other in frames:
        images = image_tokens(state, other)
        if not images:
            out.append({"frame": other.id, "name": other.spec.name})
            continue
        for token_id in images:
            token = state.tokens.get(token_id)
            if token is not None and token.alive and token.pos is not None:
                out.append({"token": token_id, "name": "an image"})
    return out


def _target_frame(state: GameState, choice: Mapping) -> Optional[FrameState]:
    """The frame an option picked -- directly, or through one of its images."""
    token_id = choice.get("token")
    if token_id:
        token = state.tokens.get(str(token_id))
        found = image_owner(state, token) if token is not None else None
        return found[0] if found is not None else None
    return state.frames.get(str(choice.get("frame")))


# --------------------------------------------------------------------------
# Status helpers
# --------------------------------------------------------------------------


def reveal_committed(state: GameState, frame: FrameState) -> None:
    """`Revealed`: "chosen actions are turned face up" (generateCards.py:235).

    Turning the physical card face up is the whole effect, and it is also how
    `view_for` decides what a seat may see -- so the status is applied by
    flipping the cards rather than by a second rule in the serializer.
    """
    for uid in frame.committed:
        inst = state.cards.get(uid)
        if inst is not None and inst.location == "committed" and inst.face_down:
            inst.face_down = False


def _apply_statuses(
    state: GameState, target: FrameState, pairs: Sequence[tuple[StatusKind, int]]
) -> None:
    for kind, count in pairs:
        apply_status(state, target, kind, count)
        if kind == "revealed" and target.statuses.get("revealed", 0) > 0:
            reveal_committed(state, target)


def _refresh_revealed(state: GameState) -> None:
    """Keep `revealed` frames face up as they commit new cards each turn."""
    for frame in state.frames.values():
        if frame.alive and frame.statuses.get("revealed", 0) > 0:
            reveal_committed(state, frame)


# --------------------------------------------------------------------------
# Implemented effect steps -- weapons, basics, boosters, frames
# --------------------------------------------------------------------------


def _effect_dodge(state: GameState, frame: FrameState, uid: str):
    card = state.card(uid)
    match = _RANGE_PENALTY_RE.search(card.text)
    penalty = int(match.group(1)) if match else 0
    frame.turn_flags["range_penalty_against"] = max(
        int(frame.turn_flags.get("range_penalty_against", 0)), penalty
    )
    state.note(f"{frame.id} dodges: ranged attacks target it at -{penalty}")
    return None


def _effect_repair(state: GameState, frame: FrameState, uid: str):
    card = state.card(uid)
    match = _REPAIR_RE.search(card.text)
    repair(state, frame, int(match.group(1)) if match else 0)
    return None


def _effect_shield(state: GameState, frame: FrameState, uid: str):
    add_shield(state, frame, 1)
    state.note(f"{frame.id} gains a shield counter")
    return None


def _effect_accelerate(state: GameState, frame: FrameState, uid: str):
    card = state.card(uid)
    match = _MV_BONUS_RE.search(card.text)
    bonus = int(match.group(1)) if match else 0
    _owe_movement(state, frame, bonus)
    state.note(f"{frame.id}'s other actions this turn get +{bonus} movement")
    return None


def _effect_jump(state: GameState, frame: FrameState, uid: str):
    """"All movement this turn ignores elevation penalties" -- Jump.

    Its own movement included, which is why `step_orders` puts the effect step
    of this card before its movement step: a boost that skipped the move it
    came with would be reading "all movement this turn" as "some of it".
    """
    frame.turn_flags["climb_free"] = True
    state.note(f"{frame.id} boosts: its movement this turn ignores elevation")
    return None


def _effect_boomerang(state: GameState, frame: FrameState, uid: str):
    """"At the start of next turn: return this frame to the position they
    started this action at".

    The position is noted here rather than when the return fires, and
    `step_orders` runs this step first, so "where the action started" is
    genuinely where the frame stood before the card moved it.
    """
    if frame.pos is None:
        return None
    fx.slot(state, "boomerang")[frame.id] = {
        "turn": state.turn + 1, "x": frame.pos.x, "y": frame.pos.y
    }
    state.note(
        f"{frame.id} anchors at ({frame.pos.x},{frame.pos.y}) "
        f"and snaps back to it at the start of next turn"
    )
    return None


def _boomerang_step(state: GameState) -> None:
    """Start of turn: pull back everything anchored for this turn."""
    anchored = fx.slot(state, "boomerang")
    for frame_id in [
        k for k, rec in anchored.items() if int(rec.get("turn", 0)) == state.turn
    ]:
        record = anchored.pop(frame_id)
        frame = state.frames.get(frame_id)
        if frame is None or not frame.alive or frame.pos is None:
            continue
        home = Pos(int(record["x"]), int(record["y"]))
        dest = _landing_tile(state, frame, home)
        if dest is None or dest == frame.pos:
            continue
        state.note(f"{frame.id} snaps back to ({dest.x},{dest.y})")
        record_movement(state, frame, frame.pos, dest)
        frame.pos = dest


def _landing_tile(
    state: GameState, frame: FrameState, home: Pos
) -> Optional[Pos]:
    """`home` if the frame can stand there, else the nearest tile it can.

    Nothing says what happens when someone is parked on the spot you were
    going to snap back to. Taking the nearest free tile keeps the card doing
    what it says -- putting the frame back where it came from -- without
    either deleting the effect or stacking two frames on a tile.
    """
    board = state.board
    if board is None:
        return None
    taken = state.unit_tiles(exclude=frame.id)
    flying = "flying" in frame.spec.keywords

    def usable(pos: Pos) -> bool:
        if not board.in_bounds(pos) or pos in taken:
            return False
        tile = board.tile(pos)
        return not tile.impassable and (flying or not tile.obstacle)

    if usable(home):
        return home
    for radius in range(1, BOOMERANG_SEARCH + 1):
        ring = [
            Pos(home.x + dx, home.y + dy)
            for dx in range(-radius, radius + 1)
            for dy in range(-radius, radius + 1)
            if max(abs(dx), abs(dy)) == radius
        ]
        spots = [p for p in ring if usable(p)]
        if spots:
            return min(spots, key=lambda p: (board.distance(p, home), p.y, p.x))
    return None


def ignores_elevation(state: GameState, frame: FrameState) -> bool:
    """Jump: "All movement this turn ignores elevation penalties"."""
    return bool(frame.turn_flags.get("climb_free"))


def start_of_turn(state: GameState) -> None:
    """Effects that fire as a turn begins, after the turn flags are cleared."""
    _boomerang_step(state)


def _owe_movement(state: GameState, frame: FrameState, bonus: int) -> None:
    """Bank a movement modifier for the frame's *other* actions.

    "Other actions this turn get +3 mv" (Accelerate), "all other actions this
    frame takes this turn resolve twice at -2mv" (Relentless Assault). Both
    say *other*, and the card saying so is itself an action with a movement
    step -- which the controller may order after the effect step. So the
    modifier is banked here and only joins `movement_bonus` when the card that
    granted it has finished (`after_card_resolved`), which is the one moment
    "other" is unambiguous.
    """
    if bonus:
        frame.turn_flags["movement_owed"] = (
            int(frame.turn_flags.get("movement_owed", 0)) + bonus
        )


def _effect_call_of_nature(state: GameState, frame: FrameState, uid: str):
    """"Select an opposing frame within 6: move them 2".

    The rules do not say who chooses the direction; the controller does, so
    this is offered as a single `effect_choice` over (frame, destination).
    """
    card = state.card(uid)
    match = _SELECT_MOVE_RE.search(card.text)
    reach = int(match.group(1)) if match else 6
    steps = int(match.group(2)) if match else 2
    return _shove_step(
        state, frame, label=card.name, reach=reach, steps=steps,
        side="enemy", after="",
    )


def _shove_victims(
    state: GameState, frame: FrameState, reach: int, steps: int, *,
    side: str, origin: Optional[Pos] = None,
) -> list[FrameState]:
    """Frames "move a frame within N up to M" could actually move."""
    if state.board is None or frame.pos is None:
        return []
    return [
        other for other in fx.frames_within(state, frame, reach, side=side)
        if other.pos is not None
        and _shove_tiles(state, other, steps, origin=origin)
    ]


def _shove_tiles(
    state: GameState,
    target: FrameState,
    steps: int,
    *,
    origin: Optional[Pos] = None,
    away_from: Optional[Pos] = None,
    at: Optional[Pos] = None,
) -> list[dict[str, Any]]:
    """Where a moved frame can end up, with the steps it costs to get there.

    Two kinds of card move a frame that is not their own. Most of them *walk*
    it -- "move an allied frame within 5 2 space" -- so the destination is
    whatever the frame could reach on its own legs, and `cost` is that walk.
    `origin` switches to the other kind: Displace and Suplex *place* the frame
    rather than walking it, anywhere free within `steps` of the tile named --
    which is the caster's, not the target's -- so the wall between them is no
    obstacle and the cost is meaningless (a placement is not a walk, and the
    board draws these as an orange placement rather than a green move).

    `away_from` keeps only the tiles on the far side of `origin` from it: the
    "other side of this frame" a Suplex throws its target to.
    """
    here = at if at is not None else target.pos
    if state.board is None or here is None:
        return []
    if origin is not None:
        tiles = fx.free_tiles(state, origin, steps)
        if away_from is not None:
            # The far side, by the sign of the displacement: a tile counts as
            # "the other side of" the thrower when it is not in the direction
            # the target came from. Zero on an axis is neither side, so a
            # sideways throw is allowed; the same direction is not.
            back = (away_from.x - origin.x, away_from.y - origin.y)
            tiles = [
                p for p in tiles
                if (p.x - origin.x) * back[0] + (p.y - origin.y) * back[1] <= 0
            ]
        return [{"x": p.x, "y": p.y} for p in tiles if p != here]
    walker = target if here == target.pos else _StandsAt(target, here)
    return [
        option for option in state.walk_options(
            walker, steps, flying="flying" in target.spec.keywords
        )
        if Pos(option["x"], option["y"]) != here
    ]


@dataclass
class _StandsAt:
    """A stand-in that walks a frame's legs from somewhere else.

    An Ephemeral Image being shoved is not the frame, but it moves like it, so
    `walk_options` is asked about the frame from the image's tile.
    """

    frame: FrameState
    pos: Pos

    @property
    def id(self) -> str:
        return self.frame.id

    @property
    def seat(self):
        return self.frame.seat


def _shove_step(
    state: GameState,
    frame: FrameState,
    *,
    label: str,
    reach: int,
    steps: int,
    side: str,
    after: str,
    place: bool = False,
    away: bool = False,
) -> Optional[PendingDecision]:
    """"Move a frame within N up to M", asked as two plain questions.

    Who, then where. It used to be one list of every (frame, destination) pair,
    which on a 15x16 board is dozens of rows of raw coordinates and no way to
    see any of it on the map. Split, each half is something the board can show:
    the frames you may shove, then that frame's own reachable tiles in the same
    green a move uses.

    `place` means the destinations are measured from the caster rather than
    walked by the target (Displace, Suplex); `away` additionally keeps only the
    far side of the caster (Suplex's throw).
    """
    origin = frame.pos if place else None
    victims = _shove_victims(state, frame, reach, steps, side=side, origin=origin)
    if not victims:
        return None
    # Counted in *options*, not victims: a frame behind Ephemeral Images is
    # three of them, and which image is shoved decides what actually moves.
    options = _frame_options(state, victims)
    if len(options) == 1:
        picked = _target_frame(state, options[0])
        if picked is None:
            return None
        return _shove_destination(
            state, frame, picked, label=label, steps=steps, after=after,
            place=place, away=away, via=str(options[0].get("token", "")),
        )
    return _ask(
        state,
        "shove_frame",
        seat=frame.seat,
        frame_id=frame.id,
        prompt=f"{label}: which frame within {reach}?",
        options=options,
        ctx={"steps": steps, "label": label, "after": after,
             "place": place, "away": away},
        pick_kind="frame",
    )


def _shove_destination(
    state: GameState, frame: FrameState, target: FrameState, *,
    label: str, steps: int, after: str, place: bool = False,
    away: bool = False, via: str = "",
) -> Optional[PendingDecision]:
    """Where the thing picked ends up.

    `via` is the image that was picked, when the target was behind Ephemeral
    Images. A fake is moved on its own -- "each image can be individually moved
    using say Displace" -- and the real one takes the frame with it, so the
    distances are measured from wherever that image is standing.
    """
    at = _image_pos(state, via) or target.pos
    tiles = _shove_tiles(
        state, target, steps,
        origin=frame.pos if place else None,
        away_from=at if (place and away) else None,
        at=at,
    )
    if not tiles:
        return None
    what = "an image" if _is_fake(state, via) else target.id
    return _ask(
        state,
        "shove_to",
        seat=frame.seat,
        frame_id=frame.id,
        prompt=(
            f"{label}: put {what} within {steps}" if place
            else f"{label}: move {what} up to {steps}"
        ),
        options=tiles,
        ctx={"target": target.id, "after": after, "via": via},
        pick_kind="place" if place else "move",
    )


def _image_pos(state: GameState, token_id: str) -> Optional[Pos]:
    token = state.tokens.get(token_id) if token_id else None
    return token.pos if token is not None and token.alive else None


def _is_fake(state: GameState, token_id: str) -> bool:
    """True when `token_id` is one of the decoys rather than the real image."""
    token = state.tokens.get(token_id) if token_id else None
    found = image_owner(state, token) if token is not None else None
    return found is not None and not found[1]


def _choice_shove_frame(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    target = _target_frame(state, choice)
    if target is None:
        return
    nxt = _shove_destination(
        state, frame, target,
        label=str(ctx.get("label", "Move")),
        steps=int(ctx.get("steps", 2)),
        after=str(ctx.get("after", "")),
        place=bool(ctx.get("place")),
        away=bool(ctx.get("away")),
        # Which image was picked, if it was an image: that is the piece that
        # moves, and where it is standing is what the throw is measured from.
        via=str(choice.get("token", "")),
    )
    if nxt is not None:
        state.pending = nxt


def _choice_shove_to(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    target = state.frames.get(str(ctx.get("target")))
    if target is None:
        return
    dest = Pos(int(choice["x"]), int(choice["y"]))
    via = str(ctx.get("via", ""))
    if _is_fake(state, via):
        # A decoy was shoved: the decoy moves and the frame does not. Nothing
        # is given away -- the pieces are indistinguishable, so an enemy that
        # shoves one learns only that it shoved one.
        token = state.tokens[via]
        token.pos = dest
        state.note(f"an image of {target.id} is displaced")
        return
    _move_frame(state, target, dest)
    if ctx.get("after") == "reveal_nearby":
        for enemy in fx.frames_within(state, target, 3, side="enemy"):
            _apply_statuses(state, enemy, [("revealed", 1)])
    elif ctx.get("after") == "suplex":
        # "That target gets 2 stunned and 2 dazed" -- after the throw, so a
        # frame thrown onto the rails takes the fall and the jolt both.
        _apply_statuses(state, target, _parse_statuses(
            state.catalogue[SUPLEX].text))


def _move_frame(state: GameState, target: FrameState, pos: Pos) -> None:
    """A frame put somewhere by a card rather than by its own move step.

    A shove, a reflex step, a Teleport. It is still a frame moving on the
    board, so it raises the same beat a move decision does -- otherwise the
    replay would attribute the change to whatever happened next.
    """
    from . import objectives as objectivelib
    from . import resolve as _resolve

    old = target.pos
    if pos == old:
        return
    target.pos = pos
    target.moved_this_turn = True
    record_movement(state, target, old, pos)
    objectivelib.on_move(state, target, old)
    state.note(f"{target.id} is moved to ({pos.x},{pos.y})")
    _resolve._beat(state, "move")


# --------------------------------------------------------------------------
# Pilot cards -- Bruiser
# --------------------------------------------------------------------------


def _effect_relentless(state: GameState, frame: FrameState, uid: str):
    """"All other actions this frame takes this turn resolve twice at -2mv."

    The repeat is done by winding the card's initiative index back one when it
    finishes (`after_card_resolved`), so the repeated action goes through the
    ordinary queue at its own initiative rather than resolving back to back.
    """
    frame.turn_flags["repeat_actions"] = True
    _owe_movement(state, frame, -2)
    state.note(
        f"{frame.id} presses the attack: its other actions this turn "
        f"resolve twice, at -2 movement"
    )
    return None


def _blockers_in_hand(state: GameState, frame: FrameState) -> list[str]:
    """Remaining committed cards that could block something."""
    from . import combat

    return [
        uid for uid in combat.remaining_cards(state, frame)
        if any(state.card(uid).blocks.get(z, 0) > 0 for z in ZONES)
    ]


def _consume_block(state: GameState, frame: FrameState, uid: str) -> None:
    state.note(f"{frame.id} is intimidated and loses {state.card(uid).key}")
    discard_card(state, uid)


def _intimidate_step(
    state: GameState, source_id: str, queue: list[str]
) -> Optional[PendingDecision]:
    """Walk the victim list until one of them actually has a choice."""
    queue = list(queue)
    while queue:
        victim = state.frames.get(queue.pop(0))
        if victim is None or not victim.alive:
            continue
        candidates = _blockers_in_hand(state, victim)
        if not candidates:
            continue
        if len(candidates) == 1:
            _consume_block(state, victim, candidates[0])
            continue
        return _ask(
            state,
            "intimidate",
            seat=victim.seat,
            frame_id=victim.id,
            prompt=f"{victim.id} is intimidated: consume one block",
            options=[{"uid": u, "key": state.cards[u].key} for u in candidates],
            ctx={"queue": queue, "source": source_id},
        )
    return None


def _effect_intimidate(state: GameState, frame: FrameState, uid: str):
    reach = _reach_from_text(state.card(uid).text, 5)
    victims = [f.id for f in fx.frames_within(state, frame, reach, side="enemy")]
    if not victims:
        return None
    state.note(f"{frame.id} intimidates {len(victims)} enemy frame(s)")
    return _intimidate_step(state, frame.id, victims)


def _choice_intimidate(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    _consume_block(state, frame, str(choice["uid"]))
    nxt = _intimidate_step(state, str(ctx.get("source", "")), list(ctx.get("queue", [])))
    if nxt is not None:
        state.pending = nxt


def _effect_net_strength(state: GameState, frame: FrameState, uid: str):
    state.note(
        f"{frame.id}'s attacks this turn and next gain Guard Break "
        f"and daze what they hit"
    )
    return None


def _effect_target_status(state: GameState, frame: FrameState, uid: str):
    """"Target frame within N gets <statuses>" -- Lockdown and Outfox."""
    return _target_effect_step(state, frame, uid)


def _target_effect_step(
    state: GameState, frame: FrameState, uid: str, *, reach: Optional[int] = None
):
    """One frame picked, then whatever the card does to it.

    `reach` overrides the "within N" the text prints. Two cards print none at
    all (System Override, Sensory Overload), and reading a missing range as
    the default three would be inventing a restriction the card does not have.
    """
    card = state.card(uid)
    if reach is None:
        reach = _reach_from_text(card.text, 3)
    targets = fx.frames_within(state, frame, reach, side="any")
    if not targets:
        return None
    if len(targets) == 1:
        _apply_target_effect(state, targets[0], card)
        return None
    within = f" within {reach}" if reach < 10 ** 6 else ""
    return _ask(
        state,
        "target_status",
        seat=frame.seat,
        frame_id=frame.id,
        prompt=f"{card.name}: choose a frame{within}",
        options=_frame_options(state, targets),
        ctx={"uid": uid},
        pick_kind="frame",
    )


def _apply_target_effect(
    state: GameState, target: FrameState, card: Card
) -> None:
    """The statuses the card prints, plus anything else it does to one frame."""
    _apply_statuses(state, target, _parse_statuses(card.text))
    if card.key == SENSORY_OVERLOAD:
        _range_cap(state, target, OVERLOAD_RANGE_CAP)


def _choice_target_status(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    target = _target_frame(state, choice)
    if target is None:
        return
    card = state.catalogue[state.cards[str(ctx["uid"])].key]
    _apply_target_effect(state, target, card)


def _effect_bind(state: GameState, frame: FrameState, uid: str):
    """"Target an adjacent frame: as long as that frame is adjacent it cannot
    move."

    A grapple, and it is the *grappler* that has to keep hold: the lock is
    stored against this frame and asked about at movement time
    (`is_bound`), so it lifts the moment the Bruiser dies, is moved away, or
    the card leaves play -- rather than leaving a flag on the victim that
    something has to remember to clear.
    """
    victims = fx.frames_within(state, frame, 1, side="any")
    if not victims:
        state.note(f"{frame.id} grabs at nothing -- no frame is adjacent")
        return None
    if len(victims) == 1:
        _bind(state, frame, victims[0])
        return None
    return _ask(
        state,
        "bind",
        seat=frame.seat,
        frame_id=frame.id,
        prompt="Bind: which adjacent frame?",
        options=_frame_options(state, victims),
        pick_kind="frame",
    )


def _bind(state: GameState, frame: FrameState, target: FrameState) -> None:
    fx.slot(state, "bind")[frame.id] = target.id
    state.note(f"{frame.id} binds {target.id}: it cannot move while held")


def _choice_bind(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    target = _target_frame(state, choice)
    if target is not None:
        _bind(state, frame, target)


def is_bound(state: GameState, frame: FrameState) -> bool:
    """True while a living Bruiser is holding this frame in place."""
    if frame.pos is None:
        return False
    for holder_id, held_id in fx.slot(state, "bind").items():
        if held_id != frame.id:
            continue
        holder = state.frames.get(holder_id)
        if holder is None or not holder.alive or holder.pos is None:
            continue
        if not fx.card_active(state, holder, BIND):
            continue
        gap = fx.distance(state, holder.pos, frame.pos)
        if gap is not None and gap <= 1:
            return True
    return False


def _effect_suplex(state: GameState, frame: FrameState, uid: str):
    """"Move Target frame within 3 to a position within 3 the other side of
    this frame. That target gets 2 stunned and 2 dazed."

    The throw goes *through* the thrower, so the destination is measured from
    this frame and only the far side of it counts -- see `_shove_tiles`.
    """
    card = state.card(uid)
    reach, steps = _reaches_from_text(card.text, 3, 3)
    return _shove_step(
        state, frame, label=card.name, reach=reach, steps=steps,
        side="any", after="suplex", place=True, away=True,
    )


# --------------------------------------------------------------------------
# Pilot cards -- Mystic
# --------------------------------------------------------------------------


def _effect_teleport(state: GameState, frame: FrameState, uid: str):
    """"Reposition this frame anywhere on the map."

    It used to read "next turn ... at initiative 4", which made it a follow-up
    the driver had to offer between cards. It is now an ordinary effect step:
    the card resolves, the frame goes wherever it likes.

    Behind Ephemeral Images every image goes -- they all use the frame's
    actions -- so it is asked once per image, and the real one takes the frame
    with it.
    """
    if _images(state).get(frame.id):
        return _teleport_images(state, frame)
    options = [
        {"x": p.x, "y": p.y}
        for p in fx.free_tiles(state, frame.pos, 10 ** 6, include_origin=True)
    ]
    if not options:
        return None
    return _ask(
        state,
        "reposition",
        seat=frame.seat,
        frame_id=frame.id,
        prompt=f"Teleport: reposition {frame.id} anywhere on the map",
        options=options,
        pick_kind="place",
    )


def _teleport_images(state: GameState, frame: FrameState):
    """Queue every image for its own destination, then ask the first."""
    record = _images(state).get(frame.id)
    if record is None:
        return None
    record["porting"] = list(record.get("tokens", ()))
    return _next_image_teleport(state, frame)


def _next_image_teleport(state: GameState, frame: FrameState):
    """Ask the next image still owed a destination, or None when done."""
    record = _images(state).get(frame.id)
    while record is not None and record.get("porting"):
        token_id = str(record["porting"][0])
        token = state.tokens.get(token_id)
        if token is None or not token.alive or token.pos is None:
            record["porting"].pop(0)
            continue
        tiles = fx.free_tiles(state, token.pos, 10 ** 6)
        if not tiles:
            record["porting"].pop(0)
            continue
        return _ask(
            state,
            "image_teleport",
            seat=frame.seat,
            frame_id=frame.id,
            # Anonymous, like the movement step: which image is being asked
            # about is not something the question may say.
            prompt=f"Teleport: reposition an image of {frame.id}",
            options=[{"x": p.x, "y": p.y} for p in tiles],
            ctx={"token": token_id},
            pick_kind="place",
        )
    if record is not None:
        record.pop("porting", None)
    return None


def _choice_image_teleport(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    record = _images(state).get(frame.id)
    token_id = str(ctx.get("token"))
    if record is not None and record.get("porting"):
        record["porting"] = [t for t in record["porting"] if t != token_id]
    token = state.tokens.get(token_id)
    dest = Pos(int(choice["x"]), int(choice["y"]))
    if token is not None and token.alive:
        if record is not None and token_id == record.get("real"):
            # The frame is standing on this one, so it goes too; `sync_images`
            # keeps the token on the frame's tile from here.
            _move_frame(state, frame, dest)
        token.pos = dest
        state.note(f"an image of {frame.id} blinks somewhere else")
    nxt = _next_image_teleport(state, frame)
    if nxt is not None:
        state.pending = nxt


def _effect_utter_darkness(state: GameState, frame: FrameState, uid: str):
    state.note(
        f"{frame.id} calls down darkness: next turn nothing within "
        f"{DARKNESS_RADIUS} of it can be attacked"
    )
    return None


def _effect_encode(state: GameState, frame: FrameState, uid: str):
    """"Next turn: allied frames choose cards from their deck."

    Read as: next turn every frame on this side picks its actions out of its
    whole deck instead of only the hand it drew. It used to name one ally and
    ask which; the card now says all of them, so there is nothing to ask.
    """
    for ally in fx.frames_within(state, frame, 10 ** 6, side="ally", include_self=True):
        _arm_encode(state, ally)
    return None


def _arm_encode(state: GameState, target: FrameState) -> None:
    fx.slot(state, "encode")[target.id] = state.turn + 1
    state.note(f"{target.id} will choose next turn's actions from its deck")


def _effect_psychic_storm(state: GameState, frame: FrameState, uid: str):
    """"Create storm token within 5. At the end of each turn, every unit
    within 5 of that storm token takes 1 energy High."

    The storm outlives the card: Persistence is 0, but what the card makes is
    a token, and nothing on it says the weather clears. It hurts both sides --
    it says "every unit", and a storm that politely stepped around its own
    caster's frames would be a different card.
    """
    reach = _reach_from_text(state.card(uid).text, STORM_RADIUS)
    tiles = fx.free_tiles_from(state, frame, reach)
    if not tiles:
        return None
    return _ask(
        state,
        "psychic_storm",
        seat=frame.seat,
        frame_id=frame.id,
        prompt=f"Psychic Storm: where does it break (within {reach})?",
        options=[{"x": p.x, "y": p.y} for p in tiles],
        pick_kind="place",
    )


def _choice_psychic_storm(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    pos = Pos(int(choice["x"]), int(choice["y"]))
    fx.spawn_token(state, fx.STORM, pos, owner=frame.seat)
    state.note(f"a psychic storm breaks over ({pos.x},{pos.y})")


def _storm_step(state: GameState) -> None:
    """Every unit standing in a storm takes its damage, once per turn."""
    for storm in fx.tokens_of_kind(state, fx.STORM):
        for target in state.frames.values():
            if not target.alive or target.pos is None:
                continue
            gap = fx.distance(state, storm.pos, target.pos)
            if gap is not None and gap <= STORM_RADIUS:
                state.note(f"{target.id} is caught in the storm")
                deal_damage(state, target, STORM_ZONE, STORM_DAMAGE)
        for token in list(state.tokens.values()):
            if not token.alive or token.pos is None or not fx.is_unit(token):
                continue
            gap = fx.distance(state, storm.pos, token.pos)
            if gap is not None and gap <= STORM_RADIUS:
                damage_token(state, token, STORM_DAMAGE)


def _effect_doom(state: GameState, frame: FrameState, uid: str):
    """"Target a frame within 5: they get Dazed, and at the end of next turn,
    if they have not moved more than 3 spaces they take 3 energy High."

    "Next turn" is the turn after this one, and the count is that turn's
    movement -- so the daze lands now and the frame has a whole turn to run.
    """
    card = state.card(uid)
    reach = _reach_from_text(card.text, 5)
    targets = fx.frames_within(state, frame, reach, side="any")
    if not targets:
        return None
    if len(targets) == 1:
        _doom(state, targets[0], card)
        return None
    return _ask(
        state,
        "doom",
        seat=frame.seat,
        frame_id=frame.id,
        prompt=f"Doom: which frame within {reach}?",
        options=_frame_options(state, targets),
        pick_kind="frame",
    )


def _doom(state: GameState, target: FrameState, card: Card) -> None:
    _apply_statuses(state, target, _parse_statuses(card.text))
    fx.slot(state, "doom")[target.id] = state.turn + 1
    state.note(
        f"{target.id} is doomed: it must move more than {DOOM_ESCAPE} "
        f"spaces next turn or take {DOOM_DAMAGE}"
    )


def _choice_doom(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    target = _target_frame(state, choice)
    if target is not None:
        _doom(state, target, state.catalogue[DOOM])


def _doom_step(state: GameState) -> None:
    """End of turn: collect from anything the doom caught standing still."""
    marked = fx.slot(state, "doom")
    for frame_id in [k for k, turn in marked.items() if turn == state.turn]:
        marked.pop(frame_id, None)
        target = state.frames.get(frame_id)
        if target is None or not target.alive:
            continue
        moved = int(target.turn_flags.get("moved_distance", 0))
        if moved > DOOM_ESCAPE:
            state.note(f"{target.id} outruns its doom ({moved} spaces)")
            continue
        state.note(f"{target.id}'s doom lands ({moved} spaces moved)")
        deal_damage(state, target, DOOM_ZONE, DOOM_DAMAGE)


# --------------------------------------------------------------------------
# Pilot cards -- Tactician
# --------------------------------------------------------------------------


def _effect_allies_status(state: GameState, frame: FrameState, uid: str):
    """"Allied frames within N get <statuses>" -- Tactical broadcast."""
    card = state.card(uid)
    reach = _reach_from_text(card.text, 8)
    pairs = _parse_statuses(card.text)
    allies = fx.frames_within(state, frame, reach, side="ally", include_self=True)
    for ally in allies:
        _apply_statuses(state, ally, pairs)
    if allies:
        state.note(f"{card.name} reaches {len(allies)} allied frame(s)")
    return None


def _effect_fog_of_war(state: GameState, frame: FrameState, uid: str):
    reach = _reach_from_text(state.card(uid).text, 7)
    state.note(
        f"{frame.id} jams the net: next turn allies within {reach} "
        f"cannot be targeted by ranged attacks"
    )
    return None


def _effect_set_the_trap(state: GameState, frame: FrameState, uid: str):
    """"Move an allied frame within 5 2 space. All enemies within 3 of them
    get revealed"."""
    return _shove_step(
        state, frame, label="Set the trap", reach=5, steps=2,
        side="ally", after="reveal_nearby",
    )


def _effect_displace(state: GameState, frame: FrameState, uid: str):
    """"Move another target frame within 5 to a new position within 8."

    Two different distances, both measured from the Tactician: how far it can
    reach to grab something, and how far it can throw it. Unlike Set the trap
    the frame does not walk there -- it is displaced, so walls and its own
    movement have nothing to say about where it ends up.
    """
    card = state.card(uid)
    reach, steps = _reaches_from_text(card.text, 5, 8)
    return _shove_step(
        state, frame, label=card.name, reach=reach, steps=steps,
        side="any", after="", place=True,
    )


# --------------------------------------------------------------------------
# Pilot cards -- Wunderkid
# --------------------------------------------------------------------------


def _effect_hyper(state: GameState, frame: FrameState, uid: str):
    state.note(f"{frame.id} may commit an extra action next turn")
    return None


def _effect_self_status(state: GameState, frame: FrameState, uid: str):
    """"This frame gets <statuses>" -- Net Speed, Precision Tuning."""
    card = state.card(uid)
    _apply_statuses(state, frame, _parse_statuses(card.text))
    return None


def _effect_portal(state: GameState, frame: FrameState, uid: str):
    """"Create two portals within 7. Those tiles are connected."

    The card used to read "a portal at the start and end of this move", which
    made it a movement rider; it now names its own two tiles, so the pair is
    chosen here rather than inferred from where the frame walked.
    """
    return _portal_step(
        state, frame,
        reach=_reach_from_text(state.card(uid).text, 7),
        first=None,
    )


def _portal_step(
    state: GameState, frame: FrameState, *, reach: int, first: Optional[Pos]
):
    """Ask for one end of the pair. `first` is the end already chosen."""
    tiles = [p for p in fx.free_tiles_from(state, frame, reach) if p != first]
    if not tiles:
        if first is not None:
            state.note(f"{frame.id} finds nowhere to anchor the far end")
        return None
    wanted = 1 if first is not None else min(2, len(tiles))
    return _ask(
        state,
        "portal",
        seat=frame.seat,
        frame_id=frame.id,
        prompt=(
            f"Portal: choose both ends (within {reach})" if first is None
            else f"Portal: choose the tile to link ({first.x},{first.y}) to"
        ),
        options=[{"x": p.x, "y": p.y} for p in tiles],
        ctx={"reach": reach, "first": ([first.x, first.y] if first else None)},
        pick_min=wanted,
        pick_max=wanted,
        pick_kind="place",
    )


def _choice_portal(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    pos = Pos(int(choice["x"]), int(choice["y"]))
    raw = ctx.get("first")
    if raw is None:
        nxt = _portal_step(state, frame, reach=int(ctx.get("reach", 7)), first=pos)
        if nxt is not None:
            state.pending = nxt
        else:
            # Nowhere for the far end: one portal on its own connects nothing,
            # so the card simply does not go up.
            state.note(f"{frame.id} cannot open a pair of portals")
        return
    near = fx.spawn_token(state, fx.PORTAL, Pos(int(raw[0]), int(raw[1])),
                          owner=frame.seat)
    far = fx.spawn_token(state, fx.PORTAL, pos, owner=frame.seat)
    pairs = fx.slot(state, "portals")
    pairs[near.id] = far.id
    pairs[far.id] = near.id
    state.note(
        f"a portal links ({near.pos.x},{near.pos.y}) and ({far.pos.x},{far.pos.y})"
    )


def _effect_ace_reflexes(state: GameState, frame: FrameState, uid: str):
    state.note(f"{frame.id} is on its toes: it moves 2 after every attack on it")
    return None


def _effect_parallel_action(state: GameState, frame: FrameState, uid: str):
    """""Next turn:" the next time this frame would take an action or is
    attacked, draw another hand and swap any face down actions you like with
    cards from that hand."

    Armed here and fired by `followup_decision` -- which is exactly the moment
    the driver is between cards, so "would take an action" is checked before
    the action is chosen and the swap can still change what happens next.
    Being attacked sets the same flag from `after_attacked`, because an attack
    cannot stop half way to ask a question.

    Nothing fires on the turn it is played: the card is a plan for the turn
    after, and without the delay the opponent could burn it by attacking after
    it resolved -- a redraw with nothing left to change is no card at all.
    That is what "Next turn:" means everywhere else on these cards, so it is
    asked the same way: the card is only in the `aside` pile from the turn
    after it resolved (`card_active(this_turn=False)`).
    """
    fx.slot(state, "parallel")[frame.id] = {"card": uid}
    state.note(f"{frame.id} runs its actions in parallel: next turn's may change")
    return None


def _parallel_armed(state: GameState, frame: FrameState) -> bool:
    """Armed *and* far enough into the game to fire -- see the card's docstring."""
    return (
        frame.id in fx.slot(state, "parallel")
        and fx.card_active(state, frame, PARALLEL_ACTION, this_turn=False)
    )


def _swappable(state: GameState, frame: FrameState) -> list[str]:
    """The actions still in front of the frame and still to resolve.

    The card says "face down actions", which is how the rules name an action
    that has not resolved yet -- so that is what this reads it as, rather than
    literally "currently face down". A frame under `Revealed` has its whole
    plan turned face up, and switching off a different card as a side effect
    of that would be a rule nobody printed.
    """
    return [
        uid for uid in frame.committed
        if state.cards[uid].location == "committed"
        and not state.cards[uid].resolved
    ]


def _parallel_step(state: GameState, frame: FrameState) -> Optional[PendingDecision]:
    """Ask which face-down action to swap out, or to stop.

    A card that has been looked at and kept is not offered again. Without
    that the exchange has no end: "swap out this one" -> "actually keep it"
    -> "swap out this one" for as long as anyone likes. Each round now settles
    exactly one action, so the run is as long as the frame has cards.
    """
    record = fx.slot(state, "parallel").get(frame.id) or {}
    settled = set(record.get("settled") or ())
    mine = [uid for uid in _swappable(state, frame) if uid not in settled]
    fresh = [uid for uid in frame.hand]
    if not mine or not fresh:
        _parallel_finish(state, frame)
        return None
    options: list[dict[str, Any]] = [
        {"uid": uid, "key": state.cards[uid].key, "swap": "out"} for uid in mine
    ]
    options.append({"done": True})
    return _ask(
        state,
        "parallel_out",
        seat=frame.seat,
        frame_id=frame.id,
        prompt=f"Parallel Action: swap out one of {frame.id}'s actions?",
        options=options,
        pick_min=0,
        pick_max=len(mine),
    )


def _choice_parallel_out(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    if choice.get("done"):
        _parallel_finish(state, frame)
        return
    out = str(choice.get("uid"))
    options = [
        {"uid": uid, "key": state.cards[uid].key, "swap": "in"}
        for uid in frame.hand
    ]
    if not options:
        _parallel_finish(state, frame)
        return
    # The card being swapped out is offered back. Naming it is not committing
    # to losing it: without this the only way out of a mis-tap is to spend the
    # swap on a card you did not want.
    options.append({"uid": out, "key": state.cards[out].key, "swap": "keep"})
    state.pending = _ask(
        state,
        "parallel_in",
        seat=frame.seat,
        frame_id=frame.id,
        prompt=(
            f"Parallel Action: play what instead of "
            f"{state.catalogue[state.cards[out].key].name}?"
        ),
        options=options,
        ctx={"out": out},
    )


def _choice_parallel_in(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    out = str(ctx.get("out"))
    incoming = str(choice.get("uid"))
    if incoming == out:
        state.note(f"{frame.id} keeps {state.cards[out].key}")
        _settle(state, frame, out)
        nxt = _parallel_step(state, frame)
        if nxt is not None:
            state.pending = nxt
        return
    if out in state.cards and incoming in state.cards:
        state.note(
            f"{frame.id} swaps {state.cards[out].key} for {state.cards[incoming].key}"
        )
        discard_card(state, out)
        move_card(state, incoming, "committed")
        inst = state.cards[incoming]
        inst.face_down = True
        inst.resolved = False
        inst.init_index = 0
    nxt = _parallel_step(state, frame)
    if nxt is not None:
        state.pending = nxt


def _settle(state: GameState, frame: FrameState, uid: str) -> None:
    """This action has had its look and is staying: do not offer it again."""
    record = fx.slot(state, "parallel").get(frame.id)
    if record is not None:
        record.setdefault("settled", []).append(uid)


def _parallel_finish(state: GameState, frame: FrameState) -> None:
    """The swap is over: the rest of the extra hand goes, and so does the card.

    The hand was drawn for this one look; nothing on the card says it is kept,
    and a Wunderkid holding seven spare cards into the next planning phase
    would be playing a different game.
    """
    record = fx.slot(state, "parallel").pop(frame.id, None)
    for uid in list(frame.hand):
        move_card(state, uid, "discard")
    frame.turn_flags.pop("parallel_now", None)
    if record and str(record.get("card")) in state.cards:
        discard_card(state, str(record["card"]))
    state.note(f"{frame.id} settles on its actions")


def _parallel_fire(state: GameState, frame: FrameState) -> bool:
    """Draw the second hand and start asking. True if a decision parked."""
    drawn = draw(state, frame, frame.draw_count)
    state.note(f"Parallel Action: {frame.id} draws {len(drawn)} more cards")
    decision = _parallel_step(state, frame)
    if decision is None:
        return False
    state.pending = decision
    return True


def _effect_showboating(state: GameState, frame: FrameState, uid: str):
    """"For the rest of this turn, any frame that is able to must attack this
    frame and this frame's blocks are not discarded."

    Both halves are asked for rather than pushed: `forced_target` filters the
    attacker's option list and `blocks_are_kept` answers `keywords.block_is_kept`.
    """
    state.note(
        f"{frame.id} showboats: everything that can reach it must swing at it, "
        f"and its blocks are not discarded"
    )
    return None


# --------------------------------------------------------------------------
# Pilot cards -- Engineer
# --------------------------------------------------------------------------


def _effect_battlefield_repairs(state: GameState, frame: FrameState, uid: str):
    card = state.card(uid)
    reach = _reach_from_text(card.text, 3)
    amount = int(_REPAIR_RE.search(card.text).group(1)) if _REPAIR_RE.search(card.text) else 3
    allies = [
        f for f in fx.frames_within(state, frame, reach, side="ally", include_self=True)
        if any(f.damage[z] > 0 for z in ZONES)
    ]
    if not allies:
        return None
    allies.sort(key=lambda f: -sum(f.damage[z] for z in ZONES))
    if len(allies) == 1:
        repair(state, allies[0], amount)
        return None
    return _ask(
        state,
        "repairs",
        seat=frame.seat,
        frame_id=frame.id,
        prompt=f"Battlefield Repairs: repair {amount} from an ally within {reach}",
        options=_frame_options(state, allies),
        ctx={"amount": amount},
    )


def _choice_repairs(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    target = _target_frame(state, choice)
    if target is not None:
        repair(state, target, int(ctx.get("amount", 3)))


def _barricade_step(
    state: GameState, frame: FrameState, reach: int, left: int
) -> Optional[PendingDecision]:
    if left <= 0:
        return None
    tiles = fx.free_tiles_from(state, frame, reach)
    if not tiles:
        return None
    options: list[dict[str, Any]] = [{"x": p.x, "y": p.y} for p in tiles]
    options.append({"done": True})
    return _ask(
        state,
        "barricade",
        seat=frame.seat,
        frame_id=frame.id,
        prompt=f"Barricade: place up to {left} more within {reach}",
        options=options,
        ctx={"reach": reach, "left": left},
        pick_min=0,                     # "up to 3" -- and `done` stops early
        pick_max=min(left, len(tiles)),
        pick_kind="place",
    )


def _effect_barricade(state: GameState, frame: FrameState, uid: str):
    text = state.card(uid).text
    return _barricade_step(
        state, frame,
        _reach_from_text(text, 3),
        _count_from_text(text, BARRICADE_COUNT),
    )


def _choice_barricade(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    if choice.get("done"):
        return
    pos = Pos(int(choice["x"]), int(choice["y"]))
    fx.spawn_token(state, fx.BARRICADE, pos, owner=frame.seat)
    state.note(f"a barricade blocks ({pos.x},{pos.y})")
    nxt = _barricade_step(
        state, frame, int(ctx.get("reach", 3)), int(ctx.get("left", 1)) - 1
    )
    if nxt is not None:
        state.pending = nxt


def _effect_gravity_well(state: GameState, frame: FrameState, uid: str):
    """"Create a gravity well within 5. Its an obstacle that adds an extra 1
    movement penalty to any step away from it within 5."

    Two 5s in that sentence and they mean different things: the first is how
    far the well can be *placed*, the second the radius it drags inside.
    `_reach_from_text` takes the first, which is the placement one.
    """
    reach = _reach_from_text(state.card(uid).text, 1)
    tiles = fx.free_tiles_from(state, frame, reach)
    if not tiles:
        return None
    if len(tiles) == 1:
        _place_well(state, frame, tiles[0])
        return None
    return _ask(
        state,
        "gravity_well",
        seat=frame.seat,
        frame_id=frame.id,
        prompt=f"Gravity Well: choose a tile within {reach}",
        options=[{"x": p.x, "y": p.y} for p in tiles],
        pick_kind="place",
    )


def _place_well(state: GameState, frame: FrameState, pos: Pos) -> None:
    fx.spawn_token(state, fx.GRAVITY_WELL, pos, owner=frame.seat)
    state.note(f"a gravity well opens at ({pos.x},{pos.y})")


def _choice_gravity_well(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    _place_well(state, frame, Pos(int(choice["x"]), int(choice["y"])))


def _effect_system_override(state: GameState, frame: FrameState, uid: str):
    """"You choose which tile target frame within 4 moves to on their next
    action."

    The hold is spent on the next movement decision that frame is offered --
    an action with no movement in it has no tile to choose, so it is not what
    the card is talking about. The range is checked when the card resolves;
    the frame may be anywhere by the time it moves.
    """
    reach = _reach_from_text(state.card(uid).text, 4)
    targets = fx.frames_within(state, frame, reach, side="any")
    if not targets:
        return None
    if len(targets) == 1:
        _override(state, frame, targets[0])
        return None
    return _ask(
        state,
        "system_override",
        seat=frame.seat,
        frame_id=frame.id,
        prompt="System Override: whose next move do you take?",
        options=_frame_options(state, targets),
        pick_kind="frame",
    )


def _override(state: GameState, frame: FrameState, target: FrameState) -> None:
    fx.slot(state, "override")[target.id] = frame.seat
    state.note(f"{frame.id} takes control of {target.id}'s next move")


def _choice_system_override(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    target = _target_frame(state, choice)
    if target is not None:
        _override(state, frame, target)


def move_chooser(state: GameState, frame: FrameState) -> Team:
    """Which seat answers this frame's movement decision. Normally its own.

    Spends the hold: System Override is "their next action", once.
    """
    seat = fx.slot(state, "override").pop(frame.id, None)
    if seat is None:
        return frame.seat
    state.note(f"{frame.id} is under System Override: the other side moves it")
    return int(seat)


def _effect_sensory_overload(state: GameState, frame: FrameState, uid: str):
    """"Target frame within 6 gets 2 Dazed 3 Stunned and their ranged attacks
    this turn have a range of 2"."""
    return _target_effect_step(state, frame, uid)


def _range_cap(state: GameState, target: FrameState, cap: int) -> None:
    have = int(target.turn_flags.get("range_cap", 0))
    target.turn_flags["range_cap"] = cap if have <= 0 else min(have, cap)
    state.note(f"{target.id}'s ranged attacks reach only {cap} this turn")


# --------------------------------------------------------------------------
# Pilot cards -- Specialist
# --------------------------------------------------------------------------


def _effect_combo_strike(state: GameState, frame: FrameState, uid: str):
    """"Until the end of next turn: when resolving attacks from this frame..."

    Nothing is armed. Like Snipers aim, the card in front of the frame *is*
    the state -- `fx.card_active` covers the turn it resolved and, at
    persistence 1, the turn after -- so it now rides *every* attack in that
    window rather than only the next one. The `combo` slot is used for one
    attack's chosen extra and is emptied as soon as that attack applies it.
    """
    state.note(f"{frame.id} lines up combos on its attacks this turn and next")
    return None


def _armed_rider(
    state: GameState, frame: FrameState, card: Card
) -> Optional[str]:
    """The name of an effect owed by `frame` that fires on `card`, if any."""
    if (
        card.is_attack
        and not delegates_attack(card)
        and fx.card_active(state, frame, COMBO_STRIKE)
    ):
        return "combo"
    return None


def _combo_options(
    state: GameState, frame: FrameState, card: Card
) -> list[dict[str, Any]]:
    """Up to 1 attack from the same weapon among the top 4 of the deck."""
    out: list[dict[str, Any]] = []
    for uid in frame.deck[:4]:
        other = state.catalogue[state.cards[uid].key]
        if other.group == card.group and other.is_attack:
            out.append({"uid": uid, "key": other.key})
    return out


def _combo_decision(
    state: GameState, frame: FrameState, uid: str
) -> Optional[PendingDecision]:
    """"When resolving the next attack ... reveal the top 4 cards of its deck
    and choose up to 1 attack from the same weapon: add its attacks"."""
    res = state.resolution
    if res is None or "attack" not in res.steps:
        # The controller ordered the attack before the effect: nothing to add
        # to any more. Stay armed for the next attack rather than fizzling.
        return None
    card = state.card(uid)
    options = _combo_options(state, frame, card)
    # Nothing chosen for this attack yet: drop anything a previous one left.
    fx.slot(state, "combo").pop(frame.id, None)
    if not options:
        state.note(f"Combo strike: no {card.group} attack in the top 4 cards")
        return None
    options.append({"skip": True})
    return _ask(
        state,
        "combo",
        seat=frame.seat,
        frame_id=frame.id,
        prompt=f"Combo strike: add a {card.group} attack to {card.name}?",
        options=options,
    )


def _choice_combo(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    if choice.get("skip"):
        return
    uid = str(choice.get("uid"))
    if uid not in state.cards:
        return
    card = state.catalogue[state.cards[uid].key]
    fx.slot(state, "combo")[frame.id] = {
        "extra": {z: card.attacks[z] for z in ZONES if card.attacks[z] > 0},
    }
    discard_card(state, uid)
    state.note(f"Combo strike adds {card.key}'s attack")


def _effect_snipers_aim(state: GameState, frame: FrameState, uid: str):
    """"Until the end of next turn: ranged attacks ignore obstacles, get +N
    range and deal M extra damage."

    Nothing is stored. The card itself is the state: it stays in front of the
    frame this turn and, with persistence 1, in the `aside` pile the next, so
    `fx.card_active` answers "is it still in force?" for both -- which is what
    "until the end of next turn" means. The two numbers are read off the
    printed text at the point of use (`snipers_range_bonus`, the damage bonus
    in `attack_damage_bonus`), so a balance pass needs no engine change.
    """
    card = state.card(uid)
    state.note(
        f"{frame.id} takes aim: ranged attacks this turn and next ignore "
        f"obstacles, reach {_extra_range(card)} further and deal "
        f"{_extra_damage(card)} extra damage"
    )
    return None


def _extra_range(card: Optional[Card]) -> int:
    match = _EXTRA_RANGE_RE.search((card.text if card else "") or "")
    return int(match.group(1)) if match else 4


def _extra_damage(card: Optional[Card]) -> int:
    match = _EXTRA_DAMAGE_RE.search((card.text if card else "") or "")
    return int(match.group(1)) if match else 1


def snipers_range_bonus(state: GameState, frame: FrameState, card: Card) -> int:
    """Extra range a still-in-play Snipers aim gives this frame's ranged attacks.

    Asked for by `keywords.range_bonus`, which owns the sum of every source.
    """
    if not card.is_ranged or not fx.card_active(state, frame, SNIPERS_AIM):
        return 0
    return _extra_range(state.catalogue.get(SNIPERS_AIM))


def _effect_master_duelist(state: GameState, frame: FrameState, uid: str):
    state.note(
        f"{frame.id} duels: for the next 3 turns its melee targets are "
        f"revealed and it picks their blocks"
    )
    return None


def _effect_practiced_technique(state: GameState, frame: FrameState, uid: str):
    _apply_statuses(state, frame, _parse_statuses((state.card(uid).text or "").split("\\\\")[0]))
    state.note(
        f"{frame.id}'s attacks next turn hit harder for each other "
        f"attack from the same weapon"
    )
    return None


def _effect_rebound(state: GameState, frame: FrameState, uid: str):
    """"Create a Rebound token within 5. If this frame can see the token, its
    treated as if it can see every enemy within 4 of that token."

    A mirror for shooting round corners. It belongs to the frame that put it
    down rather than to the seat -- "this frame" -- so a second Specialist does
    not get to borrow it.
    """
    reach = _reach_from_text(state.card(uid).text, 5)
    tiles = fx.free_tiles_from(state, frame, reach)
    if not tiles:
        return None
    return _ask(
        state,
        "rebound",
        seat=frame.seat,
        frame_id=frame.id,
        prompt=f"Rebound: where does the mirror go (within {reach})?",
        options=[{"x": p.x, "y": p.y} for p in tiles],
        pick_kind="place",
    )


def _choice_rebound(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    pos = Pos(int(choice["x"]), int(choice["y"]))
    token = fx.spawn_token(state, fx.REBOUND, pos, owner=frame.seat)
    fx.slot(state, "rebound")[token.id] = frame.id
    state.note(f"{frame.id} sets a rebound at ({pos.x},{pos.y})")


def rebound_sight(
    state: GameState, attacker: FrameState, target_pos: Pos
) -> bool:
    """True when one of this frame's rebounds sees what the frame cannot.

    Asked only after ordinary line of sight has failed, so the mirror never
    takes anything away -- it only adds the enemies standing within 4 of a
    rebound the frame can see itself.
    """
    if attacker.pos is None or state.board is None:
        return False
    if not fx.card_active(state, attacker, REBOUND):
        return False
    owners = fx.slot(state, "rebound")
    for token in fx.tokens_of_kind(state, fx.REBOUND):
        if owners.get(token.id) != attacker.id:
            continue
        gap = fx.distance(state, token.pos, target_pos)
        if gap is None or gap > REBOUND_RADIUS:
            continue
        if state.board.has_line_of_sight(
            attacker.pos, token.pos, occupied=state.occupied(exclude=attacker.id)
        ):
            return True
    return False


def _effect_cage_fight(state: GameState, frame: FrameState, uid: str):
    """"Choose an enemy frame within 2. Create a 5x5 box of impassible terrain
    that surrounds this frame and that frame."

    The box is not chosen: both fighters have to end up inside the 3x3 the
    walls enclose, and with them at most two apart the centre is their
    midpoint. So the only question the card asks is who is being locked in
    with -- the walls follow.
    """
    reach = _reach_from_text(state.card(uid).text, 2)
    victims = fx.frames_within(state, frame, reach, side="enemy")
    if not victims:
        state.note(f"{frame.id} finds nobody to lock in with")
        return None
    if len(victims) == 1:
        _raise_cage(state, frame, victims[0])
        return None
    return _ask(
        state,
        "cage_fight",
        seat=frame.seat,
        frame_id=frame.id,
        prompt=f"Cage Fight: who is locked in (within {reach})?",
        options=_frame_options(state, victims),
        pick_kind="frame",
    )


def _choice_cage_fight(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    target = _target_frame(state, choice)
    if target is not None:
        _raise_cage(state, frame, target)


def _cage_walls(state: GameState, centre: Pos) -> list[Pos]:
    """The ring of the 5x5 box: everything exactly `CAGE_RADIUS` from centre."""
    out: list[Pos] = []
    if state.board is None:
        return out
    for dy in range(-CAGE_RADIUS, CAGE_RADIUS + 1):
        for dx in range(-CAGE_RADIUS, CAGE_RADIUS + 1):
            if max(abs(dx), abs(dy)) != CAGE_RADIUS:
                continue
            pos = Pos(centre.x + dx, centre.y + dy)
            if state.board.in_bounds(pos):
                out.append(pos)
    return out


def _push_out(state: GameState, centre: Pos, pos: Pos) -> Optional[Pos]:
    """The nearest free tile outside the box, for whatever the wall lands on."""
    if state.board is None:
        return None
    taken = set(state.occupied()) | {
        t.pos for t in state.tokens.values() if t.alive and t.pos is not None
    }
    best: Optional[Pos] = None
    best_key = (10 ** 6, 0, 0)
    for y in range(state.board.height):
        for x in range(state.board.width):
            candidate = Pos(x, y)
            if state.board.distance(centre, candidate) <= CAGE_RADIUS:
                continue
            tile = state.board.tile(candidate)
            if tile.impassable or tile.obstacle or candidate in taken:
                continue
            key = (state.board.distance(pos, candidate), y, x)
            if key < best_key:
                best, best_key = candidate, key
    return best


def _raise_cage(state: GameState, frame: FrameState, target: FrameState) -> None:
    if frame.pos is None or target.pos is None or state.board is None:
        return
    centre = Pos((frame.pos.x + target.pos.x) // 2, (frame.pos.y + target.pos.y) // 2)
    walls: list[str] = []
    for pos in _cage_walls(state, centre):
        # "Units in tiles that this places are pushed outside" -- and so is
        # anything else standing there, since the wall goes up regardless.
        standing = state.frame_at(pos)
        if standing is not None:
            spot = _push_out(state, centre, pos)
            if spot is not None:
                _move_frame(state, standing, spot)
            else:
                continue                       # nowhere to put it: no wall here
        for token in state.tokens.values():
            if token.alive and token.pos == pos:
                spot = _push_out(state, centre, pos)
                if spot is None:
                    break
                token.pos = spot
        if state.frame_at(pos) is not None:
            continue
        if any(t.alive and t.pos == pos for t in state.tokens.values()):
            continue
        walls.append(fx.spawn_token(state, fx.CAGE, pos, owner=frame.seat).id)
    fx.slot(state, "cages")[frame.id] = {
        "centre": [centre.x, centre.y],
        "fighters": [frame.id, target.id],
        "walls": walls,
    }
    state.note(
        f"{frame.id} cages {target.id}: a 5x5 box around "
        f"({centre.x},{centre.y})"
    )


def _in_cage(state: GameState, record: Mapping, frame_id: str) -> bool:
    frame = state.frames.get(frame_id)
    if frame is None or not frame.alive or frame.pos is None:
        return False
    centre = Pos(int(record["centre"][0]), int(record["centre"][1]))
    gap = fx.distance(state, centre, frame.pos)
    return gap is not None and gap < CAGE_RADIUS


def sync_cages(state: GameState) -> None:
    """"Remove this cage when this frame or the target dies or leaves the cage."

    Checked between beats rather than hooked to death and movement, for the
    same reason `sync_images` is: there are half a dozen ways off a tile and
    only one of them is a move step.
    """
    cages = fx.slot(state, "cages")
    for owner_id in list(cages):
        record = cages[owner_id]
        if all(_in_cage(state, record, fid) for fid in record["fighters"]):
            continue
        for token_id in record.get("walls", []):
            token = state.tokens.get(str(token_id))
            if token is not None:
                token.alive = False
                token.pos = None
        cages.pop(owner_id, None)
        state.note("the cage comes down")
        owner = state.frames.get(owner_id)
        if owner is not None:
            for uid in list(owner.aside) + list(owner.committed):
                if state.cards[uid].key == CAGE_FIGHT:
                    discard_card(state, uid)
                    break


# --------------------------------------------------------------------------
# Drone cards
# --------------------------------------------------------------------------


def _drone_reach(card: Card) -> int:
    """The furthest the drone's own printed attack carries. 0 = melee."""
    return max((card.ranges[z] for z in ZONES if card.attacks[z] > 0), default=0)


def _placement_options(
    state: GameState, frame: FrameState, reach: int, card: Card
) -> list[dict[str, Any]]:
    """Free tiles to put a drone on, each carrying what it will shoot with.

    `reach` rides along because the choice is not "somewhere useful to stand"
    -- an immobile Gun Tower shooting 8 tiles wants very different ground from
    an Attack Dog that has to bite. Nothing is obliged to read it.
    """
    return [
        {"x": pos.x, "y": pos.y, "reach": _drone_reach(card)}
        for pos in fx.free_tiles_from(state, frame, reach)
    ]


def _effect_summon_drone(state: GameState, frame: FrameState, uid: str):
    """"Summon <n> <drone>[ within <r>]": tokens that repeat this card each turn.

    Every drone card resolves through here -- the count and the placement reach
    are read off the printed text, so a new drone in `Drone actions.csv` needs
    no code. "Summon one Swarm" is one token beside the frame; "Summon one Gun
    Tower within 3" is one token up to three tiles away; "Summon two attack
    dogs" is two, one decision each.
    """
    card = state.card(uid)
    res = state.resolution
    if res is not None and "attack" in res.steps:
        # The drone makes the attack printed on this card, not the frame.
        res.steps.remove("attack")
    return _summon_step(
        state, frame, uid,
        reach=_reach_from_text(card.text, 1),
        left=_count_from_text(card.text, 1),
    )


def _summon_step(
    state: GameState, frame: FrameState, uid: str, *, reach: int, left: int
):
    """Place one drone, then come back for the next. `None` when done."""
    if left <= 0:
        return None
    card = state.card(uid)
    options = _placement_options(state, frame, reach, card)
    if not options:
        state.note(f"{frame.id} has no room to deploy a {card.name}")
        return None
    if len(options) == 1:
        _deploy_drone(state, frame, uid, Pos(options[0]["x"], options[0]["y"]))
        return _summon_step(state, frame, uid, reach=reach, left=left - 1)
    where = "beside it" if reach <= 1 else f"within {reach}"
    wanted = min(left, len(options))
    return _ask(
        state,
        "summon_drone",
        seat=frame.seat,
        frame_id=frame.id,
        prompt=(
            f"Deploy {card.name} {where}"
            + (f" ({left} left)" if left > 1 else "")
        ),
        options=options,
        ctx={"reach": reach, "left": left, "uid": uid},
        pick_min=wanted,
        pick_max=wanted,
        pick_kind="place",
    )


def _deploy_drone(
    state: GameState, frame: FrameState, uid: str, pos: Pos
) -> None:
    card = state.card(uid)
    token = fx.spawn_token(
        state, fx.DRONE, pos, hp=max(1, card.drone_health), owner=frame.seat
    )
    fx.slot(state, "drones")[token.id] = {
        "frame": frame.id,
        "key": card.key,
        "uid": uid,
        "acted": 0,
    }
    state.note(
        f"{frame.id} deploys its {card.name} drone at ({pos.x},{pos.y})"
    )


def _choice_summon_drone(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    uid = str(ctx.get("uid", ""))
    _deploy_drone(state, frame, uid, Pos(int(choice["x"]), int(choice["y"])))
    nxt = _summon_step(
        state, frame, uid,
        reach=int(ctx.get("reach", 1)),
        left=int(ctx.get("left", 1)) - 1,
    )
    if nxt is not None:
        state.pending = nxt


# --------------------------------------------------------------------------
# Ephemeral Images
# --------------------------------------------------------------------------
#
# "Replace this frame with 3 tokens, one of which is secretly marked to be this
# frame. These tokens use this frame's actions and the fakes are removed if
# attacked or if they would deal damage."
#
# The frame does not actually leave the board -- it stands on one of the three
# tiles, and that tile carries the *real* image. What changes is what an enemy
# may aim at: while the images are up the frame itself is not a legal target,
# and its three images are. Striking the real one is an ordinary attack on the
# frame (which then has to block as usual, and is revealed by the hit);
# striking a fake removes it and nothing else.
#
# "These tokens use this frame's actions" is read as: when the frame resolves a
# card, all three images resolve it, at the same time and each from where it
# stands. Three consequences, and they are the whole card:
#
# * **They move separately.** The card's movement step asks once for the frame
#   (which carries the real image) and then once for each fake, each with the
#   same budget. Nothing drags them along any more.
# * **Anything the action counts may be counted from any of them**
#   (`effects_state.origins`): range, line of sight and every "within N". A
#   zone lands if any image is placed to land it.
# * **A fake that would deal damage is removed.** All three swing; only one of
#   them can actually hurt anything, so any fake whose own copy of the attack
#   reached what was hit is given away and goes. Shooting is therefore how the
#   trick ends -- unless the shot is blocked, in which case no damage was dealt
#   and nobody learned anything.
#
# Forced movement is the exception to "separately": a knockback, a Teleport or
# an Ace Reflexes step moves the frame alone, so `sync_images` still slides the
# fakes by the same delta. Letting the real one walk off on its own would say
# which it was, and the concealment has to survive things the player did not
# choose.
#
# The concealment is structural rather than a matter of the client behaving
# itself: `view_for` gives another seat no position for a cloaked frame and no
# way to tell one image from another, and the engine offers the frame in no
# target list -- so there is no path from an enemy's view or its legal moves
# to which tile the frame is really on.

#: Token kind for an image. The piece art ships as `tokens/Image.png`.
IMAGE = fx.IMAGE

#: How many images the card puts on the table, the real one included.
IMAGE_COUNT = 3

#: The bookkeeping itself lives in `effects_state`, so the shared frame
#: queries there can leave a hidden frame out of every enemy option list.
_images = fx.image_records
is_cloaked = fx.is_cloaked
image_positions = fx.image_positions


def image_tokens(state: GameState, frame: FrameState) -> list[str]:
    record = _images(state).get(frame.id)
    return list(record.get("tokens", ())) if record else []


def image_owner(
    state: GameState, token: TokenState
) -> Optional[tuple[FrameState, bool]]:
    """`(frame, is_real)` if this token is one of a frame's images, else None."""
    if token.kind != IMAGE:
        return None
    for frame_id, record in _images(state).items():
        if token.id in record.get("tokens", ()):
            frame = state.frames.get(frame_id)
            if frame is None:
                return None
            return frame, token.id == record.get("real")
    return None


def _image_spots(state: GameState, frame: FrameState) -> list[Pos]:
    """Two tiles for the fakes, chosen so no image is positionally special.

    Preferring a pair that is adjacent to each other as well as to the frame
    makes the three tiles mutually adjacent, so none of them is "the middle
    one" -- the geometry gives nothing away. Where the terrain leaves no such
    pair, any two free neighbours will do.
    """
    free = fx.free_tiles(state, frame.pos, 1)
    if len(free) < IMAGE_COUNT - 1 or state.board is None:
        return free[: IMAGE_COUNT - 1]
    pairs = [
        (a, b)
        for i, a in enumerate(free)
        for b in free[i + 1:]
        if state.board.distance(a, b) == 1
    ]
    chosen = state.rng.choice(pairs) if pairs else tuple(state.rng.sample(free, 2))
    return list(chosen)


def _effect_ephemeral_images(state: GameState, frame: FrameState, uid: str):
    if frame.pos is None:
        return None
    _clear_images(state, frame)
    spots = _image_spots(state, frame)
    if not spots:
        state.note(f"{frame.id} has no room to project its images")
        return None
    # The frame's own tile is index 0 and is the real one; shuffling the order
    # the tokens are *created* in means their ids carry no hint of which.
    positions = [frame.pos] + spots
    order = list(range(len(positions)))
    state.rng.shuffle(order)
    ids: list[str] = []
    real_id = ""
    for index in order:
        token = fx.spawn_token(
            state, IMAGE, positions[index], hp=1, owner=frame.seat
        )
        ids.append(token.id)
        if index == 0:
            real_id = token.id
    _images(state)[frame.id] = {
        "real": real_id,
        "tokens": ids,
        "at": [frame.pos.x, frame.pos.y],
    }
    state.note(
        f"{frame.id} splits into {len(ids)} images -- only one of them is real"
    )
    return None


def _clear_images(state: GameState, frame: FrameState) -> Optional[dict]:
    """Take every image off the board and forget them. Returns the old record."""
    record = _images(state).pop(frame.id, None)
    if record is None:
        return None
    for token_id in record.get("tokens", ()):
        token = state.tokens.get(token_id)
        if token is not None:
            token.alive = False
            token.pos = None
    return record


def reveal_images(state: GameState, frame: FrameState, *, why: str = "") -> None:
    """The trick is over: the images go, the frame is a target again."""
    if _clear_images(state, frame) is None:
        return
    where = f" at ({frame.pos.x},{frame.pos.y})" if frame.pos else ""
    state.note(f"{frame.id}'s images fade{where}" + (f" -- {why}" if why else ""))


def strike_image(state: GameState, token: TokenState) -> bool:
    """Resolve an attack that landed on a *fake*. True if it was one."""
    found = image_owner(state, token)
    if found is None:
        return False
    frame, real = found
    if real:
        return False
    token.alive = False
    token.pos = None
    record = _images(state).get(frame.id)
    if record is not None:
        record["tokens"] = [t for t in record["tokens"] if t != token.id]
    state.note(f"one of {frame.id}'s images was a fake -- it flickers out")
    return True


def _image_moves(state: GameState, frame: FrameState) -> bool:
    """"These tokens use this frame's actions": every image walks, not just
    the one the frame is standing on.

    Called once the frame's own move is in, so the real image has already gone
    with it. Each fake is then offered the same budget from its own tile. True
    if a decision parked.
    """
    record = _images(state).get(frame.id)
    if record is None:
        return False
    # The frame walked of its own accord, so `sync_images` must not now drag
    # the fakes along behind it as well -- they get their own move below.
    if frame.pos is not None:
        record["at"] = [frame.pos.x, frame.pos.y]
    from . import keywords as kw

    res = state.resolution
    card = state.catalogue.get(state.cards[res.uid].key) if res is not None else None
    if card is None:
        return False
    budget = kw.movement_budget(state, frame, card)
    if budget <= 0:
        return False
    record["walking"] = [
        token_id for token_id in record.get("tokens", ())
        if token_id != record.get("real")
    ]
    return _next_image_move(state, frame, budget)


def _next_image_move(state: GameState, frame: FrameState, budget: int) -> bool:
    """Ask the next fake still owed a move. True if a decision parked."""
    from . import keywords as kw

    record = _images(state).get(frame.id)
    while record is not None and record.get("walking"):
        token_id = str(record["walking"][0])
        token = state.tokens.get(token_id)
        if token is None or not token.alive or token.pos is None:
            record["walking"].pop(0)
            continue
        options = state.walk_options(token, budget, flying=kw.is_flying(frame))
        if len(options) <= 1:
            record["walking"].pop(0)
            continue
        state.pending = _ask(
            state,
            "image_move",
            seat=frame.seat,
            frame_id=frame.id,
            # Deliberately anonymous: naming which image is being moved would
            # say nothing, but numbering them across a turn would.
            prompt=f"Move an image of {frame.id} (up to {budget})",
            options=options,
            ctx={"token": token_id, "budget": budget},
            pick_kind="move",
        )
        return True
    if record is not None:
        record.pop("walking", None)
    return False


def _choice_image_move(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    record = _images(state).get(frame.id)
    token = state.tokens.get(str(ctx.get("token")))
    if record is not None and record.get("walking"):
        record["walking"] = [
            t for t in record["walking"] if t != str(ctx.get("token"))
        ]
    if token is not None and token.alive:
        token.pos = Pos(int(choice["x"]), int(choice["y"]))
        state.note(f"an image of {frame.id} moves")
    _next_image_move(state, frame, int(ctx.get("budget", 0)))


#: Tokens that reach past their own tile, and how far. The client draws the
#: area so a player can see they are standing in it -- a gravity well silently
#: re-pricing every step away from it is the whole reason this exists. The
#: prose is the engine's, like a hazard tile's, because the rule is.
TOKEN_AURAS: Mapping[str, tuple[int, str, str]] = {
    fx.GRAVITY_WELL: (
        GRAVITY_RADIUS,
        "gravity well",
        f"every step away from it costs {GRAVITY_PENALTY} extra movement",
    ),
    fx.STORM: (
        STORM_RADIUS,
        "psychic storm",
        "every unit in it takes 1 energy High at the end of each turn",
    ),
    fx.REBOUND: (
        REBOUND_RADIUS,
        "rebound",
        "its owner can see every enemy in it, if it can see the mirror",
    ),
}


def token_aura(kind: str) -> Optional[tuple[int, str, str]]:
    """`(radius, name, what it does)` for a token that reaches past its tile."""
    return TOKEN_AURAS.get(kind)


def images_dealt_damage(
    state: GameState,
    attacker: FrameState,
    card: Card,
    target_pos: Optional[Pos],
    defender: Optional[FrameState] = None,
) -> None:
    """"the fakes are removed ... if they would deal damage".

    All three images made this attack. Only one of them can actually hurt
    anything, so every fake whose own copy of it reached what was hit is
    revealed for what it is and removed. A fake that could not have reached is
    left alone: it swung at nothing and gave nothing away.
    """
    from . import combat

    record = _images(state).get(attacker.id)
    if record is None or target_pos is None or not card.is_attack:
        return
    for token_id in list(record.get("tokens", ())):
        if token_id == record.get("real"):
            continue
        token = state.tokens.get(token_id)
        if token is None or not token.alive or token.pos is None:
            continue
        would_hit = combat.zones_in_range(
            state, attacker, card, target_pos, defender, origin=token.pos
        ) and combat.can_target(
            state, attacker, card, target_pos, defender, origin=token.pos
        )
        if would_hit:
            token.alive = False
            token.pos = None
            record["tokens"] = [t for t in record["tokens"] if t != token_id]
            state.note(
                f"an image of {attacker.id} struck and dealt nothing -- "
                f"it flickers out"
            )


def sync_images(state: GameState) -> None:
    """Keep the real image under the frame, and end the trick when it must.

    Called from the engine's advance loop rather than from each place a frame
    can move, because frames are moved by movement, knockback, Teleport, Ace
    Reflexes and portals, and the tile the frame is standing on *is* the real
    image's tile.

    Nothing is dragged. Each image is its own piece: it moves on the card's
    movement step, or because something moved it by name (a Displace aimed at
    that image). Sliding the fakes after the frame would be the wrong shape now
    that they can be individually targeted -- and it is not needed to keep the
    frame hidden, because the pieces are indistinguishable whichever one moved.
    """
    records = _images(state)
    for frame_id in list(records):
        record = records[frame_id]
        frame = state.frames.get(frame_id)
        if frame is None or not frame.alive or frame.pos is None:
            if frame is not None:
                _clear_images(state, frame)
            else:
                records.pop(frame_id, None)
            continue
        live = [
            t for t in record["tokens"]
            if (state.tokens.get(t) is not None and state.tokens[t].alive)
        ]
        record["tokens"] = live
        # One image left is no concealment at all -- and it is the real one,
        # because a fake is removed the moment it is attacked.
        if record["real"] not in live or len(live) < 2:
            reveal_images(state, frame, why="nothing left to hide behind")
            continue
        record["at"] = [frame.pos.x, frame.pos.y]
        state.tokens[record["real"]].pos = frame.pos


# --------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------

EFFECT_STEPS: Mapping[str, EffectFn] = {
    # weapons, basics, boosters and faction frame cards
    "Basic_Dodge": _effect_dodge,
    "Frame_Bio-regen": _effect_repair,
    "Frame_Shield": _effect_shield,
    "Frame_Call of Nature": _effect_call_of_nature,
    ACCELERATE: _effect_accelerate,
    JUMP: _effect_jump,
    BOOMERANG: _effect_boomerang,
    # Bruiser
    RELENTLESS: _effect_relentless,
    INTIMIDATE: _effect_intimidate,
    NET_STRENGTH: _effect_net_strength,
    LOCKDOWN: _effect_target_status,
    BIND: _effect_bind,
    SUPLEX: _effect_suplex,
    # Mystic
    TELEPORT: _effect_teleport,
    UTTER_DARKNESS: _effect_utter_darkness,
    ENCODE: _effect_encode,
    EPHEMERAL: _effect_ephemeral_images,
    PSYCHIC_STORM: _effect_psychic_storm,
    DOOM: _effect_doom,
    # Tactician
    BROADCAST: _effect_allies_status,
    FOG_OF_WAR: _effect_fog_of_war,
    SET_THE_TRAP: _effect_set_the_trap,
    OUTFOX: _effect_target_status,
    DISPLACE: _effect_displace,
    ENNERVATE: _effect_allies_status,
    # Wunderkid
    HYPER: _effect_hyper,
    NET_SPEED: _effect_self_status,
    PORTAL: _effect_portal,
    ACE_REFLEXES: _effect_ace_reflexes,
    PARALLEL_ACTION: _effect_parallel_action,
    SHOWBOATING: _effect_showboating,
    # Engineer
    REPAIRS: _effect_battlefield_repairs,
    BARRICADE: _effect_barricade,
    GRAVITY_WELL: _effect_gravity_well,
    PRECISION_TUNING: _effect_self_status,
    SYSTEM_OVERRIDE: _effect_system_override,
    SENSORY_OVERLOAD: _effect_sensory_overload,
    # Specialist
    COMBO_STRIKE: _effect_combo_strike,
    SNIPERS_AIM: _effect_snipers_aim,
    MASTER_DUELIST: _effect_master_duelist,
    PRACTICED: _effect_practiced_technique,
    REBOUND: _effect_rebound,
    CAGE_FIGHT: _effect_cage_fight,
    # Drone cards are not listed here one by one -- see `_effect_handler`.
}


def _effect_handler(card: Card) -> Optional[EffectFn]:
    """The effect step for a card, or `None` if it has no implemented one.

    Every drone card summons, and `_effect_summon_drone` reads the count and
    the placement reach off the printed text -- so drones are matched on their
    *type* rather than listed key by key, and a new one in `Drone actions.csv`
    works without an engine change. That is not true of anything else: a pilot
    card's text is its own, and an unlisted one is deferred on purpose.
    """
    handler = EFFECT_STEPS.get(card.key)
    if handler is not None:
        return handler
    return _effect_summon_drone if card.card_type == "drone" else None

#: Effect-choice handlers keyed by card, for effects whose decision is raised
#: straight out of the effect step and answered with no extra context.
EFFECT_CHOICES: Mapping[str, Callable[[GameState, FrameState, Mapping], None]] = {}

#: Handlers for the decisions `_ask` raises, keyed by the name given there.
CHOICE_HANDLERS: Mapping[str, ChoiceFn] = {
    "intimidate": _choice_intimidate,
    "target_status": _choice_target_status,
    "bind": _choice_bind,
    "psychic_storm": _choice_psychic_storm,
    "doom": _choice_doom,
    "parallel_out": _choice_parallel_out,
    "parallel_in": _choice_parallel_in,
    "system_override": _choice_system_override,
    "rebound": _choice_rebound,
    "cage_fight": _choice_cage_fight,
    "shove_frame": _choice_shove_frame,
    "shove_to": _choice_shove_to,
    "repairs": _choice_repairs,
    "barricade": _choice_barricade,
    "gravity_well": _choice_gravity_well,
    "portal": _choice_portal,
    "summon_drone": _choice_summon_drone,
    "combo": _choice_combo,
}


def resolve_effect(
    state: GameState, frame: FrameState, uid: str
) -> Optional[PendingDecision]:
    """Run a card's effect step. Returns a decision if the effect needs one."""
    card = state.card(uid)
    rider = _armed_rider(state, frame, card)
    if rider == "combo":
        decision = _combo_decision(state, frame, uid)
        if decision is not None:
            return decision
    handler = _effect_handler(card)
    if handler is not None:
        return handler(state, frame, uid)
    if effect_kind(card) == "deferred":
        state.note(f"[not implemented] {card.key}: {card.text}")
    return None


def apply_effect_choice(state: GameState, frame: FrameState, uid: str,
                        choice: Mapping) -> None:
    """Feed one `effect_choice` answer back to whichever effect asked for it."""
    record = fx.bag(state).pop("await", None)
    if record:
        handler = CHOICE_HANDLERS.get(str(record.get("handler")))
        if handler is not None:
            handler(state, frame, dict(choice), dict(record.get("ctx") or {}))
        return
    if uid in state.cards:
        legacy = EFFECT_CHOICES.get(state.card(uid).key)
        if legacy is not None:
            legacy(state, frame, choice)


# --------------------------------------------------------------------------
# Passive hooks -- the attack pipeline asks these
# --------------------------------------------------------------------------


def grants_guard_break(state: GameState, attacker: FrameState) -> bool:
    """Net Strength: "all attacks you make this turn and next gain guard break"."""
    return fx.card_active(state, attacker, NET_STRENGTH)


def ignores_obstacles(state: GameState, attacker: FrameState) -> bool:
    """Snipers aim: "until the end of next turn: ranged attacks ignore obstacles"."""
    return fx.card_active(state, attacker, SNIPERS_AIM)


def is_untargetable(
    state: GameState,
    attacker: FrameState,
    card: Card,
    defender: FrameState,
) -> bool:
    """Whether an effect currently forbids this attack on this frame.

    * **Utter darkness** -- "while any frame is within 5 of this frame it
      cannot be attacked". Read as a bubble: everything inside it is hidden,
      friend and foe alike, which is the only reading under which the card's
      own frame is protected too.
    * **Fog of war** -- allies within 7 of the Tactician cannot be *ranged*.
    * **Ephemeral Images** -- the frame is not on the table as far as an
      attacker is concerned; its three images are what may be aimed at.
    """
    if defender.pos is None:
        return False
    if is_cloaked(state, defender):
        return True
    for mystic in fx.any_frame_with(
        state, UTTER_DARKNESS, this_turn=False
    ):
        gap = fx.distance(state, mystic.pos, defender.pos)
        if gap is not None and gap <= DARKNESS_RADIUS:
            return True
    if card.is_ranged:
        if defender.turn_flags.get("untargetable_ranged"):
            return True
        for tactician in fx.any_frame_with(
            state, FOG_OF_WAR, seat=defender.seat, this_turn=False
        ):
            reach = _reach_from_text(state.catalogue[FOG_OF_WAR].text, 7)
            gap = fx.distance(state, tactician.pos, defender.pos)
            if gap is not None and gap <= reach:
                return True
    return False


def on_attack_declared(
    state: GameState, attacker: FrameState, card: Card, attack: AttackInProgress
) -> None:
    """Fires once an attack has been built, before any block decision."""
    if grants_guard_break(state, attacker) and not attack.guard_break:
        attack.guard_break = True
        state.note(f"Net Strength gives {card.key} Guard Break")
    if not card.is_ranged and fx.card_active(state, attacker, MASTER_DUELIST):
        for target in attack.targets:
            if target.kind != "frame":
                continue
            defender = state.frames.get(target.id)
            if defender is not None and defender.seat != attacker.seat:
                _apply_statuses(state, defender, [("revealed", 1)])


def block_chooser(
    state: GameState, defender: FrameState, attack: AttackInProgress
) -> Team:
    """Which seat picks the blocking card. Normally the defender's."""
    attacker = state.frames.get(attack.attacker_id)
    if attacker is None or attacker.seat == defender.seat:
        return defender.seat
    card = state.catalogue[state.cards[attack.uid].key]
    if not card.is_ranged and fx.card_active(state, attacker, MASTER_DUELIST):
        return attacker.seat
    return defender.seat


def after_attacked(
    state: GameState, defender: FrameState, attacker: Optional[FrameState]
) -> None:
    """What being attacked sets off: Ace Reflexes, and Parallel Action."""
    if not defender.alive:
        return
    if _parallel_armed(state, defender):
        # "...or is attacked". An attack cannot stop half way to ask a
        # question, so this only marks it; `followup_decision` draws the hand
        # once the attack has finished.
        defender.turn_flags["parallel_now"] = True
    if not fx.card_active(state, defender, ACE_REFLEXES, later_turns=False):
        return
    owed = int(defender.turn_flags.get("reflex_moves", 0))
    defender.turn_flags["reflex_moves"] = min(4, owed + 1)


def taunting(state: GameState, attacker: FrameState) -> list[str]:
    """Frames the attacker must swing at if it can: Showboating, this turn."""
    return [
        f.id for f in fx.any_frame_with(
            state, SHOWBOATING, later_turns=False
        )
        if f.seat != attacker.seat and f.alive
    ]


def forced_targets(
    state: GameState,
    attacker: FrameState,
    options: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Cut an attack's target list down to what a taunt leaves it.

    "Any frame that is able to must attack this frame": if the showboater is
    in the list at all, it is the only thing in it -- tokens included, since
    shooting a reactor instead would be exactly the dodge the card forbids.
    """
    wanted = set(taunting(state, attacker))
    if not wanted:
        return [dict(o) for o in options]
    forced = [dict(o) for o in options
              if o.get("kind") == "frame" and str(o.get("id")) in wanted]
    return forced or [dict(o) for o in options]


def blocks_are_kept(state: GameState, defender: FrameState) -> bool:
    """Showboating: "this frame's blocks are not discarded"."""
    return fx.card_active(state, defender, SHOWBOATING, later_turns=False)


def range_cap(state: GameState, attacker: FrameState) -> Optional[int]:
    """The most a jammed frame's ranged attacks can reach (Sensory Overload)."""
    cap = int(attacker.turn_flags.get("range_cap", 0))
    return cap if cap > 0 else None


# --------------------------------------------------------------------------
# Planning hooks
# --------------------------------------------------------------------------


def commit_pool(state: GameState, frame: FrameState) -> list[str]:
    """Which cards this frame may commit from. Normally just its hand."""
    if fx.slot(state, "encode").get(frame.id) == state.turn:
        return list(frame.hand) + list(frame.deck)
    return list(frame.hand)


def actions_to_commit(state: GameState, frame: FrameState) -> int:
    """The most actions this frame may commit this turn. Hyper adds one."""
    extra = 1 if fx.card_active(state, frame, HYPER, this_turn=False) else 0
    return ACTIONS_PER_TURN + extra


# --------------------------------------------------------------------------
# Movement hooks
# --------------------------------------------------------------------------


def _gravity_penalty(state: GameState, start: Pos, dest: Pos) -> int:
    """Extra movement for stepping away from a gravity well.

    "An extra 1 movement penalty to any step away from it within 5." On a grid
    where range is Chebyshev, a route from distance `a` to distance `b` takes
    at least `b - a` steps away, so the cheapest route pays exactly that --
    counted only over the part of the trip inside the well's radius.
    """
    total = 0
    for well in fx.tokens_of_kind(state, fx.GRAVITY_WELL):
        near = fx.distance(state, well.pos, start)
        far = fx.distance(state, well.pos, dest)
        if near is None or far is None:
            continue
        away = min(GRAVITY_RADIUS, far) - min(GRAVITY_RADIUS, near)
        if away > 0:
            total += GRAVITY_PENALTY * away
    return total


def _portal_pairs(state: GameState) -> list[tuple[Pos, Pos]]:
    out: list[tuple[Pos, Pos]] = []
    for a_id, b_id in fx.slot(state, "portals").items():
        a = state.tokens.get(a_id)
        b = state.tokens.get(str(b_id))
        if a is None or b is None or not (a.alive and b.alive):
            continue
        if a.pos is None or b.pos is None or a.pos == b.pos:
            continue
        out.append((a.pos, b.pos))
    return out


def adjust_move_options(
    state: GameState,
    frame: FrameState,
    budget: int,
    options: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Re-cost and extend a movement offer for the effects on the board."""
    if frame.pos is None or state.board is None:
        return [dict(o) for o in options]
    if is_bound(state, frame):
        # Bind: "as long as that frame is adjacent it cannot move". Staying
        # put is still an answer, so the tile it is on is what it is offered.
        state.note(f"{frame.id} is held and cannot move")
        return [{"x": frame.pos.x, "y": frame.pos.y, "cost": 0}]
    costs: dict[Pos, int] = {}
    for option in options:
        pos = Pos(int(option["x"]), int(option["y"]))
        cost = int(option.get("cost", 0)) + _gravity_penalty(state, frame.pos, pos)
        if cost <= budget and cost < costs.get(pos, 10 ** 6):
            costs[pos] = cost
    costs.setdefault(frame.pos, 0)

    for near, far in _portal_pairs(state):
        for entry, exit_ in ((near, far), (far, near)):
            entered = costs.get(entry)
            if entered is None or entered + 1 > budget:
                continue
            if exit_ not in costs or costs[exit_] > entered + 1:
                costs[exit_] = entered + 1
            left = budget - entered - 1
            if left <= 0:
                continue
            onward = state.board.reachable(
                exit_,
                left,
                occupied=state.move_blockers(frame),
                flying="flying" in frame.spec.keywords,
            )
            for pos, cost in onward.items():
                total = entered + 1 + cost + _gravity_penalty(state, exit_, pos)
                if total <= budget and total < costs.get(pos, 10 ** 6):
                    costs[pos] = total

    # Coming out of a portal is still ending a move, so it lands under the
    # same rule as walking there: through a unit, never onto one.
    taken = state.unit_tiles(exclude=frame.id)
    return [
        {"x": pos.x, "y": pos.y, "cost": cost}
        for pos, cost in sorted(costs.items(), key=lambda kv: (kv[0].y, kv[0].x))
        if pos == frame.pos or pos not in taken
    ]


def after_move(
    state: GameState, frame: FrameState, old: Optional[Pos], new: Optional[Pos]
) -> None:
    """The engine's one "a frame finished moving" seam.

    Ephemeral Images keys off it: the frame has just taken the move its card
    granted, and each of its fakes is owed the same one from its own tile.
    Portal used to key off it too -- it read "create a portal at the start and
    end of this move", so the pair could only be known once the frame had
    walked -- but the card now names its own two tiles.
    """
    _image_moves(state, frame)


def after_card_resolved(state: GameState, frame: FrameState, uid: str) -> None:
    """What a finished card sets off for the ones after it."""
    owed = int(frame.turn_flags.pop("movement_owed", 0))
    if owed:
        # "Other actions": the card that granted this has now finished, so
        # everything still to come is an other action and nothing that has
        # already moved is charged for it.
        frame.turn_flags["movement_bonus"] = (
            int(frame.turn_flags.get("movement_bonus", 0)) + owed
        )
    # Relentless Assault: wind an action back so it resolves a second time.
    if not frame.turn_flags.get("repeat_actions"):
        return
    inst = state.cards.get(uid)
    if inst is None or inst.location != "committed":
        return
    if inst.key == RELENTLESS:
        return
    done = frame.turn_flags.setdefault("repeated", [])
    if uid in done:
        return
    done.append(uid)
    inst.init_index = max(0, inst.init_index - 1)
    state.note(f"{inst.key} resolves again (Relentless Assault)")


def end_of_turn(state: GameState) -> None:
    """Card effects that collect at the end of every turn.

    Called from `resolve.cleanup_phase` beside the terrain hazards and before
    the objectives are counted, for the same reason: a frame the storm kills
    is not also holding the ground it died on.
    """
    _storm_step(state)
    _doom_step(state)


# --------------------------------------------------------------------------
# Follow-ups: things that happen between cards
# --------------------------------------------------------------------------


def _highest_initiative(state: GameState) -> Optional[int]:
    """The initiative of the next card still waiting to act, if any."""
    from . import keywords as kw

    best: Optional[int] = None
    for frame in state.frames.values():
        if not frame.alive:
            continue
        for uid in frame.committed:
            inst = state.cards.get(uid)
            if inst is None or inst.location != "committed" or inst.is_echo:
                continue
            card = state.catalogue[inst.key]
            if inst.init_index >= len(card.initiative):
                continue
            value = kw.effective_initiative(state, frame, card, inst.init_index)
            best = value if best is None else max(best, value)
    return best


def followup_decision(state: GameState) -> bool:
    """Offer anything owed between card resolutions. True if a decision parked.

    Called by the driver whenever it is about to pick the next card to
    resolve, which is the only moment at which "after the attack" and "at
    initiative 4" are both well defined.
    """
    if state.phase != "action" or state.pending is not None:
        return False
    _refresh_revealed(state)
    _retire_dead_drones(state)
    if _parallel_step_due(state):
        return True
    if _reflex_step(state):
        return True
    top = _highest_initiative(state)
    if _drone_step(state, top):
        return True
    return False


# -- Parallel Action -------------------------------------------------------


def _parallel_step_due(state: GameState) -> bool:
    """Fire Parallel Action if its frame is about to act, or has been hit."""
    from . import resolve as _resolve

    armed = fx.slot(state, "parallel")
    if not armed:
        return False
    for frame_id in list(armed):
        frame = state.frames.get(frame_id)
        if frame is None or not frame.alive:
            armed.pop(frame_id, None)
            continue
        if frame.turn_flags.get("parallel_now") and _parallel_ready(state, frame):
            return _parallel_fire(state, frame)
    nxt = _resolve.peek_actor(state)          # looking must not advance the tie
    if nxt is None:
        return False
    actor, _uid = nxt
    if _parallel_ready(state, actor):
        return _parallel_fire(state, actor)
    return False


def _parallel_ready(state: GameState, frame: FrameState) -> bool:
    """Armed, in date, and with something left to change.

    A frame whose last face-down action was just spent blocking has nothing to
    swap, and drawing a hand to stare at would burn the card for nothing --
    which is the whole thing the "Next turn:" delay is there to prevent. So it
    stays armed and waits for a trigger it can actually use.
    """
    return _parallel_armed(state, frame) and bool(_swappable(state, frame))


# -- Ace Reflexes ----------------------------------------------------------


def _reflex_step(state: GameState) -> bool:
    for frame in state.frames.values():
        if not frame.alive or int(frame.turn_flags.get("reflex_moves", 0)) <= 0:
            continue
        frame.turn_flags["reflex_moves"] = int(frame.turn_flags["reflex_moves"]) - 1
        if frame.pos is None or state.board is None:
            continue
        from . import keywords as kw

        options = state.walk_options(frame, 2, flying=kw.is_flying(frame))
        if len(options) <= 1:
            continue
        state.pending = _ask(
            state,
            "reposition",
            seat=frame.seat,
            frame_id=frame.id,
            prompt=f"Ace Reflexes: move {frame.id} up to 2",
            options=options,
            pick_kind="move",
        )
        return True
    return False


def _choice_reposition(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    _move_frame(state, frame, Pos(int(choice["x"]), int(choice["y"])))


# -- Drones ----------------------------------------------------------------


def drone_name(state: GameState, token_id: str) -> str:
    """What to call a drone in the log: whose it is, and which card made it.

    A frame can have more than one drone out, and both sides can have Swarms,
    so "the drone moves" is as unhelpful as a bare model name would be for a
    frame -- the log names them the same way it names everything else.
    """
    record = fx.slot(state, "drones").get(token_id)
    if not record:
        return "a drone"
    owner = str(record.get("frame", ""))
    card = state.catalogue.get(str(record.get("key")))
    return f"{owner}'s {card.name if card else 'drone'}"


def _retire_dead_drones(state: GameState) -> None:
    """A drone whose token or summoner is gone stops acting."""
    drones = fx.slot(state, "drones")
    for token_id in list(drones):
        record = drones[token_id]
        token = state.tokens.get(token_id)
        summoner = state.frames.get(str(record.get("frame")))
        if token is None or not token.alive:
            drones.pop(token_id, None)
            continue
        if summoner is None or not summoner.alive:
            token.alive = False
            token.pos = None
            state.note(f"{drone_name(state, token_id)} shuts down with its frame")
            drones.pop(token_id, None)


def _drone_step(state: GameState, top: Optional[int]) -> bool:
    drones = fx.slot(state, "drones")
    for token_id, record in list(drones.items()):
        token = state.tokens.get(token_id)
        if token is None or not token.alive or token.pos is None:
            continue
        card = state.catalogue.get(str(record.get("key")))
        if card is None:
            continue
        if record.get("acted") == state.turn:
            continue
        if top is not None and top > card.initiative[0]:
            continue
        record["acted"] = state.turn
        if _drone_move(state, token_id, record):
            return True
        if _drone_attack(state, token_id, record):
            return True
    return False


def _drone_move(state: GameState, token_id: str, record: Mapping) -> bool:
    token = state.tokens[token_id]
    card = state.catalogue[str(record["key"])]
    summoner = state.frames.get(str(record.get("frame")))
    if summoner is None or state.board is None or token.pos is None:
        return False
    budget = max(0, card.drone_movement)
    if budget <= 0:
        return False
    # A drone is a unit, so it walks by the same rule a frame does: through
    # its own side, never onto anything that is already standing somewhere.
    # The cost matters to the client, which labels the tiles it offers.
    options = state.walk_options(token, budget)
    if len(options) <= 1:
        return False
    state.pending = _ask(
        state,
        "drone_move",
        seat=summoner.seat,
        frame_id=summoner.id,
        prompt=f"Move {drone_name(state, token_id)} (up to {budget})",
        options=options,
        ctx={"token": token_id},
        pick_kind="move",
    )
    return True


def _choice_drone_move(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    token_id = str(ctx.get("token"))
    token = state.tokens.get(token_id)
    if token is None or not token.alive:
        return
    token.pos = Pos(int(choice["x"]), int(choice["y"]))
    state.note(
        f"{drone_name(state, token_id)} moves to ({token.pos.x},{token.pos.y})"
    )
    record = fx.slot(state, "drones").get(token_id)
    if record is not None:
        _drone_attack(state, token_id, record)


def _drone_zones(
    state: GameState, token_id: str, card: Card, defender: FrameState
) -> dict[str, int]:
    """The zones the drone's copy of the card lands with, from its own tile."""
    return _drone_zones_at(state, token_id, card, defender.pos)


def _drone_zones_at(
    state: GameState, token_id: str, card: Card, target_pos: Optional[Pos]
) -> dict[str, int]:
    from . import combat

    token = state.tokens[token_id]
    if token.pos is None or target_pos is None or state.board is None:
        return {}
    defender_pos = target_pos
    gap = state.board.distance(token.pos, defender_pos)
    zones: dict[str, int] = {}
    for zone in ZONES:
        damage = card.attacks[zone]
        if damage <= 0:
            continue
        printed = card.ranges[zone]
        if printed <= 0:
            if gap == 1:
                zones[zone] = damage
        elif 1 < gap <= printed:
            try:
                clear = state.board.has_line_of_sight(
                    token.pos, defender_pos, occupied=state.occupied()
                )
            except Exception:
                clear = False
            if clear:
                zones[zone] = damage
    if zones and not card.is_ranged:
        delta = state.elevation(token.pos) - state.elevation(defender_pos)
        zones = combat.elevation_shift(zones, delta)
    return {z: d for z, d in zones.items() if d > 0}


def _drone_options(
    state: GameState, token_id: str, card: Card, summoner: FrameState
) -> list[dict]:
    """What this drone may shoot: enemy frames, their images, or an objective.

    A frame hiding behind Ephemeral Images is not a target -- but its images
    are, for a drone exactly as for a frame. Without this the card would be a
    total answer to drones, which is not what it says.

    Tokens can attack objectives: a drone shoots a reactor or the Tower the
    same way a frame does. Attackable tokens are offered by the same rule
    `combat.legal_targets` uses -- anything with hit points that is not the
    drone's own side's.
    """
    options: list[dict] = []
    for other in state.frames.values():
        if not other.alive or other.seat == summoner.seat:
            continue
        if is_cloaked(state, other):
            for image_id in image_tokens(state, other):
                image = state.tokens.get(image_id)
                if image is None or not image.alive:
                    continue
                if _drone_zones_at(state, token_id, card, image.pos):
                    options.append({"token": image_id, "name": "an image"})
            continue
        if not _drone_zones(state, token_id, card, other):
            continue
        if is_untargetable(state, summoner, card, other):
            continue
        options.append({"frame": other.id, "name": other.id})
    for target in state.tokens.values():
        if target.id == token_id or not target.attackable:
            continue
        if target.kind == IMAGE:
            continue                    # already offered, via its own frame
        if target.owner is not None and target.owner == summoner.seat:
            continue
        if _drone_zones_at(state, token_id, card, target.pos):
            options.append({"token": target.id, "name": target.kind})
    return options


def _drone_attack(state: GameState, token_id: str, record: Mapping) -> bool:
    token = state.tokens.get(token_id)
    card = state.catalogue[str(record["key"])]
    summoner = state.frames.get(str(record.get("frame")))
    if token is None or summoner is None or not card.is_attack:
        return False
    options = _drone_options(state, token_id, card, summoner)
    if not options:
        return False
    if len(options) == 1:
        return _drone_fire(state, token_id, record, options[0])
    state.pending = _ask(
        state,
        "drone_target",
        seat=summoner.seat,
        frame_id=summoner.id,
        prompt=f"Choose a target for {drone_name(state, token_id)}",
        options=options,
        ctx={"token": token_id},
    )
    return True


def _choice_drone_target(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    token_id = str(ctx.get("token"))
    record = fx.slot(state, "drones").get(token_id)
    if record is None:
        return
    _drone_fire(state, token_id, record, choice)


def _drone_fire(
    state: GameState, token_id: str, record: Mapping, choice: Mapping
) -> bool:
    """Send the drone's attack at whichever kind of target was picked."""
    image_id = choice.get("token")
    if image_id:
        image = state.tokens.get(str(image_id))
        if image is not None and image.kind != IMAGE:
            return _drone_strike_token(state, token_id, record, image)
        found = image_owner(state, image) if image is not None else None
        if found is None:
            return False
        frame, real = found
        if not real:
            state.note(
                f"{drone_name(state, token_id)} strikes at one of "
                f"{frame.id}'s images"
            )
            strike_image(state, image)
            return False
        reveal_images(state, frame, why="a drone found it")
        return _drone_declare(state, token_id, record, frame.id)
    return _drone_declare(state, token_id, record, str(choice.get("frame")))


def _drone_strike_token(
    state: GameState, token_id: str, record: Mapping, target: TokenState
) -> bool:
    """A drone shooting an objective -- a reactor, the Tower, a gang.

    Built as an ordinary attack and run through `_drone_resolve` like any
    other, so damage reduction and destruction are the same code that handles
    a frame shooting the same thing. Tokens never block, so nothing parks.
    """
    card = state.catalogue[str(record["key"])]
    zones = _drone_zones_at(state, token_id, card, target.pos)
    if not zones:
        return False
    attack = AttackInProgress(
        attacker_id=str(record["frame"]),
        uid=str(record["uid"]),
        via=drone_name(state, token_id),
        via_token=token_id,
    )
    attack.targets.append(AttackTarget("token", target.id, dict(zones)))
    state.note(f"{drone_name(state, token_id)} attacks the {target.kind}")
    return _drone_resolve(state, token_id, attack)


def _drone_declare(
    state: GameState, token_id: str, record: Mapping, target_id: str
) -> bool:
    """Build the drone's attack and run it through the ordinary block rules."""
    card = state.catalogue[str(record["key"])]
    defender = state.frames.get(target_id)
    if defender is None:
        return False
    zones = _drone_zones(state, token_id, card, defender)
    if not zones:
        return False
    attack = AttackInProgress(
        attacker_id=str(record["frame"]),
        uid=str(record["uid"]),
        guard_break="guardbreak" in card.keywords,
        feint="feint" in card.keywords,
        via=drone_name(state, token_id),
        via_token=token_id,
    )
    target = AttackTarget("frame", target_id, dict(zones))
    target.pending_zones = [z for z in ZONES if z in zones]
    attack.targets.append(target)
    state.note(f"{drone_name(state, token_id)} attacks {defender.id}")
    return _drone_resolve(state, token_id, attack)


def _drone_resolve(
    state: GameState, token_id: str, attack: AttackInProgress
) -> bool:
    """Work the drone's attack through its compulsory blocks. True if parked."""
    from . import combat

    while attack.current is not None:
        decision = combat.next_block_decision(state, attack)
        if decision is not None:
            zones, candidates = decision
            defender = state.frames[attack.current.id]
            fx.slot(state, "drone_attack")[token_id] = attack
            state.pending = _ask(
                state,
                "drone_block",
                seat=defender.seat,
                frame_id=defender.id,
                prompt=(
                    f"{defender.id} must block "
                    f"{'/'.join(zones)} (blocking is compulsory)"
                ),
                options=[
                    {"uid": uid, "key": state.cards[uid].key, "zones": list(zones)}
                    for uid in candidates
                ],
                ctx={"token": token_id, "zones": list(zones)},
            )
            return True
        combat.finish_target(state, attack)
        combat.advance_attack(state, attack)
    fx.slot(state, "drone_attack").pop(token_id, None)
    return False


def _choice_drone_block(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    from . import combat

    token_id = str(ctx.get("token"))
    attack = fx.slot(state, "drone_attack").get(token_id)
    if attack is None:
        return
    uid = str(choice.get("uid"))
    zones = [str(z) for z in ctx.get("zones", [])]
    block_card = state.card(uid)
    combat.apply_block(state, frame, attack, uid, zones)
    on_block(state, frame, block_card, state.frames.get(attack.attacker_id),
             via_token=attack.via_token)
    _drone_resolve(state, token_id, attack)


CHOICE_HANDLERS = dict(CHOICE_HANDLERS)
CHOICE_HANDLERS.update({
    "reposition": _choice_reposition,
    "drone_move": _choice_drone_move,
    "image_move": _choice_image_move,
    "image_teleport": _choice_image_teleport,
    "drone_target": _choice_drone_target,
    "drone_block": _choice_drone_block,
})


# --------------------------------------------------------------------------
# Attack riders
# --------------------------------------------------------------------------


def on_hit(
    state: GameState, attacker: FrameState, card: Card, defender: FrameState
) -> None:
    """`On Hit:` -- only applies when the attack was not blocked."""
    match = _ON_HIT_RE.search(card.text or "")
    if match:
        for kind, count in _parse_statuses(match.group(1)):
            apply_status(state, defender, kind, count)
    # Net Strength: "all attacks ... gain On Hit: target gets dazed".
    if fx.card_active(state, attacker, NET_STRENGTH):
        apply_status(state, defender, "dazed", 1)


def on_block(
    state: GameState,
    defender: FrameState,
    block_card: Card,
    attacker: Optional[FrameState],
    *,
    via_token: str = "",
) -> None:
    """`On Block:` -- fires when this card is spent blocking.

    Whatever the rider does, it does to *the thing that swung*. When a drone
    made the attack that is the drone, not the frame that summoned it: a Chain
    catching a Gun Tower's shot dazes the gun tower, and dazing a machine on
    the far side of the board because its owner's card is the one being
    resolved is not what the card says. A token carries no statuses
    (rules.tex: "they only have one health stat"), so a debuff simply fizzles
    against one; damage lands on it.
    """
    match = _ON_BLOCK_RE.search(block_card.text or "")
    if not match:
        return
    token = state.tokens.get(via_token) if via_token else None
    if token is None and attacker is None:
        return
    body = match.group(1)
    what = drone_name(state, token.id) if token is not None else ""
    for kind, count in _parse_statuses(body):
        if token is not None:
            state.note(f"{what} has no {kind} counter to take -- it is a token")
            continue
        assert attacker is not None
        apply_status(state, attacker, kind, count)
    # "On Block: deals mid <dtype>" -- Parry hits back for one Mid.
    hit = re.search(r"deals\s+(high|mid|low)", body, re.I)
    if hit:
        from .state import deal_damage

        zone = hit.group(1).capitalize()
        if token is not None:
            damage_token(state, token, 1)
            state.note(f"{block_card.key} strikes {what} back for 1")
            return
        assert attacker is not None
        deal_damage(state, attacker, zone, 1, source=defender)
        state.note(f"{block_card.key} strikes back for 1 {zone}")


def attack_damage_bonus(
    state: GameState, attacker: FrameState, card: Card, target_id: str
) -> tuple[dict[str, int], int]:
    """Extra attack marks from card text: `(per zone, every zone)`.

    Split because the rules treat the two differently. A card that names a
    zone adds there; a card that just says "+1 damage" adds to *every* zone
    the attack applies to (rules.tex "Damage reduction and increases"), which
    is the second half of the pair -- and which the caller applies to the
    zones that actually land, so a bonus never opens a zone the attack has no
    reach in.
    """
    bonus: dict[str, int] = {}
    spread = 0

    def add(zone: Optional[str], amount: int) -> None:
        if zone and amount:
            bonus[zone] = bonus.get(zone, 0) + amount

    match = _DIDNT_MOVE_RE.search(card.text or "")
    if match:
        target = state.frames.get(target_id)
        if target is not None and not target.moved_this_turn:
            spread += int(match.group(1))
            state.note(f"{card.key}: target has not moved, +{match.group(1)} damage")

    if card.is_ranged and fx.card_active(state, attacker, SNIPERS_AIM):
        extra = _extra_damage(state.catalogue.get(SNIPERS_AIM))
        spread += extra
        state.note(f"Snipers aim adds {extra} damage")

    if fx.card_active(state, attacker, PRACTICED, this_turn=False):
        # "for each other *completed* attack from the same weapon": the ones
        # that have already resolved. The card being resolved now is not one
        # of them -- `resolved` is set in `_finish_card`, after this runs --
        # so the count is already "other" and nothing is subtracted.
        same = sum(
            1 for uid in attacker.committed
            if state.cards[uid].resolved
            and state.catalogue[state.cards[uid].key].group == card.group
            and state.catalogue[state.cards[uid].key].is_attack
        )
        if same:
            spread += same
            state.note(f"Practiced Technique adds {same} damage")

    combo = fx.slot(state, "combo").get(attacker.id) or {}
    extra = combo.get("extra")
    if extra:
        for zone, amount in extra.items():
            add(zone, int(amount))
        # One attack, one added card: the next attack asks again.
        fx.slot(state, "combo").pop(attacker.id, None)

    return bonus, spread
