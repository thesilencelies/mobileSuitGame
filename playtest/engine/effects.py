"""Card-text effect registry.

v1 implements weapon, basic, booster and faction-frame card text plus the
`On Hit:` / `On Block:` riders. Pilot and drone *text* is deliberately
deferred: those cards still load, block and deal damage, but their text is
registered as a `DeferredEffect` so the UI can flag it instead of the engine
silently doing nothing.

Anything a card's text asks for that is not recognised also lands in
`DeferredEffect`, so nothing is ever quietly dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Mapping, Optional

from .state import (
    FrameState,
    GameState,
    add_shield,
    apply_status,
    repair,
)
from .types import Card, PendingDecision, Pos, StatusKind, ZONES

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


# --------------------------------------------------------------------------
# Deferred effects
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DeferredEffect:
    """A card effect that is out of scope for v1.

    The engine raises nothing and does nothing, but records the fact in the
    log and exposes it through `view_for` so the client can show "this card's
    text is not implemented yet" rather than pretending it worked.
    """

    key: str
    text: str

    @property
    def reason(self) -> str:
        return f"{self.key}: card text not implemented in v1 -- {self.text}"


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
    if card.key in EFFECT_STEPS:
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
    """Every card whose text v1 does not implement."""
    return {
        key: DeferredEffect(key, card.text)
        for key, card in catalogue.items()
        if effect_kind(card) == "deferred"
    }


def has_effect_step(card: Card) -> bool:
    return effect_kind(card) in ("handled", "deferred")


# --------------------------------------------------------------------------
# Implemented effect steps
# --------------------------------------------------------------------------

EffectFn = Callable[[GameState, FrameState, str], Optional[PendingDecision]]


def _effect_dodge(state: GameState, frame: FrameState, uid: str):
    card = state.card(uid)
    match = _RANGE_PENALTY_RE.search(card.text)
    penalty = int(match.group(1)) if match else 0
    frame.turn_flags["range_penalty_against"] = max(
        int(frame.turn_flags.get("range_penalty_against", 0)), penalty
    )
    state.note(f"{frame.spec.name} dodges: ranged attacks target it at -{penalty}")
    return None


def _effect_repair(state: GameState, frame: FrameState, uid: str):
    card = state.card(uid)
    match = _REPAIR_RE.search(card.text)
    repair(state, frame, int(match.group(1)) if match else 0)
    return None


def _effect_shield(state: GameState, frame: FrameState, uid: str):
    add_shield(state, frame, 1)
    state.note(f"{frame.spec.name} gains a shield counter")
    return None


def _effect_accelerate(state: GameState, frame: FrameState, uid: str):
    card = state.card(uid)
    match = _MV_BONUS_RE.search(card.text)
    bonus = int(match.group(1)) if match else 0
    frame.turn_flags["movement_bonus"] = (
        int(frame.turn_flags.get("movement_bonus", 0)) + bonus
    )
    state.note(f"{frame.spec.name}'s other actions this turn get +{bonus} movement")
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
    if state.board is None or frame.pos is None:
        return None
    options: list[dict] = []
    for other in state.enemies_of(frame.seat):
        if other.pos is None or state.board.distance(frame.pos, other.pos) > reach:
            continue
        reachable = state.board.reachable(
            other.pos,
            steps,
            occupied=state.occupied(exclude=other.id),
            flying="flying" in other.spec.keywords,
        )
        for pos in reachable:
            options.append({"frame": other.id, "x": pos.x, "y": pos.y})
    if not options:
        return None
    return PendingDecision(
        kind="effect_choice",
        seat=frame.seat,
        prompt=f"{card.name}: move an opposing frame within {reach} up to {steps}",
        options=options,
        frame_id=frame.id,
    )


def _apply_call_of_nature(state: GameState, frame: FrameState, choice: Mapping) -> None:
    target = state.frames.get(str(choice.get("frame")))
    if target is None:
        return
    target.pos = Pos(int(choice["x"]), int(choice["y"]))
    state.note(f"{target.spec.name} is moved to ({target.pos.x},{target.pos.y})")


EFFECT_STEPS: Mapping[str, EffectFn] = {
    "Basic_Dodge": _effect_dodge,
    "Frame_Bio-regen": _effect_repair,
    "Frame_Shield": _effect_shield,
    "Frame_Call of Nature": _effect_call_of_nature,
    "Booster_Accelerate": _effect_accelerate,
}

#: Effect-choice handlers, keyed the same way.
EFFECT_CHOICES: Mapping[str, Callable[[GameState, FrameState, Mapping], None]] = {
    "Frame_Call of Nature": _apply_call_of_nature,
}


def resolve_effect(
    state: GameState, frame: FrameState, uid: str
) -> Optional[PendingDecision]:
    """Run a card's effect step. Returns a decision if the effect needs one."""
    card = state.card(uid)
    handler = EFFECT_STEPS.get(card.key)
    if handler is not None:
        return handler(state, frame, uid)
    if effect_kind(card) == "deferred":
        state.note(f"[not implemented] {card.key}: {card.text}")
    return None


def apply_effect_choice(state: GameState, frame: FrameState, uid: str,
                        choice: Mapping) -> None:
    handler = EFFECT_CHOICES.get(state.card(uid).key)
    if handler is not None:
        handler(state, frame, choice)


# --------------------------------------------------------------------------
# Attack riders
# --------------------------------------------------------------------------


def on_hit(
    state: GameState, attacker: FrameState, card: Card, defender: FrameState
) -> None:
    """`On Hit:` -- only applies when the attack was not blocked."""
    match = _ON_HIT_RE.search(card.text or "")
    if not match:
        return
    body = match.group(1)
    for kind, count in _parse_statuses(body):
        apply_status(state, defender, kind, count)


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


def attack_damage_bonus(
    state: GameState, attacker: FrameState, card: Card, target_id: str
) -> dict[str, int]:
    """Text that adds damage to an attack -- the Sniper Rifle's stillness bonus."""
    match = _DIDNT_MOVE_RE.search(card.text or "")
    if not match:
        return {}
    target = state.frames.get(target_id)
    if target is None or target.moved_this_turn:
        return {}
    bonus = int(match.group(1))
    zones = [z for z in ZONES if card.attacks[z] > 0]
    if not zones:
        return {}
    state.note(f"{card.key}: target has not moved, +{bonus} damage")
    return {zones[0]: bonus}
