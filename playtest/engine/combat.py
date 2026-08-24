"""Attack resolution: range, elevation shift, compulsory blocking, damage.

The attack pipeline, in order:

1. `zones_in_range`      -- drop zones the target is not in range for
2. `elevation_shift`     -- melee only; shift toward High/Low by the elevation
                            difference, dropping anything pushed off the ends
3. `declare_attack`      -- build the `AttackInProgress`, adding splash targets
4. `block_options`       -- compulsory: if any remaining card blocks any
                            attacked zone, the defender must pick one
5. `apply_block`         -- discard the blocker (unless super block / Hector)
6. `finish_target`       -- damage, On Hit, knockback, destruction
"""

from __future__ import annotations

from typing import Mapping, Optional, Sequence

from . import keywords as kw
from .state import (
    AttackInProgress,
    AttackTarget,
    FrameState,
    GameState,
    TokenState,
    damage_token,
    deal_damage,
    discard_card,
)
from .types import Card, Pos, ZONES, Zone

# --------------------------------------------------------------------------
# Range
# --------------------------------------------------------------------------


def effective_range(
    state: GameState,
    attacker: FrameState,
    card: Card,
    zone: Zone,
    defender: Optional[FrameState] = None,
) -> int:
    """Printed range for a zone, plus attacker bonuses, minus defender tricks."""
    printed = card.ranges[zone]
    if printed <= 0:
        return 0
    value = printed + kw.range_bonus(state, attacker, card)
    if defender is not None:
        value -= kw.range_penalty_against(state, defender)
    return max(0, value)


def zones_in_range(
    state: GameState,
    attacker: FrameState,
    card: Card,
    target_pos: Pos,
    defender: Optional[FrameState] = None,
) -> dict[str, int]:
    """Zone -> damage for the zones actually in range (rules.tex:414).

    Melee zones (range 0) need an adjacent target; ranged zones need the
    target within range and *not* adjacent.
    """
    if attacker.pos is None or state.board is None:
        return {}
    distance = state.board.distance(attacker.pos, target_pos)
    out: dict[str, int] = {}
    for zone in ZONES:
        damage = card.attacks[zone]
        if damage <= 0:
            continue
        printed = card.ranges[zone]
        if printed <= 0:
            if distance == 1:
                out[zone] = damage
        else:
            reach = effective_range(state, attacker, card, zone, defender)
            if 1 < distance <= reach:
                out[zone] = damage
    return out


# --------------------------------------------------------------------------
# Elevation shift (rules.tex:563 and its two worked examples)
# --------------------------------------------------------------------------


def elevation_shift(zones: Mapping[str, int], delta: int) -> dict[str, int]:
    """Shift a melee attack's zones by `delta = attacker_elev - target_elev`.

    Attacker higher -> toward High; attacker lower -> toward Low. Anything
    pushed past either end is out of range and is dropped.

    The rulebook prose says "moved up one bracket per difference"; both of its
    worked examples say the direction depends on who is higher, and the
    examples are authoritative:

    * attacker at elevation 1 vs defender at 3 with Cleave (High 2, Mid 2):
      High becomes Low, Mid is out of range.
    * attacker at elevation 2 vs defender at 1 with Thrust (Mid 1):
      Mid becomes High.
    """
    if delta == 0:
        return dict(zones)
    shifted: dict[str, int] = {}
    for zone, damage in zones.items():
        index = ZONES.index(zone) - delta
        if 0 <= index < len(ZONES):
            target = ZONES[index]
            shifted[target] = shifted.get(target, 0) + damage
    return shifted


def attack_zones_against(
    state: GameState,
    attacker: FrameState,
    card: Card,
    target_pos: Pos,
    defender: Optional[FrameState] = None,
    *,
    extra: Optional[Mapping[str, int]] = None,
) -> dict[str, int]:
    """The zones and damage an attack lands with against this target."""
    zones = zones_in_range(state, attacker, card, target_pos, defender)
    if extra and zones:
        # Bonus marks only exist if the attack reaches at all.
        for zone, bonus in extra.items():
            zones[zone] = zones.get(zone, 0) + bonus
    if not card.is_ranged and defender is not None:
        delta = state.elevation(attacker.pos) - state.elevation(defender.pos)
        zones = elevation_shift(zones, delta)
    return {z: d for z, d in zones.items() if d > 0}


# --------------------------------------------------------------------------
# Target selection
# --------------------------------------------------------------------------


def _line_of_sight(
    state: GameState,
    attacker: FrameState,
    target_pos: Pos,
    defender: Optional[FrameState],
) -> bool:
    """LoS through `BoardProtocol`, passing Flying on both ends.

    Flying says obstacles do not block LoS "to or from" the frame, but the
    frozen `BoardProtocol` only names `flying_attacker`. Workstream B1's board
    takes an extra optional `flying_target`; any other implementation that does
    not is called without it.
    """
    occupied = state.occupied(exclude=attacker.id)
    if defender is not None and defender.pos is not None:
        occupied = occupied - {defender.pos}
    try:
        return state.board.has_line_of_sight(
            attacker.pos,
            target_pos,
            occupied=occupied,
            flying_attacker=kw.is_flying(attacker),
            flying_target=bool(defender is not None and kw.is_flying(defender)),
        )
    except TypeError:
        return state.board.has_line_of_sight(
            attacker.pos,
            target_pos,
            occupied=occupied,
            flying_attacker=kw.is_flying(attacker),
        )


def can_target(
    state: GameState,
    attacker: FrameState,
    card: Card,
    target_pos: Pos,
    defender: Optional[FrameState] = None,
) -> bool:
    """Adjacency, LoS and the Fenrir restriction."""
    if attacker.pos is None or state.board is None:
        return False
    distance = state.board.distance(attacker.pos, target_pos)
    if card.is_ranged:
        if not kw.can_use_ranged(attacker):
            return False
        if distance <= 1:
            return False           # ranged attacks may not target adjacent
        return _line_of_sight(state, attacker, target_pos, defender)
    return distance == 1


def legal_targets(
    state: GameState, attacker: FrameState, card: Card
) -> list[dict[str, object]]:
    """Every frame/token this card could attack right now, with its zones."""
    options: list[dict[str, object]] = []
    if not card.is_attack or attacker.pos is None:
        return options
    for other in state.frames.values():
        if not other.alive or other.seat == attacker.seat or other.pos is None:
            continue
        if other.turn_flags.get("untargetable_ranged") and card.is_ranged:
            continue
        if not can_target(state, attacker, card, other.pos, other):
            continue
        zones = attack_zones_against(state, attacker, card, other.pos, other)
        if zones:
            options.append(
                {"kind": "frame", "id": other.id, "zones": dict(zones),
                 "name": other.spec.name}
            )
    for token in state.tokens.values():
        if not token.attackable or token.pos is None:
            continue
        if token.owner is not None and token.owner == attacker.seat:
            continue
        if not can_target(state, attacker, card, token.pos):
            continue
        zones = zones_in_range(state, attacker, card, token.pos)
        if zones:
            options.append(
                {"kind": "token", "id": token.id, "zones": dict(zones),
                 "name": token.kind}
            )
    return options


def _splash_targets(
    state: GameState, attacker: FrameState, card: Card, primary: FrameState
) -> list[FrameState]:
    """Extra frames caught by the card's splash text."""
    text = card.text.lower()
    if state.board is None:
        return []
    if "hits all adjacent enemies" in text:
        origin = attacker.pos
    elif "adjacent to the target" in text:
        origin = primary.pos
    else:
        return []
    if origin is None:
        return []
    caught: list[FrameState] = []
    for other in state.frames.values():
        if (
            other.alive
            and other.seat != attacker.seat
            and other.id != primary.id
            and other.pos is not None
            and state.board.distance(origin, other.pos) == 1
        ):
            caught.append(other)
    return caught


# --------------------------------------------------------------------------
# Declaring an attack
# --------------------------------------------------------------------------


def declare_attack(
    state: GameState,
    attacker: FrameState,
    uid: str,
    *,
    target_kind: str,
    target_id: str,
) -> AttackInProgress:
    """Build the in-flight attack. Live: consumes Kamikiri's once-a-turn bonus.

    An attack spent reloading never reaches here -- `resolve` drops the whole
    attack step for it, so it consumes no block and triggers nothing.
    """
    card = state.card(uid)
    attack = AttackInProgress(
        attacker_id=attacker.id,
        uid=uid,
        guard_break=kw.is_guard_break(card),
        feint=kw.is_feint(card),
    )
    extra = kw.bonus_attacks(state, attacker, card)
    from . import effects

    extra_from_text = effects.attack_damage_bonus(state, attacker, card, target_id)
    for zone, bonus in extra_from_text.items():
        extra[zone] = extra.get(zone, 0) + bonus

    if target_kind == "frame":
        defender = state.frames[target_id]
        zones = attack_zones_against(
            state, attacker, card, defender.pos, defender, extra=extra
        )
        attack.targets.append(AttackTarget("frame", target_id, zones))
        for other in _splash_targets(state, attacker, card, defender):
            splash_zones = attack_zones_against(
                state, attacker, card, other.pos, other, extra=extra
            )
            if splash_zones:
                attack.targets.append(AttackTarget("frame", other.id, splash_zones))
    else:
        token = state.tokens[target_id]
        zones = zones_in_range(state, attacker, card, token.pos)
        attack.targets.append(AttackTarget("token", target_id, zones))

    for target in attack.targets:
        # Zones still open to a block. A normal block clears the lot in one
        # go; Guard Break makes the defender find a block for each zone.
        target.pending_zones = [z for z in ZONES if z in target.zones]
    return attack


def next_block_decision(
    state: GameState, attack: AttackInProgress
) -> Optional[tuple[list[str], list[str]]]:
    """`(zones, candidate uids)` for the next compulsory block, or None.

    Blocking is compulsory whenever an option exists, so this returning a
    value *is* the obligation -- there is no "decline" option.

    The offer is always the *whole* set of still-unblocked zones, for both
    kinds of attack. What differs is what spending a card does to that set
    (see `apply_block`): an ordinary attack is stopped outright by one
    matching zone, while Guard Break only clears the zones that card actually
    covers and comes back for the rest.
    """
    target = attack.current
    if target is None or target.done or target.kind != "frame":
        return None
    defender = state.frames.get(target.id)
    if defender is None or not defender.alive or not target.pending_zones:
        return None
    zones = list(target.pending_zones)
    candidates = block_options(state, defender, attack, zones)
    return (zones, candidates) if candidates else None


# --------------------------------------------------------------------------
# Blocking (rules.tex:551 -- compulsory)
# --------------------------------------------------------------------------


def remaining_cards(state: GameState, defender: FrameState) -> list[str]:
    """Cards still in front of the frame: face-down and resolved alike.

    Persistent cards set aside at cleanup are excluded -- they neither resolve
    nor block (rules.tex:598).
    """
    return [uid for uid in defender.committed if state.cards[uid].location == "committed"]


def block_options(
    state: GameState,
    defender: FrameState,
    attack: AttackInProgress,
    zones: Sequence[str],
) -> list[str]:
    """Which of the defender's remaining cards may block these zones."""
    target = attack.current
    used = set(target.used_blockers) if target else set()
    attack_card = state.card(attack.uid)
    close_quarters = kw.is_close_quarters(attack_card)
    out: list[str] = []
    for uid in remaining_cards(state, defender):
        if uid in used:
            continue
        inst = state.cards[uid]
        if close_quarters and inst.resolved:
            continue           # cannot be blocked by an already-resolved card
        card = state.card(uid)
        if any(card.blocks.get(z, 0) > 0 for z in zones):
            out.append(uid)
    return out


def apply_block(
    state: GameState,
    defender: FrameState,
    attack: AttackInProgress,
    uid: str,
    zones: Sequence[str],
) -> None:
    """Spend a card to block. Super blocks (and Hector's first) are kept.

    Against Guard Break, "the same card can block multiple zones if it has
    them" (rules.tex:956): the card covers *every* attacked zone it blocks,
    and is still only spent once. Zones it does not cover stay open, and the
    defender must keep blocking while any remaining card covers any of them.
    """
    target = attack.current
    if target is None:
        return
    card = state.card(uid)
    matched = [z for z in zones if card.blocks.get(z, 0) > 0]
    target.blocked.extend(matched)
    target.used_blockers.append(uid)
    state.cards[uid].face_down = False
    kept = kw.block_is_kept(state, defender, card, matched)
    state.note(
        f"{defender.spec.name} blocks with {card.key}"
        + (" (kept)" if kept else " (discarded)")
    )
    if not kept:
        # If it had not yet resolved, its own action is forfeit -- leaving the
        # committed pile takes it out of the initiative queue.
        discard_card(state, uid)
    for zone in matched:
        if zone in target.pending_zones:
            target.pending_zones.remove(zone)
    if not attack.guard_break:
        target.pending_zones = []
        target.blocked = list(target.zones)   # one matching zone stops it all


# --------------------------------------------------------------------------
# Landing the damage
# --------------------------------------------------------------------------


def finish_target(state: GameState, attack: AttackInProgress) -> None:
    """Apply what is left of the attack to the current target."""
    target = attack.current
    if target is None or target.done:
        return
    target.done = True
    attacker = state.frames[attack.attacker_id]
    card = state.card(attack.uid)
    landed = {
        z: d for z, d in target.zones.items() if z not in target.blocked
    }
    if attack.feint:
        landed = {}

    if target.kind == "token":
        token = state.tokens.get(target.id)
        total = sum(landed.values())
        if token is not None and total:
            damage_token(state, token, total)
            state.note(f"{attacker.spec.name} hits the {token.kind} for {total}")
        return

    defender = state.frames.get(target.id)
    if defender is None:
        return
    for zone in ZONES:
        if zone in landed:
            deal_damage(state, defender, zone, landed[zone], source=attacker)
    if landed:
        state.note(
            f"{attacker.spec.name} hits {defender.spec.name} with {card.key} "
            f"for {sum(landed.values())}"
        )
    elif target.blocked:
        state.note(f"{card.key} is blocked by {defender.spec.name}")

    if landed:
        from . import effects

        effects.on_hit(state, attacker, card, defender)
        steps = kw.knockback_amount(state, attacker, card)
        if steps:
            kw.apply_knockback(state, attacker, defender, steps)


def advance_attack(state: GameState, attack: AttackInProgress) -> bool:
    """Move to the next splash target. True when the attack is finished."""
    attack.index += 1
    return attack.index >= len(attack.targets)
