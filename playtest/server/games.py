"""In-process game registry and the server loop.

The loop is the one in SPEC.md: apply the human's command, then keep asking the
AI for commands while the pending decision belongs to the AI seat, then hand
back `view_for(state, human_seat)`. A `GameState` is never serialised directly
-- every response goes through `view_for`, so the redaction is the engine's
and not this module's.
"""

from __future__ import annotations

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
)
from . import ai_bridge, readouts

#: How many human decisions can be stepped back through `POST /undo`.
UNDO_DEPTH = 40

#: Safety net: the AI is asked for at most this many decisions in a row.
MAX_AI_STEPS = 4000

#: The AI's whole turn happens inside one `POST /command`, so by the time the
#: human sees a view again several frames have moved, attacked and died. The
#: server keeps a lightweight snapshot after each AI decision and ships them
#: with the response as `replay`, and the client plays them back at a speed the
#: player picks -- otherwise the AI's turn is a single jump-cut.
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
    #: Snapshots of the AI's decisions since the human last acted.
    replay: list[dict[str, Any]] = field(default_factory=list)

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

    def snapshot(self) -> dict[str, Any]:
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
        """
        full = view_for(self.state, self.human_seat)
        frames = []
        for frame in full["frames"]:
            slim = {
                key: frame[key] for key in (
                    "id", "seat", "name", "pos", "elev", "alive", "armour",
                    "damage", "lastHit", "movement", "shields", "statuses",
                    "deckCount", "discardCount",
                ) if key in frame
            }
            slim["committedCount"] = len(frame.get("committed") or [])
            slim["onFieldCount"] = len(frame.get("onField") or [])
            frames.append(slim)
        return {
            "turn": full["turn"],
            "phase": full["phase"],
            "frames": frames,
            "tokens": full["tokens"],
            "log": full["log"],
            "vp": full["vp"],
            "resolving": _without_uids(
                readouts.resolving(self.state, self.human_seat)),
        }


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
            },
            agent=agent,
            ai_source=getattr(agent, "source", "fallback"),
        )
        with self._lock:
            self._games[session.id] = session
        advance_ai(session)
        # Setup is not a thing to watch happen; the first view the player gets
        # should be the board as it stands, not an animation of the deal.
        session.replay.clear()
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

    def command(self, game_id: str, kind: str, payload: Mapping[str, Any]) -> Session:
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
            session.replay.clear()
            cmd = Command(kind, session.human_seat, dict(payload))
            try:
                session.state = apply_command(session.state, cmd)
            except IllegalCommand:
                session.history.pop()
                raise
            except (KeyError, ValueError, TypeError) as exc:
                session.history.pop()
                raise IllegalCommand(str(exc) or type(exc).__name__) from exc
            advance_ai(session)
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
            session.replay.clear()
            session.updated = time.time()
        return session


def advance_ai(session: Session) -> None:
    """Let the AI seat act until the human is on the clock (or the game ends).

    A snapshot is taken after every AI decision that wrote to the log, so the
    client can replay the AI's turn at a readable speed instead of being handed
    the end state. Decisions that change nothing visible (a face-down commit)
    add no snapshot -- there would be nothing to watch.
    """
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
        session.state = apply_command(state, _ai_command(session))
        if len(session.state.log) != logged:
            logged = len(session.state.log)
            session.replay.append(session.snapshot())
            del session.replay[:-MAX_REPLAY_FRAMES]


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
