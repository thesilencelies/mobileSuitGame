"""In-process game registry and the server loop.

The loop is the one in SPEC.md: apply the human's command, then keep asking the
AI for commands while the pending decision belongs to the AI seat, then hand
back `view_for(state, human_seat)`. A `GameState` is never serialised directly
-- every response goes through `view_for`, so the redaction is the engine's
and not this module's.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from ..engine import (
    Command,
    GameConfig,
    GameState,
    IllegalCommand,
    apply_command,
    available_decks,
    is_over,
    legal_commands,
    new_game,
    scores,
    view_for,
    watching,
)
from . import ai_bridge, readouts

#: How many human decisions can be stepped back through `POST /undo`.
UNDO_DEPTH = 40

#: Safety net: the AI is asked for at most this many decisions in a row.
MAX_AI_STEPS = 4000

#: The AI's whole turn happens inside one `POST /command`, so by the time the
#: human sees a view again several frames have moved, attacked and died. The
#: server keeps a lightweight snapshot at each beat of that turn and ships them
#: with the response as `replay`, and the client plays them back at a speed the
#: player picks -- otherwise the AI's turn is a single jump-cut.
#:
#: A beat is not the same thing as an AI *decision*. Plenty of what the AI does
#: needs no decision at all -- a card with one legal target, an effect with no
#: choices, a card that only blocks -- and those used to be folded silently
#: into the next decision's snapshot, which is exactly the part players
#: reported as mysterious. So the engine is watched (`engine.watching`) and a
#: snapshot is taken whenever an AI frame reveals a card, moves, resolves an
#: effect or lands an attack, in addition to after each AI decision.
#:
#: The board is 240 tiles and never changes mid-turn, so a snapshot drops it;
#: what is left is a couple of kB. This caps how many are kept, newest wins,
#: because a very long AI turn is not worth megabytes on a phone.
MAX_REPLAY_FRAMES = 60


class GameNotFound(KeyError):
    pass


@dataclass
class Session:
    """One game plus everything the server keeps around it."""

    id: str
    state: GameState
    human_seat: int
    ai_seat: int
    ai_params: dict[str, Any]
    config: dict[str, Any]
    agent: Any
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    history: list[GameState] = field(default_factory=list)
    ai_source: str = "fallback"
    lock: threading.Lock = field(default_factory=threading.Lock)
    #: Snapshots of what the AI did since the human last acted.
    replay: list[dict[str, Any]] = field(default_factory=list)
    #: The frames as of the last snapshot taken, so the next one can say what
    #: *changed* -- who moved from where, which zone took how much. A snapshot
    #: on its own is a still; the marks the client draws need the difference.
    prev_frames: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Digest of the last snapshot appended, so a beat and the decision that
    #: follows it do not both record the same moment.
    last_digest: str = ""

    def view(self, *, with_replay: bool = True) -> dict[str, Any]:
        """The human seat's redacted view, with the server's game id on it."""
        out = view_for(self.state, self.human_seat)
        # The engine derives `game_id` from the seed, so two games started with
        # the same seed share it. The registry key is ours and is authoritative.
        out["gameId"] = self.id
        out["humanSeat"] = self.human_seat
        out["aiSeat"] = self.ai_seat
        out["aiSource"] = self.ai_source
        out["over"] = is_over(self.state)
        out["scores"] = {str(k): v for k, v in scores(self.state).items()}
        out["legal"] = [
            {"kind": c.kind, "payload": dict(c.payload)}
            for c in legal_commands(self.state, self.human_seat)
        ]
        out["resolving"] = readouts.resolving(self.state, self.human_seat)
        out["defence"] = readouts.defence_all(self.state, self.human_seat)
        out["initiative"] = self._initiative()
        if with_replay:
            out["replay"] = list(self.replay)
        return out

    def _initiative(self) -> dict[str, int]:
        """`{uid: initiative}` for every committed card this seat can identify.

        The *effective* number -- what the engine will actually queue on -- for
        the cards whose identity this seat already has. A face-down card the
        seat may not read is absent rather than guessed at: its uid in the view
        is an opaque stand-in anyway.
        """
        out: dict[str, int] = {}
        for frame in self.state.frames.values():
            own = frame.seat == self.human_seat
            for uid in frame.committed:
                inst = self.state.cards.get(uid)
                if inst is None or inst.location != "committed":
                    continue
                if inst.face_down and not own:
                    continue
                value = readouts.initiative_of(self.state, frame, uid)
                if value is not None:
                    out[uid] = value
        return out

    def snapshot(
        self, state: Optional[GameState] = None, event: str = ""
    ) -> dict[str, Any]:
        """One replay frame: what moved, and nothing that could identify a card.

        Built from `view_for` like everything else, then narrowed twice over.

        The board is 240 unchanging tiles, so it goes. **Card uids go too**, and
        that is not just size: a snapshot is a picture of the *past*, and a card
        that was face up while it resolved can be discarded, reshuffled and
        drawn again -- so replaying its uid would hand the player a handle on a
        card that is in the AI's hand *now*. Its `key` is already public (the
        log says which card resolved), but without a uid there is nothing to
        tie that to a face-down commitment. Counts replace the card rows, since
        an animation only needs to know how many cards are standing.

        `state` is the live state a watcher was handed, which during
        `apply_command` is *not* `self.state` -- the engine works on a private
        copy and only the finished one is assigned back.
        """
        state = self.state if state is None else state
        full = view_for(state, self.human_seat)
        frames = [_slim_frame(frame) for frame in full["frames"]]
        return {
            "turn": full["turn"],
            "phase": full["phase"],
            "frames": frames,
            "tokens": full["tokens"],
            "log": full["log"],
            "vp": full["vp"],
            "resolving": _without_uids(
                readouts.resolving(state, self.human_seat)),
            "beat": self._delta(event, frames),
        }

    def _delta(
        self, event: str, frames: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """What changed since the previous snapshot, in board terms.

        The client draws the rulebook's own marks from this -- a dashed line
        from where a frame was to where it is, a burst on each zone that took
        damage -- so it is the *difference* that matters, not the still.

        A frame the seat may not see (Ephemeral Images hides its position) has
        no `pos` in the view at all, so it contributes no move line. The
        redaction is upstream and this cannot leak around it.
        """
        moves = []
        hits = []
        dead = []
        for frame in frames:
            before = self.prev_frames.get(frame["id"])
            if before is None:
                continue
            was, now = before.get("pos"), frame.get("pos")
            if was and now and (was["x"], was["y"]) != (now["x"], now["y"]):
                moves.append({"id": frame["id"], "from": was, "to": now})
            for zone, amount in (frame.get("damage") or {}).items():
                gained = int(amount) - int((before.get("damage") or {}).get(zone, 0))
                if gained > 0:
                    hits.append({"id": frame["id"], "zone": zone, "amount": gained})
            if before.get("alive") and not frame.get("alive"):
                # A destroyed frame loses its position, so the tile to mark is
                # the one it was standing on a moment ago.
                dead.append({"id": frame["id"], "pos": before.get("pos")})
        return {"event": event, "moves": moves, "hits": hits, "dead": dead}

    def record(self, state: GameState, event: str = "") -> None:
        """Append a replay frame, unless it would repeat the one before it.

        Two paths lead here -- the engine's beats and the loop that drives the
        AI -- and they meet on the last beat of a decision, which both would
        otherwise record. Comparing the snapshot itself (bar its delta, which
        is derived) is the honest test of "nothing new to see".
        """
        snap = self.snapshot(state, event)
        digest = json.dumps(
            {k: v for k, v in snap.items() if k != "beat"}, sort_keys=True
        )
        if digest == self.last_digest:
            return
        self.last_digest = digest
        self.prev_frames = {f["id"]: f for f in snap["frames"]}
        self.replay.append(snap)
        del self.replay[:-MAX_REPLAY_FRAMES]

    def reset_replay(self) -> None:
        """Forget the recorded turn and re-baseline the diff on the present."""
        self.replay.clear()
        self.last_digest = ""
        self.rebase()

    def rebase(self, state: Optional[GameState] = None) -> None:
        """Move the diff's baseline to now without recording anything.

        For the beats that are *not* replayed. Without this, everything they
        changed would surface on the next beat that is -- the player's own
        attack landing would draw its burst on top of whatever the AI did
        next, which is worse than not drawing it at all.
        """
        state = self.state if state is None else state
        self.prev_frames = {
            frame["id"]: frame
            for frame in (
                _slim_frame(entry)
                for entry in view_for(state, self.human_seat)["frames"]
            )
        }

    def watcher(self, *, mine: bool = False):
        """The callback for `engine.watching`.

        By default only the AI's frames are recorded: the player's cards
        resolve under their own hand and are already on screen as they happen,
        so replaying those would put a delay between a tap and its result.

        But "under their own hand" is not always true. A card with one legal
        target, an effect with no choices, a move with nowhere else to go --
        those resolve inside the same call with nothing asked, and the player
        sees the aftermath and not the act. `mine=True` records those as well,
        which is what the client's "replay my own actions too" setting asks
        for.
        """
        def observe(state: GameState, event: str) -> None:
            res = getattr(state, "resolution", None)
            frame = state.frames.get(res.frame_id) if res is not None else None
            watched = frame is not None and (
                frame.seat == self.ai_seat or mine)
            if watched:
                self.record(state, event)
            else:
                self.rebase(state)

        return observe


def _slim_frame(frame: Mapping[str, Any]) -> dict[str, Any]:
    slim = {
        key: frame[key] for key in (
            "id", "seat", "name", "pos", "elev", "alive", "armour",
            "damage", "lastHit", "movement", "shields", "statuses",
            "deckCount", "discardCount", "cloaked",
        ) if key in frame
    }
    slim["committedCount"] = len(frame.get("committed") or [])
    slim["onFieldCount"] = len(frame.get("onField") or [])
    return slim


def _without_uids(blob: Any) -> Any:
    """The same structure with every `uid` key removed, at any depth."""
    if isinstance(blob, dict):
        return {k: _without_uids(v) for k, v in blob.items() if k != "uid"}
    if isinstance(blob, list):
        return [_without_uids(item) for item in blob]
    return blob


class Registry:
    """All live games, keyed by id. One process, one dict, no database."""

    def __init__(self) -> None:
        self._games: dict[str, Session] = {}
        self._lock = threading.Lock()

    # -- lifecycle -------------------------------------------------------

    def create(
        self,
        *,
        player_decks: list[str],
        ai_decks: list[str],
        seed: Optional[int] = None,
        frames_per_side: int = 3,
        ai_params: Optional[Mapping[str, Any]] = None,
        human_seat: int = 0,
        terrain_decks: Optional[Mapping[int, str]] = None,
    ) -> Session:
        # Pass the client's parameters through untouched: the AI package
        # applies its own defaults and its own `preset` handling, and merging
        # our copy of the defaults on top would silently override a preset.
        params = dict(ai_params or {})
        config = GameConfig(
            player_decks=list(player_decks),
            ai_decks=list(ai_decks),
            seed=seed,
            frames_per_side=frames_per_side,
            ai_params=params,
            # A seat with no battlefield named gets one dealt from the shipped
            # pairs by the game's own rng, which is the old behaviour.
            terrain_decks=dict(terrain_decks or {}) or None,
        )
        state = new_game(config)
        agent = ai_bridge.make_agent(
            params, seat=1 - human_seat, seed=int(seed or 0)
        )
        session = Session(
            id=uuid.uuid4().hex[:12],
            state=state,
            human_seat=human_seat,
            ai_seat=1 - human_seat,
            ai_params=params,
            config={
                "playerDecks": list(player_decks),
                "aiDecks": list(ai_decks),
                "seed": seed,
                "framesPerSide": frames_per_side,
                "terrainDecks": dict(terrain_decks or {}),
            },
            agent=agent,
            ai_source=getattr(agent, "source", "fallback"),
        )
        with self._lock:
            self._games[session.id] = session
        advance_ai(session)
        # Setup is not a thing to watch happen; the first view the player gets
        # should be the board as it stands, not an animation of the deal.
        session.reset_replay()
        return session

    def get(self, game_id: str) -> Session:
        with self._lock:
            session = self._games.get(game_id)
        if session is None:
            raise GameNotFound(game_id)
        return session

    def drop(self, game_id: str) -> None:
        with self._lock:
            self._games.pop(game_id, None)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            sessions = list(self._games.values())
        return [
            {
                "gameId": s.id,
                "turn": s.state.turn,
                "phase": s.state.phase,
                "over": is_over(s.state),
                "created": s.created,
                "updated": s.updated,
                "config": s.config,
            }
            for s in sorted(sessions, key=lambda s: -s.updated)
        ]

    # -- play ------------------------------------------------------------

    def command(
        self,
        game_id: str,
        kind: str,
        payload: Mapping[str, Any],
        *,
        replay_mine: bool = False,
    ) -> Session:
        session = self.get(game_id)
        with session.lock:
            pending = session.state.pending
            if pending is None:
                raise IllegalCommand("the game is not waiting for a decision")
            if pending.seat != session.human_seat:
                raise IllegalCommand(
                    f"seat {pending.seat} is deciding, not you"
                )
            session.history.append(session.state)
            del session.history[:-UNDO_DEPTH]
            session.reset_replay()
            cmd = Command(kind, session.human_seat, dict(payload))
            # The watch covers the human's own command as well as the AI loop:
            # answering a block declares the damage, and the AI's next few
            # cards can resolve, all inside this one `apply_command`.
            with watching(session.watcher(mine=replay_mine)):
                try:
                    session.state = apply_command(session.state, cmd)
                except IllegalCommand:
                    session.history.pop()
                    session.reset_replay()
                    raise
                except (KeyError, ValueError, TypeError) as exc:
                    session.history.pop()
                    session.reset_replay()
                    raise IllegalCommand(str(exc) or type(exc).__name__) from exc
                _drive_ai(session)
            session.updated = time.time()
        return session

    def retune(self, game_id: str, ai_params: Mapping[str, Any]) -> Session:
        """Swap the AI's parameters mid-game (the settings drawer)."""
        session = self.get(game_id)
        with session.lock:
            session.ai_params = dict(ai_params)
            session.agent = ai_bridge.make_agent(
                session.ai_params, seat=session.ai_seat,
                seed=int(session.config.get("seed") or 0),
            )
            session.ai_source = getattr(session.agent, "source", "fallback")
            session.state.note("AI parameters changed")
            session.updated = time.time()
        return session

    def undo(self, game_id: str) -> Session:
        session = self.get(game_id)
        with session.lock:
            if not session.history:
                raise IllegalCommand("nothing to undo")
            session.state = session.history.pop()
            session.reset_replay()
            session.updated = time.time()
        return session


def advance_ai(session: Session) -> None:
    """Let the AI seat act until the human is on the clock (or the game ends).

    A snapshot is taken after every AI decision that wrote to the log, so the
    client can replay the AI's turn at a readable speed instead of being handed
    the end state. Decisions that change nothing visible (a face-down commit)
    add no snapshot -- there would be nothing to watch.

    The engine's own beats (see `Session.watcher`) fill in everything between
    those decisions. `Session.record` drops a snapshot that repeats the one
    before it, so the two sources overlapping on the last beat of a decision
    costs nothing.
    """
    with watching(session.watcher()):
        _drive_ai(session)


def _drive_ai(session: Session) -> None:
    """The loop itself, for a caller that already has the watch open."""
    steps = 0
    logged = len(session.state.log)
    while True:
        state = session.state
        if is_over(state):
            break
        pending = state.pending
        if pending is None or pending.seat != session.ai_seat:
            break
        steps += 1
        if steps > MAX_AI_STEPS:               # pragma: no cover - safety net
            raise RuntimeError("AI failed to make progress")
        ai_cmd = _ai_command(session)
        session.state = apply_command(state, ai_cmd)
        if len(session.state.log) != logged:
            logged = len(session.state.log)
            session.record(session.state, "decision")


def _ai_command(session: Session) -> Command:
    """One AI decision, with a legal fallback if the agent misbehaves."""
    state = session.state
    seat = session.ai_seat
    options = legal_commands(state, seat)
    if not options:
        raise RuntimeError(
            f"engine offered no legal command for the AI seat "
            f"({state.pending.kind if state.pending else 'none'})"
        )
    try:
        choice = session.agent.choose(state, seat)
    except Exception as exc:                   # a broken agent must not 500
        session.state.note(f"AI error ({exc}); falling back to the first legal move")
        return options[0]
    if not isinstance(choice, Command):
        return options[0]
    if choice.seat != seat:
        choice = Command(choice.kind, seat, choice.payload)
    return choice


#: The one registry the app uses.
REGISTRY = Registry()


def default_decks(count: int = 3) -> tuple[list[str], list[str]]:
    """A sensible pair of squads when the client does not name any."""
    names = available_decks()
    if not names:
        return [], []
    player = [names[i % len(names)] for i in range(count)]
    ai = [names[(i + count) % len(names)] for i in range(count)]
    return player, ai
