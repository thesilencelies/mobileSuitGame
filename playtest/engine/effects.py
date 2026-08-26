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
    discard_card,
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

RELENTLESS = "Bruiser_Relentless Assault"
INTIMIDATE = "Bruiser_Intimidate"
NET_STRENGTH = "Bruiser_Net Strength"
LOCKDOWN = "Bruiser_Lockdown"

EPHEMERAL = "Mystic_Ephemeral Images"
TELEPORT = "Mystic_Teleport"
UTTER_DARKNESS = "Mystic_Utter darkness"
ENCODE = "Mystic_Encode the future"

BROADCAST = "Tactician_Tactical broadcast"
FOG_OF_WAR = "Tactician_Fog of war"
SET_THE_TRAP = "Tactician_Set the trap"
OUTFOX = "Tactician_Outfox"

HYPER = "Wunderkid_Hyper"
NET_SPEED = "Wunderkid_Net Speed"
PORTAL = "Wunderkid_Portal"
ACE_REFLEXES = "Wunderkid_Ace Reflexes"

REPAIRS = "Engineer_Battlefield Repairs"
BARRICADE = "Engineer_Barricade"
GRAVITY_WELL = "Engineer_Gravity Well"
PRECISION_TUNING = "Engineer_Precision Tuning"

COMBO_STRIKE = "Specialist_Combo strike"
SNIPERS_AIM = "Specialist_Snipers aim"
MASTER_DUELIST = "Specialist_Master duelist"
PRACTICED = "Specialist_Practiced Technique"

#: Gravity Well's radius and per-step cost (from the card text).
GRAVITY_RADIUS = 5
GRAVITY_PENALTY = 1

#: Barricade tokens per card, and Utter darkness' radius.
BARRICADE_COUNT = 3
DARKNESS_RADIUS = 5

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
    )


def _frame_options(frames: Sequence[FrameState]) -> list[dict[str, Any]]:
    return [{"frame": f.id, "name": f.spec.name} for f in frames]


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
    frame.turn_flags["movement_bonus"] = (
        int(frame.turn_flags.get("movement_bonus", 0)) + bonus
    )
    state.note(f"{frame.id}'s other actions this turn get +{bonus} movement")
    return None


def _effect_call_of_nature(state: GameState, frame: FrameState, uid: str):
    """"Select an opposing frame within 6: move them 2".

    The rules do not say who chooses the direction; the controller does, so
    this is offered as a single `effect_choice` over (frame, destination).
    """
    card = state.card(uid)
    match = _SELECT_MOVE_RE.search(card.text)
    reach = int(match.group(1)) if match else 6
    steps = int(match.group(2)) if match else 2
    options = _shove_options(state, frame, reach, steps, side="enemy")
    if not options:
        return None
    return PendingDecision(
        kind="effect_choice",
        seat=frame.seat,
        prompt=f"{card.name}: move an opposing frame within {reach} up to {steps}",
        options=options,
        frame_id=frame.id,
    )


def _shove_options(
    state: GameState, frame: FrameState, reach: int, steps: int, *, side: str
) -> list[dict[str, Any]]:
    """(frame, destination) pairs for "move a frame within N up to M"."""
    if state.board is None or frame.pos is None:
        return []
    options: list[dict[str, Any]] = []
    for other in fx.frames_within(state, frame, reach, side=side):
        if other.pos is None:
            continue
        reachable = state.board.reachable(
            other.pos,
            steps,
            occupied=state.occupied(exclude=other.id),
            flying="flying" in other.spec.keywords,
        )
        for pos in sorted(reachable, key=lambda p: (p.y, p.x)):
            options.append(
                {"frame": other.id, "name": other.spec.name, "x": pos.x, "y": pos.y}
            )
    return options


def _move_frame(state: GameState, target: FrameState, pos: Pos) -> None:
    from . import objectives as objectivelib

    old = target.pos
    if pos == old:
        return
    target.pos = pos
    target.moved_this_turn = True
    objectivelib.on_move(state, target, old)
    state.note(f"{target.id} is moved to ({pos.x},{pos.y})")


def _apply_call_of_nature(state: GameState, frame: FrameState, choice: Mapping) -> None:
    target = state.frames.get(str(choice.get("frame")))
    if target is None:
        return
    _move_frame(state, target, Pos(int(choice["x"]), int(choice["y"])))


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
    frame.turn_flags["movement_bonus"] = (
        int(frame.turn_flags.get("movement_bonus", 0)) - 2
    )
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
    card = state.card(uid)
    reach = _reach_from_text(card.text, 3)
    targets = fx.frames_within(state, frame, reach, side="any")
    if not targets:
        return None
    if len(targets) == 1:
        _apply_statuses(state, targets[0], _parse_statuses(card.text))
        return None
    return _ask(
        state,
        "target_status",
        seat=frame.seat,
        frame_id=frame.id,
        prompt=f"{card.name}: choose a frame within {reach}",
        options=_frame_options(targets),
        ctx={"uid": uid},
    )


def _choice_target_status(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    target = state.frames.get(str(choice.get("frame")))
    if target is None:
        return
    card = state.catalogue[state.cards[str(ctx["uid"])].key]
    _apply_statuses(state, target, _parse_statuses(card.text))


# --------------------------------------------------------------------------
# Pilot cards -- Mystic
# --------------------------------------------------------------------------


def _effect_teleport(state: GameState, frame: FrameState, uid: str):
    state.note(
        f"{frame.id} charges a jump: next turn it repositions at initiative 4"
    )
    return None


def _effect_utter_darkness(state: GameState, frame: FrameState, uid: str):
    state.note(
        f"{frame.id} calls down darkness: next turn nothing within "
        f"{DARKNESS_RADIUS} of it can be attacked"
    )
    return None


def _effect_encode(state: GameState, frame: FrameState, uid: str):
    """"Next turn: one allied frame (includes this) chooses cards from their deck."

    Read as: next turn that frame picks its actions out of its whole deck
    instead of only the hand it drew. The choice of *which* ally is made now.
    """
    allies = fx.frames_within(state, frame, 10 ** 6, side="ally", include_self=True)
    if not allies:
        return None
    if len(allies) == 1:
        _arm_encode(state, allies[0])
        return None
    return _ask(
        state,
        "encode",
        seat=frame.seat,
        frame_id=frame.id,
        prompt="Encode the future: which ally chooses from its deck next turn?",
        options=_frame_options(allies),
    )


def _arm_encode(state: GameState, target: FrameState) -> None:
    fx.slot(state, "encode")[target.id] = state.turn + 1
    state.note(f"{target.id} will choose next turn's actions from its deck")


def _choice_encode(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    target = state.frames.get(str(choice.get("frame")))
    if target is not None:
        _arm_encode(state, target)


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
    options = _shove_options(state, frame, 5, 2, side="ally")
    if not options:
        return None
    return _ask(
        state,
        "set_the_trap",
        seat=frame.seat,
        frame_id=frame.id,
        prompt="Set the trap: move an allied frame within 5 up to 2",
        options=options,
    )


def _choice_set_the_trap(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    target = state.frames.get(str(choice.get("frame")))
    if target is None:
        return
    _move_frame(state, target, Pos(int(choice["x"]), int(choice["y"])))
    for enemy in fx.frames_within(state, target, 3, side="enemy"):
        _apply_statuses(state, enemy, [("revealed", 1)])


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
    tiles = [p for p in fx.free_tiles(state, frame.pos, reach) if p != first]
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
        options=_frame_options(allies),
        ctx={"amount": amount},
    )


def _choice_repairs(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    target = state.frames.get(str(choice.get("frame")))
    if target is not None:
        repair(state, target, int(ctx.get("amount", 3)))


def _barricade_step(
    state: GameState, frame: FrameState, reach: int, left: int
) -> Optional[PendingDecision]:
    if left <= 0:
        return None
    tiles = fx.free_tiles(state, frame.pos, reach)
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
    tiles = fx.free_tiles(state, frame.pos, reach)
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
    )


def _place_well(state: GameState, frame: FrameState, pos: Pos) -> None:
    fx.spawn_token(state, fx.GRAVITY_WELL, pos, owner=frame.seat)
    state.note(f"a gravity well opens at ({pos.x},{pos.y})")


def _choice_gravity_well(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    _place_well(state, frame, Pos(int(choice["x"]), int(choice["y"])))


# --------------------------------------------------------------------------
# Pilot cards -- Specialist
# --------------------------------------------------------------------------


def _effect_combo_strike(state: GameState, frame: FrameState, uid: str):
    """Arm the rider; the choice is made when the next attack resolves."""
    fx.slot(state, "combo")[frame.id] = {"armed": True}
    state.note(f"{frame.id} lines up a combo on its next attack")
    return None


def _armed_rider(
    state: GameState, frame: FrameState, card: Card
) -> Optional[str]:
    """The name of an effect owed by `frame` that fires on `card`, if any."""
    if (
        card.is_attack
        and not delegates_attack(card)
        and fx.slot(state, "combo").get(frame.id, {}).get("armed")
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
    fx.slot(state, "combo")[frame.id] = {"armed": False}
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
        "armed": False,
        "extra": {z: card.attacks[z] for z in ZONES if card.attacks[z] > 0},
    }
    discard_card(state, uid)
    state.note(f"Combo strike adds {card.key}'s attack")


def _effect_snipers_aim(state: GameState, frame: FrameState, uid: str):
    frame.turn_flags["range_bonus"] = int(frame.turn_flags.get("range_bonus", 0)) + 4
    frame.turn_flags["snipers_aim"] = True
    state.note(
        f"{frame.id} takes aim: ranged attacks this turn ignore obstacles, "
        f"reach 4 further and deal 1 extra damage"
    )
    return None


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
        for pos in fx.free_tiles(state, frame.pos, reach)
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
# frame. All tokens move as this frame and the fakes are removed if attacked."
#
# The frame does not actually leave the board -- it stands on one of the three
# tiles, and that tile carries the *real* image. What changes is what an enemy
# may aim at: while the images are up the frame itself is not a legal target,
# and its three images are. Striking the real one is an ordinary attack on the
# frame (which then has to block as usual, and is revealed by the hit);
# striking a fake removes it and nothing else.
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


def _reposition_image(
    state: GameState, token: TokenState, frame: FrameState, wanted: Optional[Pos]
) -> None:
    """Put a fake back beside the frame after it has moved."""
    board = state.board
    if board is None or frame.pos is None:
        return
    taken = state.occupied() | {
        t.pos for t in state.tokens.values()
        if t.alive and t.pos is not None and t.id != token.id
    }

    def usable(pos: Optional[Pos]) -> bool:
        if pos is None or not board.in_bounds(pos):
            return False
        tile = board.tile(pos)
        return not tile.impassable and not tile.obstacle and pos not in taken

    if usable(wanted):
        token.pos = wanted
        return
    free = [p for p in fx.free_tiles(state, frame.pos, 1) if p != token.pos]
    if free:
        token.pos = state.rng.choice(free)


def sync_images(state: GameState) -> None:
    """"All tokens move as this frame" -- and the trick ends when it must.

    Called from the engine's advance loop rather than from each place a frame
    can move, because frames are moved by movement, knockback, Teleport, Ace
    Reflexes and portals, and an image left behind by any one of those would
    quietly give the frame away.
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
        at = record.get("at") or [frame.pos.x, frame.pos.y]
        old = Pos(int(at[0]), int(at[1]))
        if frame.pos != old:
            dx, dy = frame.pos.x - old.x, frame.pos.y - old.y
            for token_id in live:
                if token_id == record["real"]:
                    continue
                token = state.tokens[token_id]
                here = token.pos
                wanted = Pos(here.x + dx, here.y + dy) if here else None
                _reposition_image(state, token, frame, wanted)
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
    "Booster_Accelerate": _effect_accelerate,
    # Bruiser
    RELENTLESS: _effect_relentless,
    INTIMIDATE: _effect_intimidate,
    NET_STRENGTH: _effect_net_strength,
    LOCKDOWN: _effect_target_status,
    # Mystic
    TELEPORT: _effect_teleport,
    UTTER_DARKNESS: _effect_utter_darkness,
    ENCODE: _effect_encode,
    EPHEMERAL: _effect_ephemeral_images,
    # Tactician
    BROADCAST: _effect_allies_status,
    FOG_OF_WAR: _effect_fog_of_war,
    SET_THE_TRAP: _effect_set_the_trap,
    OUTFOX: _effect_target_status,
    # Wunderkid
    HYPER: _effect_hyper,
    NET_SPEED: _effect_self_status,
    PORTAL: _effect_portal,
    ACE_REFLEXES: _effect_ace_reflexes,
    # Engineer
    REPAIRS: _effect_battlefield_repairs,
    BARRICADE: _effect_barricade,
    GRAVITY_WELL: _effect_gravity_well,
    PRECISION_TUNING: _effect_self_status,
    # Specialist
    COMBO_STRIKE: _effect_combo_strike,
    SNIPERS_AIM: _effect_snipers_aim,
    MASTER_DUELIST: _effect_master_duelist,
    PRACTICED: _effect_practiced_technique,
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
EFFECT_CHOICES: Mapping[str, Callable[[GameState, FrameState, Mapping], None]] = {
    "Frame_Call of Nature": _apply_call_of_nature,
}

#: Handlers for the decisions `_ask` raises, keyed by the name given there.
CHOICE_HANDLERS: Mapping[str, ChoiceFn] = {
    "intimidate": _choice_intimidate,
    "target_status": _choice_target_status,
    "encode": _choice_encode,
    "set_the_trap": _choice_set_the_trap,
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
    """Snipers aim: "ranged attacks this turn can ignore obstacles"."""
    return bool(attacker.turn_flags.get("snipers_aim"))


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
    """Ace Reflexes: "whenever this frame is attacked this turn, move 2"."""
    if not defender.alive:
        return
    if not fx.card_active(state, defender, ACE_REFLEXES, later_turns=False):
        return
    owed = int(defender.turn_flags.get("reflex_moves", 0))
    defender.turn_flags["reflex_moves"] = min(4, owed + 1)


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
                if state.frame_at(exit_) is None:
                    costs[exit_] = entered + 1
            left = budget - entered - 1
            if left <= 0:
                continue
            onward = state.board.reachable(
                exit_,
                left,
                occupied=state.occupied(exclude=frame.id),
                flying="flying" in frame.spec.keywords,
            )
            for pos, cost in onward.items():
                total = entered + 1 + cost + _gravity_penalty(state, exit_, pos)
                if total <= budget and total < costs.get(pos, 10 ** 6):
                    costs[pos] = total

    return [
        {"x": pos.x, "y": pos.y, "cost": cost}
        for pos, cost in sorted(costs.items(), key=lambda kv: (kv[0].y, kv[0].x))
    ]


def after_move(
    state: GameState, frame: FrameState, old: Optional[Pos], new: Optional[Pos]
) -> None:
    """Nothing keys off a completed move any more.

    Portal used to: it read "create a portal at the start and end of this
    move", so the pair could only be known once the frame had walked. The card
    now names its own two tiles and builds the pair in its effect step. The
    hook stays because it is the engine's one "a frame finished moving" seam
    and `resolve` calls it unconditionally.
    """
    return


def after_card_resolved(state: GameState, frame: FrameState, uid: str) -> None:
    """Relentless Assault: wind an action back so it resolves a second time."""
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
    if _reflex_step(state):
        return True
    top = _highest_initiative(state)
    if _teleport_step(state, top):
        return True
    if _drone_step(state, top):
        return True
    return False


# -- Ace Reflexes ----------------------------------------------------------


def _reflex_step(state: GameState) -> bool:
    for frame in state.frames.values():
        if not frame.alive or int(frame.turn_flags.get("reflex_moves", 0)) <= 0:
            continue
        frame.turn_flags["reflex_moves"] = int(frame.turn_flags["reflex_moves"]) - 1
        if frame.pos is None or state.board is None:
            continue
        from . import keywords as kw

        reach = state.board.reachable(
            frame.pos,
            2,
            occupied=state.occupied(exclude=frame.id),
            flying=kw.is_flying(frame),
        )
        options = [
            {"x": p.x, "y": p.y}
            for p in sorted(reach, key=lambda p: (p.y, p.x))
        ]
        if len(options) <= 1:
            continue
        state.pending = _ask(
            state,
            "reposition",
            seat=frame.seat,
            frame_id=frame.id,
            prompt=f"Ace Reflexes: move {frame.id} up to 2",
            options=options,
        )
        return True
    return False


def _choice_reposition(
    state: GameState, frame: FrameState, choice: Mapping, ctx: Mapping
) -> None:
    _move_frame(state, frame, Pos(int(choice["x"]), int(choice["y"])))


# -- Teleport --------------------------------------------------------------


def _teleport_step(state: GameState, top: Optional[int]) -> bool:
    for frame in fx.any_frame_with(state, TELEPORT, this_turn=False):
        if frame.turn_flags.get("teleported"):
            continue
        if top is not None and top > 4:
            continue
        frame.turn_flags["teleported"] = True
        options = [
            {"x": p.x, "y": p.y}
            for p in fx.free_tiles(state, frame.pos, 10 ** 6, include_origin=True)
        ]
        if not options:
            continue
        state.pending = _ask(
            state,
            "reposition",
            seat=frame.seat,
            frame_id=frame.id,
            prompt=f"Teleport: reposition {frame.id} anywhere on the map",
            options=options,
        )
        return True
    return False


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
    reach = state.board.reachable(
        token.pos, budget, occupied=state.occupied() - {token.pos}
    )
    options = [
        {"x": p.x, "y": p.y}
        for p in sorted(reach, key=lambda p: (p.y, p.x))
    ]
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
    """What this drone may shoot: enemy frames, or an enemy's images.

    A frame hiding behind Ephemeral Images is not a target -- but its images
    are, for a drone exactly as for a frame. Without this the card would be a
    total answer to drones, which is not what it says.
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
    on_block(state, frame, block_card, state.frames.get(attack.attacker_id))
    _drone_resolve(state, token_id, attack)


CHOICE_HANDLERS = dict(CHOICE_HANDLERS)
CHOICE_HANDLERS.update({
    "reposition": _choice_reposition,
    "drone_move": _choice_drone_move,
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
) -> None:
    """`On Block:` -- fires when this card is spent blocking."""
    match = _ON_BLOCK_RE.search(block_card.text or "")
    if not match or attacker is None:
        return
    body = match.group(1)
    for kind, count in _parse_statuses(body):
        apply_status(state, attacker, kind, count)
    # "On Block: deals mid <dtype>" -- Parry hits back for one Mid.
    hit = re.search(r"deals\s+(high|mid|low)", body, re.I)
    if hit:
        from .state import deal_damage

        zone = hit.group(1).capitalize()
        zone = {"High": "High", "Mid": "Mid", "Low": "Low"}[zone]
        deal_damage(state, attacker, zone, 1, source=defender)
        state.note(f"{block_card.key} strikes back for 1 {zone}")


def _first_attacked_zone(card: Card) -> Optional[str]:
    zones = [z for z in ZONES if card.attacks[z] > 0]
    return zones[0] if zones else None


def attack_damage_bonus(
    state: GameState, attacker: FrameState, card: Card, target_id: str
) -> dict[str, int]:
    """Every source of extra attack marks that comes from card text."""
    bonus: dict[str, int] = {}

    def add(zone: Optional[str], amount: int) -> None:
        if zone and amount:
            bonus[zone] = bonus.get(zone, 0) + amount

    match = _DIDNT_MOVE_RE.search(card.text or "")
    if match:
        target = state.frames.get(target_id)
        if target is not None and not target.moved_this_turn:
            add(_first_attacked_zone(card), int(match.group(1)))
            state.note(f"{card.key}: target has not moved, +{match.group(1)} damage")

    if card.is_ranged and attacker.turn_flags.get("snipers_aim"):
        add(_first_attacked_zone(card), 1)
        state.note("Snipers aim adds 1 damage")

    if fx.card_active(state, attacker, PRACTICED, this_turn=False):
        same = sum(
            1 for uid in attacker.committed
            if state.cards[uid].location == "committed"
            and state.catalogue[state.cards[uid].key].group == card.group
            and state.catalogue[state.cards[uid].key].is_attack
        )
        if same > 1:
            add(_first_attacked_zone(card), same - 1)
            state.note(f"Practiced Technique adds {same - 1} damage")

    combo = fx.slot(state, "combo").get(attacker.id) or {}
    extra = combo.get("extra")
    if extra:
        for zone, amount in extra.items():
            add(zone, int(amount))
        fx.slot(state, "combo")[attacker.id] = {"armed": False}

    return bonus
