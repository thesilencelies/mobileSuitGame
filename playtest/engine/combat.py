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
    deal_attack_damage,
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
    from . import effects

    # Sensory Overload: "their ranged attacks this turn have a range of 2".
    # A cap, not a modifier -- it applies after the bonuses, so Snipers aim
    # cannot buy its way back out of a jammed sensor suite.
    cap = effects.range_cap(state, attacker)
    if cap is not None:
        value = min(value, cap)
    return max(0, value)


def attack_origins(state: GameState, attacker: FrameState) -> list[Pos]:
    """The tiles this attacker measures range and sight from.

    Its own tile, normally. Behind Ephemeral Images it is every image that is
    still standing -- "these tokens use this frame's actions" -- so a zone
    lands if *any* image is placed to land it. See `effects_state.origins`.
    """
    from . import effects_state as fx

    return fx.origins(state, attacker)


def zones_in_range(
    state: GameState,
    attacker: FrameState,
    card: Card,
    target_pos: Pos,
    defender: Optional[FrameState] = None,
    *,
    origin: Optional[Pos] = None,
) -> dict[str, int]:
    """Zone -> damage for the zones actually in range (rules.tex:414).

    Melee zones (range 0) need an adjacent target; ranged zones need the
    target within range and *not* adjacent.

    Each zone is measured from whichever of the attacker's origins reaches it,
    which for all but Ephemeral Images is the one tile it is standing on.
    `origin` pins the question to a single tile instead -- what one image on
    its own could have done.
    """
    if state.board is None:
        return {}
    spots = [origin] if origin is not None else attack_origins(state, attacker)
    if not spots:
        return {}
    out: dict[str, int] = {}
    for zone in ZONES:
        damage = card.attacks[zone]
        if damage <= 0:
            continue
        printed = card.ranges[zone]
        reach = (
            0 if printed <= 0
            else effective_range(state, attacker, card, zone, defender)
        )
        for spot in spots:
            distance = state.board.distance(spot, target_pos)
            if printed <= 0:
                if distance == 1:
                    out[zone] = damage
                    break
            elif 1 < distance <= reach:
                out[zone] = damage
                break
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
    spread: int = 0,
    reach: bool = True,
) -> dict[str, int]:
    """The zones and damage an attack lands with against this target.

    `extra` is per-zone and may open a zone the card does not print; `spread`
    is a flat "+N damage" from card text with no zone named, which the rules
    put on every zone the attack already applies to.

    `reach=False` drops the range check and lands every zone the card prints.
    Splash text needs it: "hits all enemies adjacent to the target" says who
    is caught, and the far side of the target is two tiles from a melee
    attacker -- measuring the weapon's own reach again would delete most of
    what the card just said it hits. Everything else still applies, elevation
    shift included, because that is about the ground and not the range.
    """
    zones = (
        zones_in_range(state, attacker, card, target_pos, defender)
        if reach
        else {z: d for z, d in card.attacks.items() if d > 0}
    )
    if extra and zones:
        # Bonus marks only exist if the attack reaches at all. These *can*
        # open a zone the card does not print -- Kamikiri's extra cut Mid.
        for zone, bonus in extra.items():
            zones[zone] = zones.get(zone, 0) + bonus
    if spread and zones:
        # "If a zone is not specified, this additional/reduced damage is
        # applied to each zone that the attack applies" (rules.tex "Damage
        # reduction and increases") -- so this one lands on the zones that are
        # already there and never invents a new one.
        zones = {z: d + spread for z, d in zones.items()}
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
    origin: Optional[Pos] = None,
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
    from . import effects

    # "Ignore obstacles" (Snipers aim) is exactly what `flying_attacker` means
    # to the board: obstacles stop blocking sight, nothing else changes.
    unobstructed = kw.is_flying(attacker) or effects.ignores_obstacles(state, attacker)
    # The line starts at the image that is doing the looking; `occupied` keeps
    # the others in, because an image blocks sight exactly as the frame would.
    source = origin if origin is not None else attacker.pos
    occupied = occupied - {source}
    try:
        seen = state.board.has_line_of_sight(
            source,
            target_pos,
            occupied=occupied,
            flying_attacker=unobstructed,
            flying_target=bool(defender is not None and kw.is_flying(defender)),
        )
    except TypeError:
        seen = state.board.has_line_of_sight(
            source,
            target_pos,
            occupied=occupied,
            flying_attacker=unobstructed,
        )
    # Rebound only ever adds: a mirror the frame can see lends it sight of
    # everything within 4 of the mirror.
    return seen or effects.rebound_sight(state, attacker, target_pos)


def can_target(
    state: GameState,
    attacker: FrameState,
    card: Card,
    target_pos: Pos,
    defender: Optional[FrameState] = None,
    *,
    origin: Optional[Pos] = None,
) -> bool:
    """Adjacency, LoS and the Fenrir restriction, from any origin the attacker
    may count from (`origin` pins it to one)."""
    if state.board is None:
        return False
    spots = [origin] if origin is not None else attack_origins(state, attacker)
    if card.is_ranged and not kw.can_use_ranged(attacker):
        return False
    for spot in spots:
        distance = state.board.distance(spot, target_pos)
        if not card.is_ranged:
            if distance == 1:
                return True
            continue
        if distance <= 1:
            continue               # ranged attacks may not target adjacent
        if _line_of_sight(state, attacker, target_pos, defender, origin=spot):
            return True
    return False


def hostile_targets(
    state: GameState, attacker: FrameState, card: Card
) -> list[tuple[str, str, Pos]]:
    """Everything this attacker is allowed to swing at, as (kind, id, pos).

    Range and line of sight are deliberately *not* applied: this is the list
    of things that count as an enemy, and each caller measures its own reach
    over it. `legal_targets` adds `can_target` and the per-zone range on top;
    splash text measures adjacency to its own origin instead.

    An enemy is any frame on another side and any attackable token that is not
    the attacker's own -- so the neutral pieces (the Tower, the reactors, a
    Shiny Thing) count, since they are things an attack may be aimed at.
    """
    from . import effects

    found: list[tuple[str, str, Pos]] = []
    if not card.is_attack or attacker.pos is None:
        return found
    for other in state.frames.values():
        if not other.alive or other.seat == attacker.seat or other.pos is None:
            continue
        if effects.is_untargetable(state, attacker, card, other):
            continue
        found.append(("frame", other.id, other.pos))
    for token in state.tokens.values():
        if not token.attackable or token.pos is None:
            continue
        if token.owner is not None and token.owner == attacker.seat:
            continue
        found.append(("token", token.id, token.pos))
    return found


def legal_targets(
    state: GameState, attacker: FrameState, card: Card, *, forced: bool = True
) -> list[dict[str, object]]:
    """Every frame/token this card could attack right now, with its zones.

    `forced=False` skips the Showboating narrowing: that card says who an
    attack may be *declared* on, which is not a question splash text asks.
    """
    from . import effects

    options: list[dict[str, object]] = []
    for kind, target_id, pos in hostile_targets(state, attacker, card):
        if kind == "frame":
            other = state.frames[target_id]
            if not can_target(state, attacker, card, pos, other):
                continue
            zones = attack_zones_against(state, attacker, card, pos, other)
            name = other.spec.name
        else:
            if not can_target(state, attacker, card, pos):
                continue
            zones = zones_in_range(state, attacker, card, pos)
            name = state.tokens[target_id].kind
        if zones:
            options.append(
                {"kind": kind, "id": target_id, "zones": dict(zones), "name": name}
            )
    # Showboating: "any frame that is able to must attack this frame".
    return effects.forced_targets(state, attacker, options) if forced else options


def _target_pos(state: GameState, kind: str, target_id: str) -> Optional[Pos]:
    """Where the declared target is standing, frame or token."""
    if kind == "frame":
        frame = state.frames.get(target_id)
        return frame.pos if frame is not None else None
    token = state.tokens.get(target_id)
    return token.pos if token is not None else None


def _splash_targets(
    state: GameState,
    attacker: FrameState,
    card: Card,
    primary_kind: str,
    primary_id: str,
) -> tuple[list[tuple[str, str]], bool]:
    """Extra targets caught by the card's splash text: (kind, id) list, reach.

    Three phrasings, differing only in the shape they sweep. All three catch
    the same *kind* of thing -- everything `hostile_targets` calls an enemy,
    tokens as well as frames, since a barricade or a gun tower standing beside
    you is as much in the way of a wide swing as a mech is:

    * "Hits all adjacent enemies" -- everything next to the *attacker*, which
      is why it does not matter what the attack was declared at;
    * "Also hits any enemies adjacent to the target" -- everything next to
      wherever the swing landed, whether or not the attacker could have
      reached it on its own;
    * "Hits all targets in range" (Chain_Tangle) -- no shape of its own. The
      card simply does not choose: it hits everything this attack could have
      been declared against.

    The second return value says whether the weapon's own range still applies
    to what was caught. For the two sweeps that name a shape it does not: the
    shape *is* the reach, and re-checking the range would take back most of
    what the card just said it hits. For "all targets in range" it does, since
    there the range is the shape -- a target at 3 is only hit by the zones
    that reach 3.

    Splash is not a choice, so none of them consults `forced_targets`:
    Showboating says who you may *declare* an attack on, and once declared the
    card does what it prints.
    """
    text = card.text.lower()
    if state.board is None:
        return [], True

    if "hits all targets in range" in text:
        caught = [
            (str(option["kind"]), str(option["id"]))
            for option in legal_targets(state, attacker, card, forced=False)
        ]
        reach = True
    else:
        if "hits all adjacent enemies" in text:
            origin = attacker.pos
        elif "adjacent to the target" in text:
            origin = _target_pos(state, primary_kind, primary_id)
        else:
            return [], True
        if origin is None:
            return [], True
        caught = [
            (kind, target_id)
            for kind, target_id, pos in hostile_targets(state, attacker, card)
            if state.board.distance(origin, pos) == 1
        ]
        reach = False
    return [t for t in caught if t != (primary_kind, primary_id)], reach


# --------------------------------------------------------------------------
# Declaring an attack
# --------------------------------------------------------------------------


def _declare_one(
    state: GameState,
    attacker: FrameState,
    card: Card,
    kind: str,
    target_id: str,
    *,
    extra: Mapping[str, int],
    spread: int,
    reach: bool = True,
) -> Optional[AttackTarget]:
    """One target's zones, with the Ephemeral Images swap already applied.

    Both the declared target and anything splash catches come through here, so
    an image is resolved the same way whichever of the two it was: hitting the
    real one was always an attack on the frame, and finding that out is the
    point of the card. `reach` is passed straight down -- see
    `attack_zones_against`.
    """
    from . import effects

    if kind == "frame":
        defender = state.frames.get(target_id)
        if defender is None or defender.pos is None:
            return None
        zones = attack_zones_against(
            state, attacker, card, defender.pos, defender,
            extra=extra, spread=spread, reach=reach,
        )
        return AttackTarget("frame", target_id, zones)

    token = state.tokens.get(target_id)
    if token is None or token.pos is None:
        return None
    image = effects.image_owner(state, token)
    if image is not None and image[1]:
        # The real one. This was always an attack on the frame -- and hitting
        # it is exactly how the trick gets found out.
        defender = image[0]
        effects.reveal_images(state, defender, why=f"{attacker.id} found it")
        if defender.pos is None:
            return None
        zones = attack_zones_against(
            state, attacker, card, defender.pos, defender,
            extra=extra, spread=spread, reach=reach,
        )
        return AttackTarget("frame", defender.id, zones)
    # No defender, so no elevation shift -- but a token is still shot with
    # whatever the card text is adding this turn.
    zones = attack_zones_against(
        state, attacker, card, token.pos, extra=extra, spread=spread, reach=reach
    )
    return AttackTarget("token", target_id, zones)


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

    extra_from_text, spread = effects.attack_damage_bonus(
        state, attacker, card, target_id
    )
    for zone, bonus in extra_from_text.items():
        extra[zone] = extra.get(zone, 0) + bonus

    primary = _declare_one(
        state, attacker, card, target_kind, target_id, extra=extra, spread=spread
    )
    if primary is not None:
        attack.targets.append(primary)

    # Splash goes after the primary, and reads the primary *after* the image
    # swap: a card that catches everything around "the target" has to mean the
    # frame the attack turned out to be against, not the image it was declared
    # at. A splash target with no zones left is simply out of reach in the ones
    # this card attacks, so it is dropped rather than added empty.
    if primary is not None:
        seen = {(primary.kind, primary.id)}
        splashed, reach = _splash_targets(
            state, attacker, card, primary.kind, primary.id
        )
        for kind, other_id in splashed:
            caught = _declare_one(
                state, attacker, card, kind, other_id,
                extra=extra, spread=spread, reach=reach,
            )
            if caught is None or not caught.zones:
                continue
            if (caught.kind, caught.id) in seen:
                continue          # an image that resolved to a frame already hit
            seen.add((caught.kind, caught.id))
            attack.targets.append(caught)

    for target in attack.targets:
        # Zones still open to a block. A normal block clears the lot in one
        # go; Guard Break makes the defender find a block for each zone.
        target.pending_zones = [z for z in ZONES if z in target.zones]
    effects.on_attack_declared(state, attacker, card, attack)
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
    from . import effects

    for uid in remaining_cards(state, defender):
        if uid in used:
            continue
        inst = state.cards[uid]
        if close_quarters and inst.resolved:
            continue           # cannot be blocked by an already-resolved card
        card = state.card(uid)
        if inst.resolved and effects.delegates_attack(card):
            # "Any blocks marked on the card that summoned the drone only
            # apply before the card resolves: after it resolves they no longer
            # block anything" (rules.tex Drones). The card stays on the table
            # for the drone's health bar, not as a shield.
            continue
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
        f"{defender.id} blocks with {card.key}"
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

    from . import effects as _effects

    if target.kind == "token":
        token = state.tokens.get(target.id)
        # Damage reduction is per zone, not off the total: the Tower's -1
        # against an attack of 1 High and 2 Low takes 1, not 2
        # (rules.tex "Damage reduction and increases").
        if token is not None and token.damage_reduction:
            landed = {
                z: max(0, d - token.damage_reduction) for z, d in landed.items()
            }
        total = sum(landed.values())
        # A fake image is "removed if attacked" -- not damaged, and not saved
        # by a Feint that landed nothing.
        if token is not None and _effects.strike_image(state, token):
            return
        if token is not None and total:
            damage_token(state, token, total)
            what = (
                _effects.drone_name(state, token.id)
                if token.kind == "drone" else f"the {token.kind}"
            )
            state.note(f"{attack.via or attacker.id} hits {what} for {total}")
            _effects.images_dealt_damage(state, attacker, card, token.pos)
        return

    defender = state.frames.get(target.id)
    if defender is None:
        return
    # One shield counter absorbs the whole attack, every zone of it. The
    # damage is the attacker's -- it takes the kill -- but it comes from
    # wherever the swing did, which for a drone is the drone.
    swinging = state.tokens.get(attack.via_token) if attack.via_token else None
    deal_attack_damage(
        state, defender, landed, source=attacker,
        source_pos=swinging.pos if swinging is not None else attacker.pos,
    )
    if landed:
        state.note(
            f"{attack.via or attacker.id} hits {defender.id} with {card.key} "
            f"for {sum(landed.values())}"
        )
    elif target.blocked:
        state.note(f"{card.key} is blocked by {defender.id}")

    from . import effects

    if landed:
        effects.on_hit(state, attacker, card, defender)
        # "the fakes are removed ... if they would deal damage" -- measured
        # before a knockback moves the target out from under them.
        effects.images_dealt_damage(state, attacker, card, defender.pos, defender)
        steps = kw.knockback_amount(state, attacker, card)
        if steps:
            kw.apply_knockback(state, attacker, defender, steps)
    effects.after_attacked(state, defender, attacker)


def advance_attack(state: GameState, attack: AttackInProgress) -> bool:
    """Move to the next splash target. True when the attack is finished."""
    attack.index += 1
    return attack.index >= len(attack.targets)
