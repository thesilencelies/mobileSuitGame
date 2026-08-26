"""`GameState` -> the client JSON in SPEC.md.

`view_for` is the *only* thing the server sends to a seat, and it is built
from scratch as plain dicts. Nothing in the returned structure references the
`GameState`, so there is no path from a seat's view to another seat's hand,
deck order or face-down commitments -- the redaction is structural rather
than a matter of the caller behaving itself.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping, Optional

from . import effects_state as fx
from . import objectives as objectivelib
from .state import CardInstance, FrameState, GameState, victory_points
from .types import Card, PendingDecision, Team, ZONES, team_name


def hidden_id(state: GameState, seat: Team, uid: str) -> str:
    """An opaque, stable stand-in for a uid the seat may not decode.

    Card uids are allocated in deck-file order, and the deck CSVs ship with
    the app -- so a raw uid *is* the card's identity to anyone who can count.
    Hiding the `key` while still shipping the uid therefore hides nothing.

    The stand-in is a keyed digest of the uid under a per-game secret that
    never leaves the engine and is not derived from the seed, so it cannot be
    reversed or replayed. It is stable for a given (game, seat, card), so the
    client can still track a face-down card across polls.
    """
    material = f"{state.view_salt}|{seat}|{uid}".encode()
    return "h" + hashlib.blake2b(material, digest_size=6).hexdigest()


def _card_json(
    state: GameState, uid: str, seat: Team, *, reveal: bool
) -> dict[str, Any]:
    """One card in a frame's row. `reveal=False` hides the identity.

    The uid is redacted on exactly the same condition as the key: a card this
    seat may not identify must not carry an id it can decode.
    """
    inst = state.cards[uid]
    visible = reveal or not inst.face_down
    out: dict[str, Any] = {
        "uid": uid if visible else hidden_id(state, seat, uid),
        "resolved": inst.resolved,
        "faceDown": inst.face_down,
    }
    if inst.is_echo:
        out["echo"] = True
    if visible:
        out["key"] = inst.key
    return out


def _frame_json(state: GameState, frame: FrameState, seat: Team) -> dict[str, Any]:
    from . import effects

    own = frame.seat == seat
    # Ephemeral Images: another seat gets no tile for this frame at all. The
    # three images stand in for it, and which of them it is standing on is the
    # whole card -- so the redaction has to be here, not in the client.
    cloaked = effects.is_cloaked(state, frame)
    show_pos = frame.pos if (own or not cloaked) else None
    committed = [
        _card_json(state, uid, seat, reveal=own)
        for uid in frame.committed
        if state.cards[uid].location == "committed"
    ]
    view: dict[str, Any] = {
        "id": frame.id,
        "seat": frame.seat,
        "name": frame.spec.name,
        "faction": frame.spec.faction,
        "pos": ({"x": show_pos.x, "y": show_pos.y} if show_pos else None),
        "elev": state.elevation(show_pos),
        "alive": frame.alive,
        "armour": {z: frame.spec.armour[z] for z in ZONES},
        "damage": {z: frame.damage[z] for z in ZONES},
        "lastHit": {z: frame.zone_last_hit(z) for z in ZONES},
        "movement": frame.base_movement,
        "shields": frame.shields,
        "statuses": dict(frame.statuses),
        "committed": [c for c in committed if not c["resolved"]],
        "onField": [c for c in committed if c["resolved"]],
        "aside": [
            {"uid": uid, "key": state.cards[uid].key}
            for uid in frame.aside
        ],
        "deckCount": len(frame.deck),
        "discardCount": len(frame.discard),
        "deathstrike": frame.deathstrike_until is not None,
    }
    if cloaked:
        view["cloaked"] = True
    if own:
        view["hand"] = [
            {"uid": uid, "key": state.cards[uid].key} for uid in frame.hand
        ]
    else:
        view["handCount"] = len(frame.hand)
    return view


def _pending_json(
    pending: Optional[PendingDecision], seat: Team
) -> Optional[dict[str, Any]]:
    if pending is None:
        return None
    if pending.seat != seat:
        # The other seat is deciding. Say so, but never say what the options
        # are -- that is how simultaneous planning stays simultaneous.
        return {"seat": pending.seat, "kind": pending.kind, "waiting": True}
    out = {
        "seat": pending.seat,
        "kind": pending.kind,
        "prompt": pending.prompt,
        "frameId": pending.frame_id,
        "options": [dict(option) for option in pending.options],
    }
    # How many of the options make one answer. Only interesting when it is not
    # exactly one -- committing actions, where Hyper raises the ceiling to
    # three. The client must read this rather than assume two, or the extra
    # action the card buys is unspendable.
    if pending.pick_min != 1 or pending.pick_max != 1:
        out["pickMin"] = pending.pick_min
        out["pickMax"] = pending.pick_max
    if pending.pick_kind:
        out["pickKind"] = pending.pick_kind
    return out


def _board_json(state: GameState) -> dict[str, Any]:
    board = state.board
    if board is None:
        return {"width": 0, "height": 0, "tiles": [], "objectives": []}
    tiles = []
    for y in range(board.height):
        for x in range(board.width):
            from .types import Pos

            tile = board.tile(Pos(x, y))
            tiles.append({
                "x": x,
                "y": y,
                "elev": tile.elevation,
                "impassable": tile.impassable,
                "obstacle": tile.obstacle,
                "objective": tile.objective or None,
                # The `tkn` code. The card marks it as plainly as it marks an
                # obstacle, so the board needs it to draw the same thing.
                "tokenSpawn": tile.token_spawn or None,
                "card": tile.terrain_card,
            })
    objectives = []
    for obj in state.objectives:
        seat, value = objectivelib.objective_score(state, obj)
        # Prose for the player; `scorer`/`settled` below for anything that has
        # to act on it. Naming the team rather than a seat number matches the
        # rest of the view -- a frame id already says "Blue" or "Red".
        if obj.latched is not None:
            status = f"scored by {team_name(obj.latched)}"
        elif seat is not None:
            status = f"leaning {team_name(seat)}"
        else:
            status = "unscored"
        objectives.append({
            "name": obj.name,
            "owner": obj.owner,
            "defend": obj.defend,
            "attack": obj.attack,
            "tiles": [[p.x, p.y] for p in obj.tiles],
            "status": status,
            # Who the points go to, as a seat number rather than prose --
            # the final score has to attribute each one to a side, and the AI
            # has to reason about it without parsing English.
            "scorer": seat,
            "settled": obj.latched is not None,
            "value": value,
        })
    return {
        "width": board.width,
        "height": board.height,
        "tiles": tiles,
        "objectives": objectives,
    }


def _token_json(state: GameState, token, seat: Team) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": token.id,
        "kind": token.kind,
        "pos": ({"x": token.pos.x, "y": token.pos.y} if token.pos else None),
        "hp": token.hp,
        "maxHp": token.max_hp,
        "alive": token.alive,
        "carrier": token.carrier,
    }
    if token.owner is not None:
        out["owner"] = token.owner
    from . import effects

    if token.kind == effects.IMAGE:
        found = effects.image_owner(state, token)
        if found is not None:
            frame, real = found
            out["frame"] = frame.id
            # Only the side projecting them knows which one it is standing on.
            if real and frame.seat == seat:
                out["real"] = True
    elif token.kind == fx.DRONE:
        record = fx.slot(state, "drones").get(token.id)
        if record is not None:
            out["frame"] = str(record.get("frame", ""))
            out["card"] = str(record.get("key", ""))
    return out


def view_for(state: GameState, seat: Team) -> dict[str, Any]:
    """The redacted client view for one seat."""
    points = victory_points(state)
    return {
        "gameId": state.game_id,
        "turn": state.turn,
        "phase": state.phase,
        "priority": state.priority,
        "seat": seat,
        "board": _board_json(state),
        "frames": [
            _frame_json(state, frame, seat) for frame in state.frames.values()
        ],
        "tokens": [
            _token_json(state, token, seat) for token in state.tokens.values()
        ],
        "pending": _pending_json(state.pending, seat),
        "log": list(state.log),
        "vp": {str(s): points.get(s, 0) for s in state.seats},
        # Victory points are kills *plus* objectives, and the final score has
        # to be able to say which is which. The kill half is only knowable
        # here: a frame is destroyed once and the point is credited then, so
        # it cannot be recounted from the board afterwards.
        "kills": {str(s): state.kills.get(s, 0) for s in state.seats},
        "over": state.phase == "finished",
    }


# --------------------------------------------------------------------------
# Static catalogue for `GET /api/cards`
# --------------------------------------------------------------------------


def card_json(card: Card) -> dict[str, Any]:
    return {
        "key": card.key,
        "name": card.name,
        "group": card.group,
        "faction": card.faction,
        "type": card.card_type,
        "initiative": list(card.initiative),
        "movement": card.movement,
        "attacks": {z: card.attacks[z] for z in ZONES},
        "ranges": {z: card.ranges[z] for z in ZONES},
        "dtypes": {z: card.dtypes[z] for z in ZONES},
        "blocks": {z: card.blocks[z] for z in ZONES},
        "text": card.text,
        "keywords": sorted(card.keywords),
        "knockback": card.knockback,
        "persistence": card.persistence,
        "image": f"{card.key}.png",
    }


def catalogue_json(catalogue: Mapping[str, Card]) -> dict[str, Any]:
    from . import effects

    deferred = effects.deferred_effects(catalogue)
    out = {}
    for key, card in catalogue.items():
        entry = card_json(card)
        if key in deferred:
            entry["notImplemented"] = deferred[key].text
        out[key] = entry
    return out
