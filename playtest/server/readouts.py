"""Derived read-outs the client cannot work out for itself.

Two things a player needs and the base `view_for` does not carry:

**What is resolving.** `PendingDecision` names the frame but not the card, so
"which of my six committed actions is this?" was unanswerable from the view.
`GameState.resolution` has both, and by the time it exists the card has already
been turned face up (`resolve._begin_resolution`), so surfacing it leaks
nothing. `resolving()` reads it straight off the state.

**What the defender can still block.** This is the read the game is actually
about, and by hand it means remembering every card still standing in front of
every enemy frame and what each of them blocks. The parts are all public --
a card that has resolved is face up on the table, and how many face-down cards
someone has left is a thing you can count across the table -- so `defence()`
assembles them, *through the engine*: which cards are still in front of a frame
is `combat.remaining_cards`, and which of them may block a zone is
`combat.block_options`, the same call the engine makes when it offers the
compulsory block. Nothing here restates a rule the engine owns.

Redaction is the same rule as `serialize._card_json`: a card this seat may not
identify contributes to the *counts* and nothing else. So the readout says
"two face-down cards, either of which might block Mid" and never which they are.
"""

from __future__ import annotations

from typing import Any, Optional

from ..engine import ZONES
from ..engine import combat, keywords as kw
from ..engine.state import AttackInProgress, FrameState, GameState

__all__ = ["resolving", "defence", "initiative_of"]


# --------------------------------------------------------------------------
# What is resolving
# --------------------------------------------------------------------------


def resolving(state: GameState, seat: int) -> Optional[dict[str, Any]]:
    """The card mid-resolution: which frame, which card, how far through.

    `None` between cards. The card is face up whenever this is not `None`, but
    the `face_down` check is kept so a future engine that resolves something
    face down cannot leak it through this door.
    """
    res = getattr(state, "resolution", None)
    if res is None:
        return None
    inst = state.cards.get(res.uid)
    frame = state.frames.get(res.frame_id)
    if inst is None or frame is None:
        return None
    visible = not inst.face_down or frame.seat == seat
    out: dict[str, Any] = {
        "frameId": res.frame_id,
        "frameName": frame.spec.name,
        "seat": frame.seat,
        "mine": frame.seat == seat,
        "steps": list(res.steps),
        # What is happening now, as against what is still to come. The two
        # differ: an effect takes itself off `steps` before it runs, so the
        # remaining list says "movement" while the card is mid-effect.
        "step": res.step,
        "reloading": bool(res.spent_reloading),
    }
    if visible:
        out["uid"] = res.uid
        out["key"] = inst.key
        card = state.catalogue.get(inst.key)
        if card is not None:
            out["initiative"] = kw.effective_initiative(
                state, frame, card, inst.init_index)
    attack = res.attack
    if attack is not None and attack.current is not None:
        target = attack.current
        out["attack"] = {
            "targetKind": target.kind,
            "targetId": target.id,
            "zones": dict(target.zones),
            "blocked": list(target.blocked),
            "pendingZones": list(target.pending_zones),
            "guardBreak": bool(attack.guard_break),
        }
    return out


def initiative_of(state: GameState, frame: FrameState, uid: str) -> Optional[int]:
    """A committed card's initiative *as the engine will use it*, or None.

    The printed number is on the card; this is the one that actually decides
    the queue, after Stunned/Stimmed, a High zone on its last hit and frame
    abilities such as Adam's pierce bonus.
    """
    inst = state.cards.get(uid)
    if inst is None:
        return None
    card = state.catalogue.get(inst.key)
    if card is None:
        return None
    return kw.effective_initiative(state, frame, card, inst.init_index)


# --------------------------------------------------------------------------
# What the defender can block
# --------------------------------------------------------------------------


def _live_attack(state: GameState, defender_id: str) -> Optional[AttackInProgress]:
    """The attack in flight against this frame, if it is the current target."""
    res = getattr(state, "resolution", None)
    attack = getattr(res, "attack", None) if res is not None else None
    if attack is None or attack.current is None:
        return None
    return attack if attack.current.id == defender_id else None


def _synthetic_attack(state: GameState, uid: str) -> Optional[AttackInProgress]:
    """A blank attack carrying just the attacking card, for `block_options`.

    `block_options` reads two things off the attack: the card (for Close
    Quarters, which bars cards that have already resolved) and the blockers
    already spent. Before the attack is declared -- which is exactly when the
    player is choosing a target and needs this readout -- the second is empty,
    and this stands in for the first. It is a fresh object and is never handed
    back to the engine, so nothing is mutated.
    """
    if uid not in state.cards:
        return None
    return AttackInProgress(attacker_id="", uid=uid, targets=[])


def _blockers(
    state: GameState,
    defender: FrameState,
    zones: list[str],
    attack: Optional[AttackInProgress],
) -> list[str]:
    if attack is not None:
        return combat.block_options(state, defender, attack, zones)
    # No card is attacking (planning, or an idle readout). `block_options`'
    # attack-dependent filters -- Close Quarters and the blockers already spent
    # -- have nothing to act on, so what is left is the card's own block value.
    return [
        uid for uid in combat.remaining_cards(state, defender)
        if any(state.card(uid).blocks.get(z, 0) > 0 for z in zones)
    ]


def _keeps_the_next_block(defender: FrameState) -> bool:
    """Frame abilities that let a *normal* block survive (Hector's first).

    Mirrors the non-card half of `keywords.block_is_kept`, which cannot be
    called from a read-only path because it consumes the once-a-turn flag.
    """
    return (defender.spec.name == "Hector MkI"
            and not defender.turn_flags.get("hector_block_used"))


def defence(state: GameState, defender: FrameState, seat: int) -> dict[str, Any]:
    """What this seat can see about a frame's ability to block, per zone.

    ``zones[z]`` is ``{"cards": n, "super": n, "known": [...]}``:

    * ``cards`` -- how many of the cards still in front of the frame block that
      zone *and are identifiable by this seat*;
    * ``super`` -- how many of those are super blocks (Block >= 2), which are
      kept rather than discarded, so they can block again;
    * ``known`` -- those cards, so the client can name and picture them.

    ``faceDown`` is how many cards are still face down: any of them might block
    any zone, and that uncertainty is the game. It is a count and never an
    identity, so the same structure is safe to send about either seat.
    """
    attack = _live_attack(state, defender.id)
    if attack is None:
        res = getattr(state, "resolution", None)
        if res is not None and getattr(res, "uid", None):
            attack = _synthetic_attack(state, res.uid)

    remaining = combat.remaining_cards(state, defender)
    own = defender.seat == seat

    def readable(uid: str) -> bool:
        return own or not state.cards[uid].face_down

    face_down = [uid for uid in remaining if not readable(uid)]
    zones: dict[str, Any] = {}
    for zone in ZONES:
        candidates = _blockers(state, defender, [zone], attack)
        known = []
        supers = 0
        for uid in candidates:
            if not readable(uid):
                continue
            inst = state.cards[uid]
            card = state.catalogue[inst.key]
            is_super = card.blocks.get(zone, 0) >= 2
            supers += 1 if is_super else 0
            known.append({
                "uid": uid,
                "key": inst.key,
                "super": is_super,
                "resolved": inst.resolved,
                "faceDown": inst.face_down,
            })
        # Note what is deliberately *not* here: how many of the face-down cards
        # block this zone. That is the hidden half of the game -- counting it
        # per zone would give away what the cards are. The reader gets the
        # frame's total `faceDown` instead: any of them might cover anything.
        zones[zone] = {
            "cards": len(known),
            "super": supers,
            "known": known,
        }
    return {
        "frameId": defender.id,
        "seat": defender.seat,
        "remaining": len(remaining),
        "faceDown": len(face_down),
        "onField": sum(1 for uid in remaining if state.cards[uid].resolved),
        "keepsNextBlock": _keeps_the_next_block(defender),
        "zones": zones,
    }


def defence_all(state: GameState, seat: int) -> dict[str, Any]:
    """`defence()` for every frame still standing, keyed by frame id."""
    return {
        frame.id: defence(state, frame, seat)
        for frame in state.frames.values()
        if frame.alive
    }
