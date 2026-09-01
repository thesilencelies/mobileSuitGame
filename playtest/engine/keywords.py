"""Keyword behaviours and frame abilities.

Card keywords (rules.tex:953) are parsed into `Card.keywords` by `cards.py`;
this module is where they *do* something. Frame abilities from `Frames.csv`
are the same kind of thing -- short mechanical modifiers -- so they live here
too, dispatched by frame name.

Nothing here mutates piles directly except `apply_knockback` and the reload
bookkeeping; the attack pipeline in `combat.py` calls these as hooks.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Optional

from .state import GameState, FrameState, discard_card, move_card, record_movement
from .types import Card, Pos, ZONES, Zone

# --------------------------------------------------------------------------
# Keyword predicates
# --------------------------------------------------------------------------


def has(card: Card, keyword: str) -> bool:
    return keyword in card.keywords


def is_feint(card: Card) -> bool:
    return "feint" in card.keywords


def is_guard_break(card: Card) -> bool:
    return "guardbreak" in card.keywords


def is_committed(card: Card) -> bool:
    return "committed" in card.keywords


def is_close_quarters(card: Card) -> bool:
    return "closequarters" in card.keywords


def is_reload(card: Card) -> bool:
    return "reload" in card.keywords


def has_on_hit(card: Card) -> bool:
    return "onhit" in card.keywords


def is_flying(frame: FrameState) -> bool:
    return "flying" in frame.spec.keywords


# --------------------------------------------------------------------------
# Frame abilities (Frames.csv `Abilities`)
# --------------------------------------------------------------------------

#: frame name -> the ability, as implemented. All 12 rows of Frames.csv are
#: accounted for; `Flying`, `Shield(X)` and `Deathstrike` are keyword-driven
#: and handled elsewhere (movement/LoS, `state.add_shield`, destruction).
FRAME_ABILITIES: Mapping[str, str] = {
    "Percival MkIV": "attacks with 2+ block zones lose 2 less movement",
    "Kuwagata": "once per game, mulligan the planning hand",
    "VX4-Nautilus": "ranged attacks lose 2 less movement",
    "Adam": "pierce attacks get +2 initiative",
    "RipperSmasher": "deathstrike; actions lose at most 1 movement",
    "Hannael": "flying; shield 1",
    "Elemiah": "shield 2; impact attacks gain knockback(1)",
    "J7R-Salaryman": "ranged attacks get +4 range",
    "Flamekin": "deathstrike; repair 1 at the end of each turn",
    "Fenrir": "cannot use ranged weapons",
    "Hector MkI": "the first block each turn is not discarded",
    "Kamikiri": "the first melee attack each turn deals an extra cut Mid",
}


def _card_dtypes(card: Card) -> set[str]:
    return {card.dtypes[z] for z in ZONES if card.attacks[z] > 0 and card.dtypes[z]}


# --------------------------------------------------------------------------
# Initiative
# --------------------------------------------------------------------------


def effective_initiative(
    state: GameState, frame: FrameState, card: Card, index: int = 0
) -> int:
    """A card's initiative right now: printed + statuses + damage + abilities."""
    printed = card.initiative[min(index, len(card.initiative) - 1)]
    value = printed + frame.initiative_mod
    if frame.spec.name == "Adam" and "pierce" in _card_dtypes(card):
        value += 2
    return value


# --------------------------------------------------------------------------
# Movement
# --------------------------------------------------------------------------


def card_movement_modifier(state: GameState, frame: FrameState, card: Card) -> int:
    """The card's movement modifier after frame abilities.

    All three "reduce movement by N less" abilities only ever soften a
    penalty; none of them turns a penalty into a bonus.
    """
    move = card.movement
    if move < 0:
        name = frame.spec.name
        if name == "Percival MkIV" and card.is_attack and len(card.block_zones) >= 2:
            move = min(0, move + 2)
        elif name == "VX4-Nautilus" and card.is_ranged:
            move = min(0, move + 2)
        elif name == "RipperSmasher":
            move = max(move, -1)
    return move


def movement_budget(state: GameState, frame: FrameState, card: Optional[Card]) -> int:
    """Base movement plus this card's modifier, floored at 0."""
    budget = frame.base_movement
    if card is not None:
        budget += card_movement_modifier(state, frame, card)
    return max(0, budget)


# --------------------------------------------------------------------------
# Range
# --------------------------------------------------------------------------


def range_bonus(state: GameState, frame: FrameState, card: Card) -> int:
    """Extra range on a ranged attack from abilities and effects."""
    bonus = 0
    if frame.spec.name == "J7R-Salaryman" and card.is_ranged:
        bonus += 4
    bonus += int(frame.turn_flags.get("range_bonus", 0))
    return bonus


def range_penalty_against(state: GameState, defender: FrameState) -> int:
    """Range reduction the *defender* imposes -- `Basic_Dodge` is -8."""
    return int(defender.turn_flags.get("range_penalty_against", 0))


def can_use_ranged(frame: FrameState) -> bool:
    return frame.spec.name != "Fenrir"


# --------------------------------------------------------------------------
# Attack-time hooks
# --------------------------------------------------------------------------


def knockback_amount(state: GameState, frame: FrameState, card: Card) -> int:
    """Knockback(X) from the card, plus Elemiah's impact-attack knockback."""
    amount = card.knockback
    if frame.spec.name == "Elemiah" and "impact" in _card_dtypes(card):
        amount = max(amount, 1)
    return amount


def bonus_attacks(
    state: GameState, frame: FrameState, card: Card
) -> dict[str, int]:
    """Extra attack marks a frame ability adds to a card's zones."""
    extra: dict[str, int] = {}
    if (
        frame.spec.name == "Kamikiri"
        and card.is_attack
        and not card.is_ranged
        and not frame.turn_flags.get("kamikiri_used")
    ):
        frame.turn_flags["kamikiri_used"] = True
        extra["Mid"] = 1
        state.note("Kamikiri's first melee attack adds an extra cut Mid")
    return extra


def apply_knockback(
    state: GameState,
    source: FrameState,
    target: FrameState,
    steps: int,
) -> None:
    """Push `target` `steps` tiles directly away from `source`.

    "Move the target frame X steps in any direction away from the source
    (cannot move up elevation)" -- the engine takes the straight line away,
    stopping at the first tile it cannot enter.
    """
    if steps <= 0 or target.pos is None or source.pos is None or state.board is None:
        return
    dx = (target.pos.x > source.pos.x) - (target.pos.x < source.pos.x)
    dy = (target.pos.y > source.pos.y) - (target.pos.y < source.pos.y)
    if dx == 0 and dy == 0:
        return
    # A knockback ends wherever it stops, so it is stopped by anything a move
    # could not end on -- a unit as well as a wall.
    occupied = state.move_blockers(target) | state.unit_tiles(exclude=target.id)
    pos = target.pos
    flying = is_flying(target)
    for _ in range(steps):
        nxt = Pos(pos.x + dx, pos.y + dy)
        if not state.board.in_bounds(nxt):
            break
        tile = state.board.tile(nxt)
        if tile.impassable or nxt in occupied:
            break
        if not flying and tile.obstacle:
            break
        if not flying and tile.elevation > state.board.tile(pos).elevation:
            break  # cannot be knocked up an elevation
        pos = nxt
    if pos != target.pos:
        state.note(f"{target.id} is knocked back to ({pos.x},{pos.y})")
        record_movement(state, target, target.pos, pos)
        target.pos = pos


# --------------------------------------------------------------------------
# Reload
# --------------------------------------------------------------------------


def start_reload(state: GameState, frame: FrameState, uid: str) -> None:
    """A Reload card has resolved: it stays out until this weapon fires again."""
    card = state.card(uid)
    frame.reloading[card.group] = uid
    state.cards[uid].persist_left = None
    state.note(f"{frame.id}'s {card.group} must reload")


def is_reloading_attack(state: GameState, frame: FrameState, card: Card) -> bool:
    """True if this card is the attack that gets spent reloading the weapon.

    "This card persists until this frame next resolves an attack from this
    weapon. That attack has no effect or attack, and then this card is
    discarded" (rules.tex:963). Only an *attack* from that weapon group
    clears the reload; a block-only card from the same group does not.
    """
    return card.is_attack and card.group in frame.reloading


def consume_reload(state: GameState, frame: FrameState, card: Card) -> bool:
    """Spend the reload: discard the marker and clear the group.

    The spent attack itself does nothing at all -- no attack, no effect, no
    blocks consumed and no abilities triggered, including any further Reload.
    The frame still moves; see `resolve._begin_resolution`.
    """
    marker = frame.reloading.pop(card.group, None)
    if marker is None:
        return False
    if marker in state.cards:
        state.cards[marker].reload_for = ""
        discard_card(state, marker)
    state.note(
        f"{frame.id}'s {card.group} is spent reloading: "
        f"{card.key} has no effect or attack"
    )
    return True


# --------------------------------------------------------------------------
# Blocking
# --------------------------------------------------------------------------


def block_is_kept(state: GameState, defender: FrameState, block_card: Card,
                  zones: Iterable[str]) -> bool:
    """Whether a blocking card survives instead of being discarded.

    A super block (Block >= 2) in a matched zone is never discarded; Hector's
    first block each turn is likewise kept.
    """
    if any(block_card.blocks[z] >= 2 for z in zones if z in block_card.blocks):
        return True
    from . import effects

    if effects.blocks_are_kept(state, defender):
        state.note(f"{defender.id} is showboating: the block is not discarded")
        return True
    if defender.spec.name == "Hector MkI" and not defender.turn_flags.get(
        "hector_block_used"
    ):
        defender.turn_flags["hector_block_used"] = True
        state.note("Hector's first block of the turn is not discarded")
        return True
    return False


# --------------------------------------------------------------------------
# End-of-turn frame abilities
# --------------------------------------------------------------------------


def end_of_turn(state: GameState) -> None:
    """Frame abilities that fire at the end of every turn."""
    from .state import repair

    for frame in state.frames.values():
        if frame.alive and frame.spec.name == "Flamekin":
            repair(state, frame, 1)
