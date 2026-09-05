"""The tactical evaluation: what a card is worth, and what a tile is worth.

Two halves.

**The card scorer** is a port of the ideas in `simulation/simulate.py` --
relative initiative, zone coverage against the opponent's attack profile,
concentration, dominated-card filtering, reload-awareness and a survival
override. Those ideas survive the move to a real board, but the abstraction
they came from assumed every attack always connects. Here they are multiplied
by an **opportunity** term: what that card can actually do this turn, given
where the frames are standing, what it can reach, and whether anything is in
range and in line of sight.

**The positional evaluator** is the part the abstraction never had: expected
damage from a candidate tile (including the melee elevation shift), objective
proximity, exposure to enemy line of sight, and standoff range for ranged
weapons.

Nothing here reads game state -- it works entirely on a `Snapshot` built from
one seat's redacted view.
"""

from __future__ import annotations

import math
import random
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Sequence

from ..engine.hazards import hazard_for
from ..engine.types import Pos, TURNS_PER_GAME, ZONES
from .params import AIParams
from .view import (
    NO_RANGED_FRAMES,
    RANGE_BONUS_FRAMES,
    CardInfo,
    FrameView,
    ObjectiveView,
    Snapshot,
)

# --------------------------------------------------------------------------
# Constants that are not worth exposing as parameters
# --------------------------------------------------------------------------

#: Tempo value of an attack the opponent blocks, relative to one that lands.
FORCE_WEIGHT = 0.35

#: Value of destroying an enemy frame, in "damage marks". One VP plus the loss
#: of everything that frame would have done for the rest of the game.
KILL_VALUE = 7.0

#: Value of pushing a zone to its last hit (the -1 initiative/cards/movement
#: penalty, plus being one hit from death).
LAST_HIT_VALUE = 1.2

#: Cost of one mark of hazard damage, per turn still to be played, for a tile
#: that deals it at the end of every turn (the Railway's rails). Priced above
#: a mark of ordinary incoming damage because it is certain rather than
#: likely, and no block stops it.
HAZARD_TILE_COST = 0.6

#: Multiplier on a card's offence when it has no reachable target this turn.
#: Not zero -- it can still block, and next turn exists.
NO_TARGET = 0.15

#: Multiplier when the *other* committed card's movement could bring this one
#: into range, but this card cannot get there on its own.
PAIR_ASSIST = 0.65

#: Ceiling on the modelled chance an attack is blocked.
MAX_BLOCK_PROB = 0.88

#: Per-tile discount on an attack the frame cannot make yet. A melee weapon
#: three tiles from its target is worth 0.72**3 -- about a third -- of what it
#: would be worth landed, which is a gradient to walk up without ever rating a
#: future hit above a real one.
APPROACH_DISCOUNT = 0.72

#: Cost of committing an attack that a reload marker will swallow. The card
#: does nothing whatsoever -- it does not attack, force a block or trigger an
#: ability -- so it is a wholly wasted action, not merely a harmless one.
RELOAD_WASTE = 0.9


# --------------------------------------------------------------------------
# Deck / opponent profiles
# --------------------------------------------------------------------------


@dataclass
class Profile:
    """The tendencies of a set of cards, as the scorer needs them."""

    block_freq: dict[str, float] = field(default_factory=lambda: {z: 0.0 for z in ZONES})
    atk_weight: dict[str, float] = field(default_factory=lambda: {z: 0.0 for z in ZONES})
    atk_freq: dict[str, float] = field(default_factory=lambda: {z: 0.0 for z in ZONES})
    peak_atk: dict[str, int] = field(default_factory=lambda: {z: 0 for z in ZONES})
    atk_vals: dict[str, list[int]] = field(default_factory=lambda: {z: [] for z in ZONES})
    inits: list[int] = field(default_factory=list)
    ranged_share: float = 0.0
    n: int = 0


def quantile(values: Sequence[int], q: float) -> int:
    """The `q` quantile of `values`, nearest-rank. Empty -> 0."""
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[index]


def profile(cards: Sequence[CardInfo], *, peak_q: float = 1.0) -> Profile:
    """Aggregate cards into per-zone blocking/attacking tendencies.

    `peak_q` decides what counts as the opponent's "peak" hit on a zone. For
    cards actually seen in play the answer is the plain maximum (`1.0`) -- that
    card exists and can turn up again. For a *prior* built from the whole
    faction card pool the maximum is the scariest card in the game, which the
    opponent probably does not hold and is unlikely to commit this turn; taking
    a high quantile instead stops the survival term treating every zone as a
    one-shot risk from turn one and turtling the AI permanently.
    """
    prof = Profile()
    n = len(cards) or 1
    ranged = 0
    for card in cards:
        for zone in card.block_zones:
            # A super block is never spent, so it turns away more than one
            # attack over a game.
            prof.block_freq[zone] += 1.5 if card.blocks[zone] >= 2 else 1.0
        if card.is_ranged:
            ranged += 1
        prof.inits.append(card.init)
        if card.feint:
            continue                      # a feint deals no damage
        for zone in ZONES:
            damage = card.attacks.get(zone, 0)
            if damage <= 0:
                continue
            prof.atk_weight[zone] += damage
            prof.atk_freq[zone] += 1
            prof.atk_vals[zone].append(damage)
    for zone in ZONES:
        prof.peak_atk[zone] = quantile(prof.atk_vals[zone], peak_q)
        prof.block_freq[zone] /= n
        prof.atk_weight[zone] /= n
        prof.atk_freq[zone] /= n
    prof.ranged_share = ranged / n
    prof.inits.sort()
    prof.n = len(cards)
    return prof


def blend(prior: Profile, observed: Profile, weight: float) -> Profile:
    """Mix an observed profile into a prior. `weight` is the observed share."""
    if observed.n == 0 or weight <= 0:
        return prior
    weight = min(1.0, max(0.0, weight))
    out = Profile()
    for zone in ZONES:
        out.block_freq[zone] = (
            prior.block_freq[zone] * (1 - weight) + observed.block_freq[zone] * weight
        )
        out.atk_weight[zone] = (
            prior.atk_weight[zone] * (1 - weight) + observed.atk_weight[zone] * weight
        )
        out.atk_freq[zone] = (
            prior.atk_freq[zone] * (1 - weight) + observed.atk_freq[zone] * weight
        )
        # A card seen dealing 3 to a zone is a fact and can do it again, so the
        # observed peak is taken at face value; the prior's peak has already
        # been damped to a quantile of the faction pool.
        out.peak_atk[zone] = max(prior.peak_atk[zone], observed.peak_atk[zone])
        out.atk_vals[zone] = list(prior.atk_vals[zone]) + list(observed.atk_vals[zone])
    out.inits = sorted(prior.inits + observed.inits)
    out.ranged_share = (
        prior.ranged_share * (1 - weight) + observed.ranged_share * weight
    )
    out.n = max(prior.n, 1)
    return out


def rel_init(card: CardInfo, prof: Profile) -> float:
    """Where this card's initiative sits inside the opponent's spread.

    ~0 means it is slower than everything they have (so blocking with it costs
    it its own action); ~1 means it resolves first and can then block for free.
    """
    inits, n = prof.inits, len(prof.inits)
    if not n:
        return 0.5
    lo = bisect_left(inits, card.init)
    hi = bisect_right(inits, card.init)
    return (lo + hi) / (2 * n)


def carries_live_text(card: CardInfo) -> bool:
    """True when this card's worth is its text, and the engine runs that text.

    The scorer models printed stats, not card text. For an attacker the text is
    a rider on damage the scorer can already see, so comparing on stats is
    fair. For a card with no attack of its own the text *is* the card, and
    claiming another card "is never worse" than it is a claim about effects the
    scorer cannot see.

    Read straight off the catalogue's `notImplemented` flag, so it corrects
    itself as deferred effects land: the 24 pilot and 2 drone cards are
    currently inert and freely comparable, and the day their text is
    implemented they stop being pruned without anything here changing.
    """
    return bool(card.text.strip()) and not card.not_implemented and not card.is_attack


def dominates(a: CardInfo, b: CardInfo) -> bool:
    """True when playing `a` is never worse than `b`, and better somewhere.

    Feints are incomparable in both directions: a feint deals no damage but
    forces a block, which neither an attacker nor a blocker replicates. So is
    anything whose value lives in text the scorer does not model -- see
    `carries_live_text`.
    """
    if a.feint or b.feint:
        return False
    if carries_live_text(b):
        return False
    if a.key == b.key:
        return False
    if any(a.attacks.get(z, 0) < b.attacks.get(z, 0) for z in ZONES):
        return False
    if not set(b.block_zones) <= set(a.block_zones):
        return False
    if not set(b.super_block_zones) <= set(a.super_block_zones):
        return False
    if a.movement < b.movement:
        return False
    better = (
        any(a.attacks.get(z, 0) > b.attacks.get(z, 0) for z in ZONES)
        or len(a.block_zones) > len(b.block_zones)
        or len(a.super_block_zones) > len(b.super_block_zones)
        or a.movement > b.movement
    )
    return better


# --------------------------------------------------------------------------
# Frame-ability arithmetic the AI can work out from public information
# --------------------------------------------------------------------------


def movement_budget(frame: FrameView, card: Optional[CardInfo]) -> int:
    """`frame.movement` (already status- and damage-adjusted) plus the card."""
    budget = frame.movement
    if card is not None:
        move = card.movement
        if move < 0:
            name = frame.name
            if name == "Percival MkIV" and card.is_attack and len(card.block_zones) >= 2:
                move = min(0, move + 2)
            elif name == "VX4-Nautilus" and card.is_ranged:
                move = min(0, move + 2)
            elif name == "RipperSmasher":
                move = max(move, -1)
        budget += move
    return max(0, budget)


def effective_init(frame: FrameView, card: CardInfo, index: int = 0) -> int:
    """A card's initiative right now, including statuses and frame abilities."""
    printed = card.initiative[min(index, len(card.initiative) - 1)]
    value = printed
    if frame.statuses.get("stimmed", 0) > 0:
        value += 2
    if frame.statuses.get("stunned", 0) > 0:
        value -= 2
    if frame.last_hit.get("High"):
        value -= 1
    if frame.name == "Adam" and "pierce" in {
        card.dtypes.get(z) for z in card.attack_zones
    }:
        value += 2
    return value


def can_use(frame: FrameView, card: CardInfo) -> bool:
    """Fenrir cannot use ranged weapons at all."""
    return not (card.is_ranged and frame.name in NO_RANGED_FRAMES)


def range_bonus(frame: FrameView, card: CardInfo) -> int:
    return RANGE_BONUS_FRAMES.get(frame.name, 0) if card.is_ranged else 0


# --------------------------------------------------------------------------
# Attack geometry (mirrors engine/combat.py, over view data)
# --------------------------------------------------------------------------


def zones_in_range(card: CardInfo, distance: int, bonus: int = 0) -> dict[str, int]:
    """Zone -> damage for the zones that reach a target at this distance."""
    out: dict[str, int] = {}
    for zone in ZONES:
        damage = card.attacks.get(zone, 0)
        if damage <= 0:
            continue
        printed = card.ranges.get(zone, 0)
        if printed <= 0:
            if distance == 1:
                out[zone] = damage
        elif 1 < distance <= printed + bonus:
            out[zone] = damage
    return out


def elevation_shift(zones: Mapping[str, int], delta: int) -> dict[str, int]:
    """Melee only: shift toward High when higher, toward Low when lower."""
    if delta == 0:
        return dict(zones)
    shifted: dict[str, int] = {}
    for zone, damage in zones.items():
        index = ZONES.index(zone) - delta
        if 0 <= index < len(ZONES):
            shifted[ZONES[index]] = shifted.get(ZONES[index], 0) + damage
    return shifted


def landing_zones(
    snap: Snapshot,
    attacker: FrameView,
    card: CardInfo,
    from_pos: Pos,
    target: FrameView,
) -> dict[str, int]:
    """The zones and damage this card lands on `target` from `from_pos`."""
    if target.pos is None:
        return {}
    distance = snap.distance(from_pos, target.pos)
    zones = zones_in_range(card, distance, range_bonus(attacker, card))
    if not zones:
        return {}
    if not card.is_ranged:
        delta = snap.elevation(from_pos) - snap.elevation(target.pos)
        zones = elevation_shift(zones, delta)
    return {z: d for z, d in zones.items() if d > 0}


def can_reach_target(
    snap: Snapshot,
    attacker: FrameView,
    card: CardInfo,
    from_pos: Pos,
    target: FrameView,
    los_cache: Optional[dict] = None,
) -> bool:
    """Adjacency for melee; range, non-adjacency and LoS for ranged."""
    if target.pos is None or snap.board is None:
        return False
    distance = snap.distance(from_pos, target.pos)
    if card.is_ranged:
        if not can_use(attacker, card):
            return False
        if distance <= 1:
            return False
        return has_los(snap, from_pos, target, attacker, los_cache)
    return distance == 1


def has_los(
    snap: Snapshot,
    from_pos: Pos,
    target: FrameView,
    attacker: Optional[FrameView] = None,
    cache: Optional[dict] = None,
) -> bool:
    """Line of sight from a tile to a frame, using the engine's own predicate."""
    if snap.board is None or target.pos is None:
        return False
    key = (from_pos, target.pos, target.id)
    if cache is not None and key in cache:
        return cache[key]
    occupied = set(snap.occupied())
    occupied.discard(from_pos)
    occupied.discard(target.pos)
    try:
        result = snap.board.has_line_of_sight(
            from_pos,
            target.pos,
            occupied=frozenset(occupied),
            flying_attacker=bool(attacker and attacker.flying),
            flying_target=target.flying,
        )
    except (IndexError, TypeError):
        result = True
    if cache is not None:
        cache[key] = result
    return result


# --------------------------------------------------------------------------
# What an attack is worth
# --------------------------------------------------------------------------


def block_probability(
    target: FrameView,
    zones: Iterable[str],
    card: CardInfo,
    prof: Profile,
) -> float:
    """Chance the defender stops the zones in `zones`.

    Two sources, and the known one dominates: a card already face up in front
    of the target (resolved, or revealed while resolving) is a *fact*, and it
    blocks whatever it prints. Anything still face down is modelled from the
    opponent profile.

    For the hidden cards this asks "does *any* of them cover this", which is
    `1 - (1 - coverage) ** hidden`, not `coverage * hidden`. The linear form
    over-counted badly once a frame had two or three cards out -- two cards at
    a 0.4 block rate came out as 0.8 rather than the correct 0.64.

    Call it with every attacked zone for an ordinary attack, where one
    matching zone stops the whole thing. Call it one zone at a time for Guard
    Break, where each zone is stopped or not on its own.
    """
    zones = list(zones)
    if not zones:
        return 0.0
    known = 0
    hidden = 0
    for ref in target.committed + target.on_field:
        if ref.key is None:
            hidden += 1
            continue
        if card.close_quarters and ref.resolved:
            continue                       # Close Quarters ignores spent cards
        known += 1 if _blocks_any(ref.key, zones) else 0
    if known:
        return MAX_BLOCK_PROB
    if hidden <= 0:
        return 0.0
    coverage = min(1.0, max(prof.block_freq.get(z, 0.0) for z in zones))
    return min(MAX_BLOCK_PROB, 1.0 - (1.0 - coverage) ** hidden)


#: key -> CardInfo, for the known-blocker test. Registered by every
#: `view.Catalogue` as it is built. The card list is static public data (the
#: same JSON the client fetches from `GET /api/cards`), identical for every
#: seat, so a shared registry carries no seat-specific information and cannot
#: leak anything between agents.
_CATALOGUE: dict[str, CardInfo] = {}


def set_catalogue(catalogue: Mapping[str, CardInfo]) -> None:
    """Merge a catalogue into the shared lookup."""
    _CATALOGUE.update(catalogue)


def _blocks_any(key: str, zones: Sequence[str]) -> bool:
    card = _CATALOGUE.get(key)
    if card is None:
        return False
    return any(card.blocks.get(z, 0) > 0 for z in zones)


def attack_value(
    snap: Snapshot,
    attacker: FrameView,
    card: CardInfo,
    from_pos: Pos,
    target: FrameView,
    prof: Profile,
    params: AIParams,
    *,
    dud: bool = False,
) -> float:
    """Expected worth of pointing `card` at `target` from `from_pos`."""
    zones = landing_zones(snap, attacker, card, from_pos, target)
    return zone_attack_value(card, zones, target, prof, params, dud=dud)


def zone_attack_value(
    card: CardInfo,
    zones: Mapping[str, int],
    target: FrameView,
    prof: Profile,
    params: AIParams,
    *,
    dud: bool = False,
) -> float:
    """Worth of an attack that lands `zones` on `target`.

    Split out from `attack_value` because at the `attack_target` decision the
    engine has already told us the exact post-range, post-elevation-shift
    zones, and its arithmetic is the one that counts.
    """
    if not zones:
        return 0.0
    total = sum(zones.values())
    if dud or total <= 0:
        return 0.0
    if card.feint:
        # A feint deals nothing but still forces a block -- pure tempo.
        blocked = block_probability(target, zones.keys(), card, prof)
        return FORCE_WEIGHT * total * blocked

    if card.guard_break:
        # Guard Break is a zone-by-zone question: each zone is stopped or not
        # on its own, so what lands is exactly the zones nothing they hold
        # covers. It is *not* a card-by-card question -- one blocking card
        # covers every attacked zone it has a block in, so a single wide
        # blocker turns the whole attack aside, and the defender's capacity is
        # not consumed a zone at a time. That second half is what `score_hand`
        # used to get wrong.
        #
        # Guard Break's remaining edge is attrition: every zone they have to
        # answer can cost another card, so blocked damage earns tempo per zone
        # rather than once for the whole attack.
        landed = 0.0
        forced = 0.0
        for zone, damage in zones.items():
            blocked = block_probability(target, [zone], card, prof)
            landed += damage * (1 - blocked)
            forced += FORCE_WEIGHT * damage * blocked
    else:
        blocked = block_probability(target, zones.keys(), card, prof)
        landed = total * (1 - blocked)
        forced = FORCE_WEIGHT * total * blocked

    value = params.aggression * landed + forced

    # Killing blows and last hits.
    for zone, damage in zones.items():
        remaining = target.remaining(zone)
        if remaining <= 0:
            continue
        share = landed / total if total else 0.0
        if damage >= remaining:
            value += KILL_VALUE * params.aggression * share
        elif damage >= remaining - 1:
            value += LAST_HIT_VALUE * share

    # Focus fire: finish what is already hurt.
    hurt = 1.0 - (target.total_remaining / max(1, sum(target.armour.values())))
    value *= 1.0 + params.focus_fire * hurt
    return value


#: Killing a drone is worth roughly this much per turn it would otherwise get
#: to attack for. It is not an objective, so nothing else prices it at all.
DRONE_KILL_VALUE = 1.1

#: Stripping one of a frame's Ephemeral Images. Worth more when it is the last
#: fake, because that leaves the frame standing in the open.
IMAGE_STRIP_VALUE = 0.8


def image_value(
    snap: Snapshot,
    attacker: FrameView,
    card: CardInfo,
    token,
    prof: Profile,
    params: AIParams,
) -> float:
    """Worth of shooting one of a frame's Ephemeral Images.

    Deliberately simple: one image in however many is the real frame, so the
    attack is worth that fraction of hitting it, plus what narrowing the guess
    is worth on its own. There is no attempt to track which image moved how --
    that is a memory the card is designed to defeat.
    """
    if token.pos is None or not token.alive or attacker.pos is None:
        return 0.0
    target = snap.frames.get(str(token.frame)) if token.frame else None
    if target is None:
        return 0.0
    live = [
        t for t in snap.tokens
        if t.alive and t.kind == "image" and t.frame == token.frame
    ]
    count = max(1, len(live))
    zones = zones_in_range(
        card, snap.distance(attacker.pos, token.pos), range_bonus(attacker, card)
    )
    if not zones:
        return 0.0
    hit = zone_attack_value(card, zones, target, prof, params)
    # Removing the second-to-last fake is what actually finds the frame.
    strip = IMAGE_STRIP_VALUE * (2.0 if count <= 2 else 1.0) * params.aggression
    return hit / count + strip * (count - 1) / count


#: Objectives that need several tokens gone before anything is scored, so one
#: kill is worth a fraction of the stake rather than all of it.
_TOKEN_SET_SCALE = {"Power Reactors": 0.45, "Riverside": 0.4, "Car Park": 0.4}


def token_value(
    snap: Snapshot,
    attacker: FrameView,
    card: CardInfo,
    from_pos: Pos,
    token,
    params: AIParams,
) -> float:
    """Worth of shooting an objective token (reactors, the Tower)."""
    if token.pos is None or not token.alive or token.max_hp <= 0:
        return 0.0
    distance = snap.distance(from_pos, token.pos)
    zones = zones_in_range(card, distance, range_bonus(attacker, card))
    damage = sum(zones.values())
    if damage <= 0:
        return 0.0
    if token.kind == "drone":
        # A drone attacks again every turn it survives, and nothing else in
        # this function would price it -- it belongs to no objective.
        turns_left = max(1, TURNS_PER_GAME - snap.turn + 1)
        progress = min(1.0, damage / max(1, token.hp))
        return params.aggression * DRONE_KILL_VALUE * progress * turns_left * 0.5
    obj = snap.objective_for_token(token)
    if obj is None or obj.settled:
        return 0.0
    stake = obj.value_for(snap.seat)
    # Never shoot your own -- and "your own" is the side that *created* the
    # token, not the side that brought the card. Riverside's gangs belong to
    # the attacker and it is the card's own owner who has to clear them out.
    if token.owner is not None and token.owner == snap.seat:
        return 0.0
    if token.owner is None and obj.owner == snap.seat:
        return 0.0
    progress = min(1.0, damage / max(1, token.hp))
    # Some objectives need most of a set destroyed before they score at all.
    scale = _TOKEN_SET_SCALE.get(obj.name, 1.0)
    return params.objective_weight * stake * progress * scale * 2.0


# --------------------------------------------------------------------------
# Threat and survival (ported from simulate.py, per zone)
# --------------------------------------------------------------------------


def threat_profile(
    prof: Profile, health: Mapping[str, int], hand_len: int
) -> tuple[dict[str, bool], dict[str, bool], dict[str, int]]:
    """`(kill_risk, one_shot, need)` per zone for a defender with `health`."""
    press = {z: prof.atk_weight[z] * hand_len for z in ZONES}
    one_shot = {z: prof.peak_atk[z] >= max(1, health.get(z, 99)) for z in ZONES}
    kill_risk = {
        z: one_shot[z] or (press[z] > 0 and press[z] * 2 >= max(1, health.get(z, 99)))
        for z in ZONES
    }
    n_atk = {
        z: min(hand_len, max(0, round(prof.atk_freq[z] * hand_len))) for z in ZONES
    }
    n_one = {
        z: (
            sum(1 for v in prof.atk_vals[z] if v >= max(1, health.get(z, 99)))
            if one_shot[z]
            else 0
        )
        for z in ZONES
    }
    need = {
        z: min(hand_len, max(n_atk[z] if kill_risk[z] else 0, min(n_one[z], hand_len)))
        for z in ZONES
    }
    return kill_risk, one_shot, need


def survival_deficit(
    cards: Sequence[CardInfo], prof: Profile, health: Mapping[str, int]
) -> float:
    """How badly this hand fails to cover the coverable lethal threats."""
    kill_risk, one_shot, need = threat_profile(prof, health, len(cards))
    if not any(kill_risk.values()):
        return 0.0
    has_super = {z: False for z in ZONES}
    count = {z: 0 for z in ZONES}
    for card in cards:
        for zone in card.block_zones:
            if card.blocks[zone] >= 2:
                has_super[zone] = True
            else:
                count[zone] += 1
    deficit = 0.0
    for zone in ZONES:
        if not kill_risk[zone]:
            continue
        held = need[zone] if has_super[zone] else count[zone]
        deficit += (1.0 if one_shot[zone] else 0.5) * max(0, need[zone] - held)
    return deficit


# --------------------------------------------------------------------------
# Scoring a pair of committed actions
# --------------------------------------------------------------------------


def score_hand(
    cards: Sequence[CardInfo],
    prof: Profile,
    params: AIParams,
    health: Mapping[str, int],
    *,
    reloading: Iterable[str] = (),
    opportunity: Optional[Mapping[int, float]] = None,
    board_value: Optional[Mapping[int, float]] = None,
    pressure: float = 1.0,
) -> float:
    """Rate a combination of cards as one turn's actions.

    `opportunity[i]` is how much of card *i*'s offence is actually available
    this turn (1.0 = it can reach a target under its own movement); the
    abstraction this is ported from had no such term and would happily commit
    a melee weapon with every enemy nine tiles away. `board_value[i]` is the
    positional worth of playing it (objectives, elevation, standoff).
    `pressure` in [0,1] is how much the enemy can actually hurt us this turn --
    it scales the defensive terms, so the AI does not sit behind a wall of
    blocks while nothing can reach it.
    """
    if not cards:
        return 0.0
    opportunity = opportunity or {}
    board_value = board_value or {}
    n = len(cards)

    # -- reload: which chosen attacks are spent reloading -------------------
    # A weapon with a reload marker out does not attack at all on its next
    # shot: no attack, no effect, no block consumed, no ability triggered
    # (rules.tex:963). The card is a burnt action -- it cannot even bait a
    # block out of the defender -- so it is worth strictly less than the old
    # "an attack that deals zero damage" model implied. Only an *attack* from
    # the group clears the marker, and a card spent this way does not re-arm.
    dud: set[int] = set()
    reload_state = set(reloading)
    if reload_state or any(c.reload for c in cards):
        order = sorted(
            (i for i, c in enumerate(cards) if c.is_attack),
            key=lambda i: -cards[i].init,
        )
        for i in order:
            group = cards[i].group
            if group in reload_state:
                dud.add(i)
                reload_state.discard(group)
            elif cards[i].reload:
                reload_state.add(group)

    kill_risk, one_shot, need_lethal = threat_profile(prof, health, n)
    under_alpha = any(kill_risk.values()) and pressure > 0.3

    hitters = {z: 0 for z in ZONES}
    block_avail = {z: [] for z in ZONES}
    has_super = {z: False for z in ZONES}
    attackers: list[tuple[int, CardInfo, float]] = []

    for i, card in enumerate(cards):
        t = rel_init(card, prof)
        if card.is_attack and not card.feint and i not in dud:
            attackers.append((i, card, t))
            for zone in card.attack_zones:
                hitters[zone] += 1
        # A card that is going to be spent reloading has no action left to
        # forfeit, so blocking with it is completely free.
        avail = 1.0 if (not card.is_attack or i in dud) else t
        if card.committed_kw and card.is_attack and i not in dud:
            avail *= 0.4
        for zone in card.block_zones:
            if card.blocks[zone] >= 2:
                has_super[zone] = True
            else:
                block_avail[zone].append(avail)

    # -- offence -----------------------------------------------------------
    offense = 0.0
    landing = {z: 0.0 for z in ZONES}
    budget = {z: prof.block_freq[z] * n for z in ZONES}
    forced = {z: 0 for z in ZONES}
    for i, card, t in sorted(attackers, key=lambda x: -sum(x[1].attacks.values())):
        opp = opportunity.get(i, 1.0)
        surv = (0.35 + 0.65 * t) if under_alpha else 1.0
        cqm = 0.5 if card.close_quarters else 1.0
        zones = [(z, card.attacks[z]) for z in card.attack_zones]
        if card.guard_break:
            # Each zone is answered on its own, so every zone is checked
            # against the budget -- but one blocking card covers every zone it
            # has a block in, so answering a Guard Break does *not* cost the
            # defender a card per zone. Drawing the budget down zone by zone
            # (as this did) left later zones looking undefended and made Guard
            # Break score far above what it can actually do against a
            # multi-zone blocker. Spend the budget once, like any other attack.
            spent = 0.0
            for zone, damage in sorted(zones, key=lambda za: -za[1]):
                blocked = min(1.0, max(0.0, budget[zone] * cqm))
                land = damage * (1.0 - blocked)
                offense += land * surv * opp
                landing[zone] += land * opp
                if blocked > 0.0 and forced[zone] < 1:
                    offense += FORCE_WEIGHT * damage * blocked * t * opp
                    forced[zone] += 1
                spent = max(spent, blocked)
            for zone, _damage in zones:
                budget[zone] = max(0.0, budget[zone] - spent)
        else:
            zb = max(zones, key=lambda za: budget[za[0]])[0]
            blocked = min(1.0, max(0.0, budget[zb] * cqm))
            for zone, damage in zones:
                land = damage * (1.0 - blocked)
                offense += land * surv * opp
                landing[zone] += land * opp
            if blocked > 0.0 and forced[zb] < 1:
                offense += (
                    FORCE_WEIGHT * sum(d for _, d in zones) * blocked * t * opp
                )
                forced[zb] += 1
            budget[zb] = max(0.0, budget[zb] - 1.0)
    offense *= params.aggression

    # -- defence -----------------------------------------------------------
    defense = 0.0
    for zone in ZONES:
        want = max(1, round(prof.atk_freq[zone] * n))
        if has_super[zone]:
            covered = float(want)
        else:
            covered = sum(sorted(block_avail[zone], reverse=True)[:want])
        defense += prof.atk_weight[zone] * covered
    defense *= params.defense * pressure

    # -- survival ----------------------------------------------------------
    survival = 0.0
    for zone in ZONES:
        if not kill_risk[zone]:
            continue
        held = need_lethal[zone] if has_super[zone] else len(block_avail[zone])
        weight = params.survival if one_shot[zone] else params.survival * 0.5
        survival += weight * pressure * min(held, need_lethal[zone])

    concentration = params.concentration * sum(
        landing[z] for z in ZONES if hitters[z] >= 2
    )
    positional = sum(board_value.get(i, 0.0) for i in range(n))

    # A slot spent on a shot that will be swallowed by a reload is a slot that
    # did nothing at all. Charge for it explicitly, in proportion to the attack
    # that was thrown away, so the AI clears its reload with the cheapest card
    # in the group rather than its best one -- and does not stack two shots
    # from a reloading weapon into the same turn.
    waste = 0.0
    for i in dud:
        card = cards[i]
        waste += RELOAD_WASTE * params.aggression * (
            1.0 + sum(card.attacks.values())
        ) * opportunity.get(i, 1.0)

    return offense + defense + concentration + survival + positional - waste


def softmax_pick(scores: Sequence[float], temperature: float, rng: random.Random) -> int:
    """Index sampled ~ softmax(score/temperature); <=0 is argmax."""
    if not scores:
        raise ValueError("nothing to pick from")
    if temperature <= 0 or len(scores) == 1:
        return max(range(len(scores)), key=lambda i: (scores[i], rng.random()))
    hi = max(scores)
    weights = [math.exp(max(-60.0, (s - hi) / temperature)) for s in scores]
    total = sum(weights)
    if total <= 0:
        return rng.randrange(len(scores))
    roll = rng.random() * total
    acc = 0.0
    for i, w in enumerate(weights):
        acc += w
        if roll <= acc:
            return i
    return len(scores) - 1


# --------------------------------------------------------------------------
# Positional evaluation
# --------------------------------------------------------------------------

#: Objectives scored by standing somewhere at the end of the game, and how far
#: from the objective tiles still counts.
_STAND_ON = {"Triangle", "Holo Spires", "The Egg"}
_WITHIN_TWO = {"Church"}
#: Scored by clearing a set of tokens off the board. Which side does the
#: clearing is read off the tokens, not the card: Riverside's gangs belong to
#: the attacker and it is the defender who has to kill them.
_TOKEN_HUNT = {"Power Reactors", "The Tower", "Riverside", "Car Park"}


def objective_value(
    snap: Snapshot, frame: FrameView, pos: Pos, params: AIParams
) -> float:
    """What standing on `pos` is worth in objective points.

    Objectives are roughly half the victory points on offer, so this is not a
    tiebreak. End-of-game objectives ramp up as the game runs out; latching
    ones (the Egg, the Fugitive) count at full weight from turn one.
    """
    if params.objective_weight <= 0:
        return 0.0
    total = 0.0
    late = min(1.0, 0.35 + 0.65 * (snap.turn / 5.0))
    for obj in snap.objectives:
        if obj.settled:
            continue
        stake = obj.value_for(snap.seat)
        if stake <= 0:
            continue
        if obj.name == "Shiny Thing":
            # The only objective with no tiles of its own: it is wherever its
            # token is, so it must be handled before the tile lookup below.
            total += late * stake * _held_value(snap, frame, pos, "shiny")
            continue
        if obj.name in _TOKEN_HUNT:
            total += _hunt_value(snap, pos, obj, stake)
            continue
        if not obj.tiles:
            continue
        distance = min(snap.distance(pos, t) for t in obj.tiles)
        if obj.name == "The Egg":
            # Latches after two consecutive turns standing on it.
            total += stake * (2.2 if distance == 0 else 0.55 * _falloff(distance, 6))
        elif obj.name in _STAND_ON:
            if obj.name == "Holo Spires" and obj.owner == snap.seat:
                # Defender scores by denying: standing there is still the way.
                total += late * stake * (1.0 if distance == 0 else 0.3 * _falloff(distance, 6))
            else:
                total += late * stake * (
                    1.4 if distance == 0 else 0.45 * _falloff(distance, 6)
                )
        elif obj.name in _WITHIN_TWO:
            near = 1.0 if distance <= 2 else 0.4 * _falloff(distance, 7)
            total += late * stake * near
        elif obj.name == "Fugitive":
            total += _fugitive_value(snap, frame, pos, obj, stake)
        elif obj.name == "Solar Farm":
            # A charge per frame per turn, so a turn spent walking there is a
            # charge nobody banked -- this one does not wait for the endgame.
            total += stake * (1.5 if distance == 0 else 0.5 * _falloff(distance, 7))
        elif obj.name == "Lake Crosses":
            total += _relic_value(snap, frame, pos, obj, stake, distance, late)
        elif obj.name == "Dome Campus":
            total += _bomb_value(snap, frame, pos, obj, stake, distance, late)
    return params.objective_weight * total


def _hunt_value(snap: Snapshot, pos: Pos, obj: ObjectiveView, stake: int) -> float:
    """Getting in reach of the tokens this objective wants destroyed."""
    marks = [
        t.pos for t in snap.tokens_for(obj)
        if t.alive and t.pos is not None and t.owner != snap.seat
    ]
    if not marks:
        return 0.0
    return 0.35 * stake * _falloff(min(snap.distance(pos, m) for m in marks), 8)


def _relic_value(
    snap: Snapshot, frame: FrameView, pos: Pos, obj: ObjectiveView,
    stake: int, distance: int, late: float,
) -> float:
    """Two platforms to stand on at once, then a relic to hold on to.

    Before the ritual this wants *both* platforms occupied, so it is worth
    standing on one only while the other is covered or coverable; after it,
    the relic is an ordinary carried token and reads like the Shiny Thing.
    """
    relic = next((t for t in snap.tokens_for(obj) if t.alive), None)
    if relic is not None and (relic.pos is not None or relic.carrier):
        return late * stake * _held_value(snap, frame, pos, relic.kind)
    if distance > 0:
        return late * stake * 0.4 * _falloff(distance, 7)
    # On a platform. Worth far more if a squadmate is on (or near) the other.
    others = [t for t in obj.tiles if t != pos]
    mates = [
        f for f in snap.mine()
        if f.id != frame.id and f.alive and f.pos is not None
    ]
    if not others or not mates:
        return late * stake * 0.5
    gap = min(snap.distance(f.pos, t) for f in mates for t in others)
    return late * stake * (1.6 if gap == 0 else 0.9 * _falloff(gap, 5))


def _bomb_value(
    snap: Snapshot, frame: FrameView, pos: Pos, obj: ObjectiveView,
    stake: int, distance: int, late: float,
) -> float:
    """Run the bomb in, or stand on the site so it cannot be run in."""
    if obj.carrier == frame.id:
        return stake * (2.0 if distance == 0 else 0.7 * _falloff(distance, 10))
    if obj.owner == snap.seat:
        # Defender: the site is one tile and a frame cannot walk through
        # another, so sitting on it is a real block.
        return late * stake * (0.9 if distance == 0 else 0.2 * _falloff(distance, 6))
    return 0.0


def _falloff(distance: int, span: int) -> float:
    return max(0.0, 1.0 - distance / float(span))


def _fugitive_value(
    snap: Snapshot, frame: FrameView, pos: Pos, obj: ObjectiveView, stake: int
) -> float:
    """Escorting the fugitive home (defender) or bodyblocking it (attacker)."""
    token = next((t for t in snap.tokens if t.kind == "fugitive" and t.alive), None)
    if token is None or token.pos is None:
        return 0.0
    if obj.owner == snap.seat:
        # Defender: stand next to it, and drag it toward the objective tile.
        goal = min(obj.tiles, key=lambda t: snap.distance(token.pos, t))
        touching = snap.distance(pos, token.pos) <= 1
        closing = _falloff(snap.distance(pos, goal), 12) if touching else 0.0
        return stake * (0.8 * (1.0 if touching else 0.35 * _falloff(
            snap.distance(pos, token.pos), 8)) + 1.2 * closing)
    # Attacker: sit on the road home.
    goal = min(obj.tiles, key=lambda t: snap.distance(token.pos, t))
    return 0.5 * stake * _falloff(snap.distance(pos, goal), 8)


def _held_value(snap: Snapshot, frame: FrameView, pos: Pos, kind: str) -> float:
    """Chasing, taking or keeping hold of a carried token.

    One shape for every carried token: hold it and stay alive, or walk onto
    the tile it is lying on, or run down whoever is carrying it.
    """
    token = next((t for t in snap.tokens if t.kind == kind and t.alive), None)
    if token is None:
        return 0.0
    if token.carrier == frame.id:
        return 1.2                      # already holding it: stay alive, stay put
    if token.carrier is not None:
        holder = snap.frames.get(token.carrier)
        if holder is not None and holder.seat == snap.seat:
            return 0.0
        return 0.5 * _falloff(snap.distance(pos, holder.pos), 8) if holder and holder.pos else 0.0
    if token.pos is None:
        return 0.0
    return 1.4 if pos == token.pos else 0.6 * _falloff(snap.distance(pos, token.pos), 8)


#: What a frame in contact with a pure gunline is still exposed to: the Basic
#: actions, which are melee and which every frame in the game carries.
MELEE_THREAT_FLOOR = 0.3

#: How hard a melee frame is pulled toward contact per tile of gap.
MELEE_CLOSE_PULL = 0.25

#: What arriving in contact with a shooter that has not fired yet is worth, on
#: top of whatever the frame does when it gets there. Deliberately a *contact*
#: bonus and not a longer pull: pulling harder from across the board just walks
#: frames off objectives (arena: 45.0% vs 51.7% over 60 games), while paying
#: for the last step in is the part that actually takes a turn off the enemy.
MELEE_PIN_VALUE = 1.5


def exposure(
    snap: Snapshot,
    frame: FrameView,
    pos: Pos,
    prof: Profile,
    params: AIParams,
    los_cache: Optional[dict] = None,
) -> float:
    """How dangerous standing on `pos` is, in expected incoming damage.

    Ranged threat needs line of sight, so breaking LoS with terrain or an
    elevation drop genuinely reduces this -- which is what makes the AI use
    cover instead of standing in the open.
    """
    if params.los_caution <= 0:
        return 0.0
    threat = 0.0
    incoming = sum(prof.atk_weight.values()) or 1.0
    # Only the enemy's *melee* cards can hurt something standing on top of
    # them: "ranged attacks may not target an adjacent frame". So walking into
    # contact with a gunline is not the most dangerous thing on the board, it
    # is the safest -- and the AI used to score it as the most dangerous and
    # keep its distance from exactly the frames it should have been smothering.
    # The floor is the Basic actions: everybody has a Punch, so contact is
    # never free.
    melee_threat = MELEE_THREAT_FLOOR + (1.0 - MELEE_THREAT_FLOOR) * max(
        0.0, 1.0 - prof.ranged_share
    )
    for enemy in snap.enemies():
        if enemy.pos is None:
            continue
        distance = snap.distance(pos, enemy.pos)
        reach = enemy.movement + 1
        if distance <= reach:
            # Melee range this turn.
            threat += incoming * melee_threat * (1.0 if distance <= 1 else 0.6)
        elif distance <= reach + 2:
            threat += incoming * melee_threat * 0.3
        if prof.ranged_share > 0.05 and 1 < distance <= 8:
            # Only a frame that can see us can shoot us.
            if has_los(snap, pos, enemy, frame, los_cache):
                threat += incoming * prof.ranged_share * _falloff(distance, 10)
    # A frame close to death cares far more about being shot at.
    frailty = 1.0 + 1.5 * (
        1.0 - frame.total_remaining / max(1, sum(frame.armour.values()))
    )
    return params.los_caution * threat * frailty


def terrain_value(snap: Snapshot, pos: Pos, params: AIParams) -> float:
    """A standing preference for high ground, and none at all for the rails.

    High ground shifts melee up a zone and sees further. A hazard tile is the
    opposite: it costs a hit at the end of every turn the frame is still on
    it, and nothing about it is worth that -- so it is priced by what it will
    cost over the turns that are left rather than as a flat dislike.
    """
    value = params.elevation * 0.25 * snap.elevation(pos)
    hazard = hazard_for(snap.tile(pos))
    if hazard is not None:
        turns_left = max(1, TURNS_PER_GAME - snap.turn + 1)
        value -= HAZARD_TILE_COST * hazard.amount * turns_left
    return value


def position_value(
    snap: Snapshot,
    frame: FrameView,
    pos: Pos,
    prof: Profile,
    params: AIParams,
    *,
    cards: Sequence[CardInfo] = (),
    primary: Optional[CardInfo] = None,
    los_cache: Optional[dict] = None,
    focus_id: Optional[str] = None,
    focus_weight: float = 0.0,
) -> float:
    """What it is worth for `frame` to be standing on `pos`.

    `primary` is the card resolving right now (its attack counts at full
    weight); `cards` are the frame's other committed actions, whose attacks
    count at a discount because they have not happened yet. `focus_id` is the
    squad's agreed target for the turn, scaled up by `focus_weight` -- the
    author's point that committing to one target is what makes movement
    planning tractable, because every frame then knows where it is heading.
    """
    value = 0.0
    if primary is not None and primary.is_attack:
        value += params.positioning * _best_attack_from(
            snap, frame, primary, pos, prof, params, los_cache,
            focus_id=focus_id, focus_weight=focus_weight,
        )
    for card in cards:
        if card.is_attack and card is not primary:
            value += 0.7 * params.positioning * _best_attack_from(
                snap, frame, card, pos, prof, params, los_cache,
                focus_id=focus_id, focus_weight=focus_weight,
            )
    value += objective_value(snap, frame, pos, params)
    value += terrain_value(snap, pos, params)
    value -= exposure(snap, frame, pos, prof, params, los_cache)
    value += _standoff_value(snap, frame, pos, cards, primary, prof, params)
    return value


def _standoff_value(
    snap: Snapshot,
    frame: FrameView,
    pos: Pos,
    cards: Sequence[CardInfo],
    primary: Optional[CardInfo],
    prof: Profile,
    params: AIParams,
) -> float:
    """Ranged frames want a gap; melee frames want to be on top of someone.

    A ranged attack may not target an adjacent frame, so standing next to the
    enemy with a rifle committed is worse than useless -- and the mirror of
    that is worth more than it looks: closing on a *shooter* does not just let
    a melee frame swing, it takes the shooter's whole turn away. The guns are
    also the slow cards, so the frame that walks in first is usually the one
    that gets to. `prof` is the enemy's profile, so the pull scales with how
    much of their damage is ranged.
    """
    if params.standoff <= 0:
        return 0.0
    considered = [c for c in list(cards) + ([primary] if primary else []) if c and c.is_attack]
    if not considered:
        return 0.0
    ranged = [c for c in considered if c.is_ranged]
    melee = [c for c in considered if not c.is_ranged]
    enemies = [e for e in snap.enemies() if e.pos is not None]
    if not enemies:
        return 0.0
    nearest = min(snap.distance(pos, e.pos) for e in enemies)
    value = 0.0
    if ranged and not melee:
        best = max(c.max_range for c in ranged)
        if nearest <= 1:
            value -= params.standoff * 3.0        # cannot shoot an adjacent frame
        elif nearest > best:
            value -= params.standoff * 0.4 * (nearest - best)
        else:
            value += params.standoff * 0.5
    elif melee and not ranged:
        value -= params.standoff * MELEE_CLOSE_PULL * max(0, nearest - 1)
        pinned = max(
            (
                _pin_value(enemy, prof)
                for enemy in enemies
                if snap.distance(pos, enemy.pos) <= 1
            ),
            default=0.0,
        )
        value += params.standoff * MELEE_PIN_VALUE * pinned
    return value


def _pin_value(enemy: FrameView, prof: Profile) -> float:
    """What standing on top of `enemy` takes away from it, 0..1.

    "Ranged attacks may not target an adjacent frame", so a shot it has not
    fired yet is a shot it does not get -- and the guns are the slow cards, so
    a frame that closes early usually closes before they go off. Worth nothing
    against an enemy that has already spent its actions, or one that was never
    going to shoot.
    """
    pending = sum(1 for card in enemy.committed if not card.resolved)
    if pending <= 0:
        return 0.0
    return prof.ranged_share * min(1.0, pending / 2.0)


def reach_gap(
    snap: Snapshot, frame: FrameView, card: CardInfo, pos: Pos, target_pos: Pos
) -> int:
    """Tiles short of a position this card could actually attack from.

    0 means it can attack from here. For a ranged weapon the window is
    `2 .. range`, since a ranged attack may not target an adjacent frame, so
    being *too close* is a gap as well.
    """
    distance = snap.distance(pos, target_pos)
    if card.is_ranged:
        high = card.max_range + range_bonus(frame, card)
        if distance < 2:
            return 2 - distance
        return max(0, distance - high)
    return max(0, distance - 1)


def _reference_zones(frame: FrameView, card: CardInfo) -> dict[str, int]:
    """The zones this card would land from a good position, elevation neutral."""
    if card.is_ranged:
        return zones_in_range(card, 2, range_bonus(frame, card))
    return zones_in_range(card, 1)


def _best_attack_from(
    snap: Snapshot,
    frame: FrameView,
    card: CardInfo,
    pos: Pos,
    prof: Profile,
    params: AIParams,
    los_cache: Optional[dict] = None,
    *,
    include_approach: bool = True,
    focus_id: Optional[str] = None,
    focus_weight: float = 0.0,
) -> float:
    """Best value this card gets from `pos`, now or after closing the gap.

    `focus_id` is the enemy the squad has agreed to converge on this turn; its
    value is scaled up by `focus_weight`, which is what makes three frames pick
    positions that all bear on the same target instead of each wandering off
    after its own nearest enemy.

    Without the approach term this returns 0 for every tile a melee weapon
    cannot already strike from, which is a flat landscape with no gradient --
    the frame has no reason to walk toward anybody, and two such AIs simply
    stand at opposite ends of the board trading nothing. The approach term
    values an attack we cannot make *yet* at a per-tile discount, which turns
    that flat landscape into a slope toward the enemy without ever rating a
    future hit above a real one.

    `include_approach=False` asks the strict question -- can this card do
    something *this turn* -- which is what the commit-time opportunity scan
    needs.
    """
    if not can_use(frame, card):
        return 0.0
    approach = params.approach if include_approach else 0.0
    best = 0.0
    for enemy in snap.enemies():
        if enemy.pos is None:
            continue
        weight = 1.0 + (focus_weight if enemy.id == focus_id else 0.0)
        if can_reach_target(snap, frame, card, pos, enemy, los_cache):
            best = max(
                best,
                weight * attack_value(snap, frame, card, pos, enemy, prof, params),
            )
        elif approach > 0:
            gap = reach_gap(snap, frame, card, pos, enemy.pos)
            if gap <= 0:
                continue                # in range but blocked LoS: no free lunch
            potential = zone_attack_value(
                card, _reference_zones(frame, card), enemy, prof, params
            )
            best = max(best, weight * approach * potential * APPROACH_DISCOUNT ** gap)
    for token in snap.tokens:
        if not token.alive or token.max_hp <= 0 or token.pos is None:
            continue
        distance = snap.distance(pos, token.pos)
        in_range = (
            (1 < distance <= card.max_range + range_bonus(frame, card))
            if card.is_ranged else distance == 1
        )
        if in_range:
            best = max(best, token_value(snap, frame, card, pos, token, params))
        elif approach > 0:
            gap = reach_gap(snap, frame, card, pos, token.pos)
            if gap > 0:
                potential = token_value(
                    snap, frame, card,
                    _step_toward(pos, token.pos, gap), token, params,
                )
                best = max(best, approach * potential * APPROACH_DISCOUNT ** gap)
    return best


def _step_toward(pos: Pos, goal: Pos, gap: int) -> Pos:
    """A notional position `gap` tiles closer to `goal`, for valuing approach."""
    dx = (goal.x > pos.x) - (goal.x < pos.x)
    dy = (goal.y > pos.y) - (goal.y < pos.y)
    return Pos(pos.x + dx * gap, pos.y + dy * gap)
