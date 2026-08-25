"""Turn and phase state machine, and the initiative queue.

`advance()` drives the game forward until it needs a decision, at which point
it parks a `PendingDecision` on the state and returns. Every decision -- human
or AI -- comes back in through `handle_command()`. That is the only way the
game moves, which is what makes an AI-vs-AI headless game the same code path
as a human game.

Turn structure (rules.tex:366): planning -> action -> cleanup, five times.
"""

from __future__ import annotations

import contextlib
import itertools
import random
import threading
import uuid
from typing import Any, Callable, Iterable, Iterator, Mapping, Optional, Sequence

from . import cards as cardlib
from . import combat
from . import effects
from . import keywords as kw
from . import objectives as objectivelib
from .state import (
    CardInstance,
    FrameState,
    GameState,
    Resolution,
    apply_status,
    discard_card,
    draw,
    destroy_frame,
    move_card,
    tick_statuses,
    victory_points,
)
from .types import (
    ACTIONS_PER_TURN,
    TURNS_PER_GAME,
    Card,
    Command,
    GameConfig,
    PendingDecision,
    Pos,
    Team,
    frame_id_for,
    team_name,
    Tile,
    ZONES,
)

STEPS = ("movement", "effect", "attack")


class IllegalCommand(ValueError):
    """The command was not one the engine offered."""


# --------------------------------------------------------------------------
# Watching the game happen
# --------------------------------------------------------------------------
#
# A single `apply_command` can cover a great deal: the AI's whole turn runs
# inside the call that answers the human's last decision, and cards that need
# no decision resolve invisibly inside it. To a player that is a jump cut --
# three frames have moved, something is on fire and nothing said so.
#
# `watching()` lets a caller see the beats in between. The callback is handed
# the live state at each moment worth looking at, and is expected to *read* it
# (the server takes a redacted snapshot) and nothing else -- an observer that
# mutates the state would break the purity `apply_command` promises.
#
# Watchers are per-thread: the server runs one game per request thread, and a
# global list would hand one game's callback another game's state.

#: `(state, event) -> None`. `event` is one of "card", "move", "effect",
#: "attack" -- the beat that just finished.
Watcher = Callable[[GameState, str], None]

_watchers = threading.local()


@contextlib.contextmanager
def watching(callback: Watcher) -> Iterator[None]:
    """Call `callback(state, event)` at each beat of resolution, in this thread."""
    active = getattr(_watchers, "list", None)
    if active is None:
        active = []
        _watchers.list = active
    active.append(callback)
    try:
        yield
    finally:
        active.remove(callback)


def _beat(state: GameState, event: str) -> None:
    for callback in list(getattr(_watchers, "list", None) or ()):
        callback(state, event)


# --------------------------------------------------------------------------
# Featureless board (tests only)
# --------------------------------------------------------------------------


class FlatBoard:
    """A featureless `BoardProtocol` for tests that do not care about terrain.

    Real games use `engine.board.Board`; this is **not** a fallback for it.
    Its geometry (adjacency, Chebyshev range, movement with elevation and
    obstacle costs) is real, so a test can build a board by editing tiles --
    but it has no terrain to reason about, so it cannot judge line of sight
    and refuses to pretend otherwise. See `has_line_of_sight`.
    """

    def __init__(
        self,
        width: int = 15,
        height: int = 16,
        *,
        los_always_clear: bool = False,
    ) -> None:
        self.width = width
        self.height = height
        #: Opt-in: answer every line-of-sight question with "clear". Only a
        #: caller that has decided it does not care about LoS may ask for
        #: this; the default refuses rather than inventing an answer.
        self.los_always_clear = los_always_clear
        self._tiles = {
            Pos(x, y): Tile(Pos(x, y))
            for x in range(width) for y in range(height)
        }

    def tile(self, pos: Pos) -> Tile:
        return self._tiles.get(pos, Tile(pos))

    def in_bounds(self, pos: Pos) -> bool:
        return 0 <= pos.x < self.width and 0 <= pos.y < self.height

    def neighbours(self, pos: Pos) -> list[Pos]:
        out = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx or dy:
                    nxt = Pos(pos.x + dx, pos.y + dy)
                    if self.in_bounds(nxt):
                        out.append(nxt)
        return out

    def distance(self, a: Pos, b: Pos) -> int:
        return max(abs(a.x - b.x), abs(a.y - b.y))

    def reachable(self, start, budget, *, occupied=frozenset(), flying=False):
        seen = {start: 0}
        frontier = [start]
        while frontier:
            pos = frontier.pop(0)
            cost = seen[pos]
            if cost >= budget:
                continue
            for nxt in self.neighbours(pos):
                if nxt in occupied or self.tile(nxt).impassable:
                    continue
                if not flying and self.tile(nxt).obstacle:
                    continue
                step = cost + 1
                if not flying:
                    climb = self.tile(nxt).elevation - self.tile(pos).elevation
                    if climb > 0:
                        step += climb
                if step <= budget and step < seen.get(nxt, 10**6):
                    seen[nxt] = step
                    frontier.append(nxt)
        return seen

    def path(self, start, goal, budget, *, occupied=frozenset(), flying=False):
        reach = self.reachable(start, budget, occupied=occupied, flying=flying)
        return [start, goal] if goal in reach else None

    def has_line_of_sight(self, attacker, target, *, occupied=frozenset(),
                          flying_attacker=False, flying_target=False):
        """Refuses to answer unless the caller opted into `los_always_clear`.

        Line of sight is a load-bearing rule -- it gates every ranged attack,
        the AI's threat model and the client's shading. A featureless board
        answering "clear" to everything would not fail; it would quietly play
        a different game. So this raises instead of guessing.
        """
        if self.los_always_clear:
            return True
        raise NotImplementedError(
            "FlatBoard has no terrain and cannot judge line of sight. Use "
            "engine.board.Board for a real game, or construct "
            "FlatBoard(los_always_clear=True) to state explicitly that this "
            "caller does not care about LoS."
        )


#: The server or workstream B1 can install a real battlefield builder here.
#: It is called as `factory(state, config)` and must set `state.board`, place
#: frames, and register objectives via `objectives.create_objective`.
BoardFactory = Callable[[GameState, GameConfig], None]
_BOARD_FACTORY: Optional[BoardFactory] = None


def set_board_factory(factory: Optional[BoardFactory]) -> None:
    global _BOARD_FACTORY
    _BOARD_FACTORY = factory


def _real_battlefield(state: GameState, config: GameConfig) -> None:
    """Deal a real board from the terrain decks (workstream B1's `setup.py`).

    Terrain decks come from `config.terrain_decks`, or are dealt round-robin
    from the shipped `decks/deck_terrain_*.csv` using the game's own rng, so
    the whole battlefield is reproducible from the seed.
    """
    from . import setup as _setup

    available = list(_setup.available_deck_pairs())
    chosen = dict(config.terrain_decks or {})
    pool = [name for name in available if name not in chosen.values()]
    state.rng.shuffle(pool)
    decks = {}
    for seat in state.seats:
        name = chosen.get(seat) or (pool.pop() if pool else available[0])
        # One call loads and validates the 10-card terrain deck and its
        # 5-card objective deck (rules.tex:253).
        decks[seat] = _setup.load_deck_pair(name)
        state.note(f"{team_name(seat)} brings terrain deck {name}")

    field = _setup.deal_battlefield(
        decks, rng=state.rng, frames_per_side=config.frames_per_side
    )
    state.board = field.board
    for info in field.objectives:
        objectivelib.create_objective(
            state,
            info.name,
            info.owner,
            defend=info.defend_points,
            attack=info.attack_points,
            tiles=info.tiles,
            spawns=info.token_tiles,
        )
    # Deployment is a real decision, one frame at a time, alternating seats
    # (rules.tex Setup). `state.queue` holds the seats still to place.
    # "the player who deployed first receives the priority marker".
    order = _setup.deployment_order(config.frames_per_side, first_seat=state.seats[0])
    state.queue = list(order)
    state.priority = order[0] if order else state.seats[0]


def deployment_tiles(state: GameState, seat: Team) -> list[Pos]:
    """Legal, unoccupied deployment tiles for `seat` -- its own nearest edge."""
    from . import setup as _setup

    return [
        pos for pos in _setup.deployment_tiles(state.board, seat)
        if state.frame_at(pos) is None
    ]


def _place_fugitive(state: GameState) -> None:
    """"Put a fugitive token anywhere in the enemy back row after deployment."

    The Fugitive card carries no `tkn` cell, so the token does not start on the
    card: it starts in the *attacker's* back row and the defender must escort
    it to the objective tile. Runs once deployment is finished, so it can avoid
    the tiles frames actually took.
    """
    for objective in state.objectives:
        if objective.name != "Fugitive":
            continue
        enemy = objectivelib.other_seat(state, objective.owner)
        free = deployment_tiles(state, enemy)
        if not free:
            continue
        for token_id in objective.token_ids:
            state.tokens[token_id].pos = free[len(free) // 2]


def _build_battlefield(state: GameState, config: GameConfig) -> None:
    """Deal the battlefield, or fail. There is deliberately no fallback.

    This used to swallow any exception from `_real_battlefield` and carry on
    with a featureless board. That is the worst possible failure mode: the
    game would keep playing, but on flat ground with unlimited line of sight,
    silently producing wrong rules. A broken setup must stop the game.
    """
    if _BOARD_FACTORY is not None:
        _BOARD_FACTORY(state, config)
    else:
        _real_battlefield(state, config)
    if state.board is None:
        raise RuntimeError(
            "the board factory did not set state.board -- a game cannot be "
            "played without a battlefield"
        )


# --------------------------------------------------------------------------
# New game
# --------------------------------------------------------------------------


def _opening_hand(deck_name: str) -> list[str]:
    """`decks/hands/<deck>_hand.csv`, if the repo ships one."""
    path = cardlib.DECK_DIR / "hands" / f"{cardlib.deck_path(deck_name).stem}_hand.csv"
    if not path.exists():
        return []
    return cardlib.read_deck_keys(path)


def _seat_frame_ids(
    seat: Team, specs: Sequence[Any]
) -> list[str]:
    """Ids for one seat's frames: model name, numbered only where it repeats."""
    total: dict[str, int] = {}
    for spec in specs:
        total[spec.name] = total.get(spec.name, 0) + 1
    seen: dict[str, int] = {}
    out: list[str] = []
    for spec in specs:
        seen[spec.name] = seen.get(spec.name, 0) + 1
        ordinal = seen[spec.name] if total[spec.name] > 1 else None
        out.append(frame_id_for(seat, spec.name, ordinal))
    return out


def _build_frame(
    state: GameState,
    seat: Team,
    frame_id: str,
    spec: Any,
    deck_name: str,
    catalogue: Mapping[str, Card],
) -> FrameState:
    frame = FrameState(id=frame_id, seat=seat, spec=spec)
    frame.shields = spec.shield

    deck = cardlib.load_deck(deck_name, catalogue)
    uids: list[str] = []
    for card in deck:
        uid = state.next_uid()
        state.cards[uid] = CardInstance(uid=uid, key=card.key, owner=frame_id)
        uids.append(uid)
    state.rng.shuffle(uids)

    # "choose your starting hand of 7, shuffle the deck then put those 7 on top"
    wanted = _opening_hand(deck_name)
    if wanted:
        chosen: list[str] = []
        pool = list(uids)
        for key in wanted:
            for uid in pool:
                if state.cards[uid].key == key:
                    chosen.append(uid)
                    pool.remove(uid)
                    break
        uids = chosen + pool
    frame.deck = uids
    return frame


def new_game(config: GameConfig) -> GameState:
    """Build a fresh game. Deterministic given `config.seed`."""
    catalogue = cardlib.load_cards()
    frame_specs = cardlib.load_frames()
    seed = config.seed if config.seed is not None else random.randrange(2 ** 31)
    state = GameState(
        game_id=uuid.UUID(int=random.Random(seed).getrandbits(128)).hex[:12],
        rng=random.Random(seed),
        catalogue=catalogue,
        board=None,
        seats=(0, 1),
        turn=1,
        phase="setup",
        priority=0,
    )
    state.kills = {seat: 0 for seat in state.seats}
    for seat, decks in ((0, config.player_decks), (1, config.ai_decks)):
        chosen = list(decks[: config.frames_per_side])
        specs = []
        for deck_name in chosen:
            spec = cardlib.frame_for_deck(deck_name, frame_specs)
            if spec is None:
                raise ValueError(f"cannot tell which frame {deck_name!r} belongs to")
            specs.append(spec)
        for frame_id, spec, deck_name in zip(
            _seat_frame_ids(seat, specs), specs, chosen
        ):
            frame = _build_frame(
                state, seat, frame_id, spec, deck_name, catalogue
            )
            state.frames[frame.id] = frame
    _build_battlefield(state, config)
    state.seed = seed
    state.note(f"game {state.game_id} begins")
    # The seed stays out of the public log: with the shipped deck CSVs it
    # would let either player replay the rng and read the other's deck order
    # and future draws.
    state.note_private(f"game {state.game_id} seed {seed}")
    state.phase = "setup"
    return state


# --------------------------------------------------------------------------
# Setup: deployment
# --------------------------------------------------------------------------


def _undeployed(state: GameState, seat: Team) -> list[FrameState]:
    return [f for f in state.frames_of(seat, alive_only=False) if f.pos is None]


def _deploy_decision(state: GameState) -> bool:
    """Ask the next seat in the deployment order to place one frame.

    "Each player takes it in turns to put one of their frames on nearest edge
    of their terrain cards" (rules.tex Setup) -- so the seat chooses both
    *which* frame and *where*, and the options enumerate the legal pairs.
    """
    while state.queue:
        seat = state.queue[0]
        frames = _undeployed(state, seat)
        tiles = deployment_tiles(state, seat)
        if not frames or not tiles:
            state.queue.pop(0)
            continue
        options = [
            {"frame": frame.id, "name": frame.spec.name, "x": pos.x, "y": pos.y}
            for frame in frames
            for pos in tiles
        ]
        state.pending = PendingDecision(
            kind="deploy",
            seat=seat,
            prompt=f"Deploy a frame on your edge ({len(frames)} left)",
            options=options,
        )
        return True
    return False


def _finish_setup(state: GameState) -> None:
    _place_fugitive(state)
    state.queue = []
    state.note("deployment complete")
    _begin_planning(state)


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


def _begin_planning(state: GameState) -> None:
    state.phase = "planning"
    state.resolution = None
    for frame in state.frames.values():
        frame.turn_flags = {}
        frame.moved_this_turn = False
    for frame in state.frames.values():
        if not frame.alive:
            continue
        drawn = draw(state, frame, frame.draw_count)
        state.note(f"{frame.id} draws {len(drawn)}")
    state.queue = [f.id for f in state.frames.values() if f.alive]
    state.note(f"--- turn {state.turn}: planning ---")


def _planning_decision(state: GameState) -> bool:
    """Emit the next planning decision. True if one was raised."""
    while state.queue:
        frame_id = state.queue[0]
        frame = state.frames[frame_id]
        if not frame.alive:
            state.queue.pop(0)
            continue
        # Kuwagata: "once, during planning; discard your hand and draw a new one"
        if (
            frame.spec.name == "Kuwagata"
            and not frame.mulligan_used
            and not frame.turn_flags.get("mulligan_offered")
        ):
            frame.turn_flags["mulligan_offered"] = True
            state.pending = PendingDecision(
                kind="effect_choice",
                seat=frame.seat,
                prompt=f"{frame.id}: discard your hand and draw a new one?",
                options=[{"mulligan": True}, {"mulligan": False}],
                frame_id=frame.id,
            )
            return True
        pool = effects.commit_pool(state, frame)
        low, high = _commit_range(state, frame, len(pool))
        how_many = str(low) if low == high else f"{low}-{high}"
        state.pending = PendingDecision(
            kind="commit_actions",
            seat=frame.seat,
            prompt=f"Commit {how_many} actions for {frame.id}",
            options=[
                {"uid": uid, "key": state.cards[uid].key} for uid in pool
            ],
            frame_id=frame.id,
            pick_min=low,
            pick_max=high,
        )
        return True
    return _echo_decision(state)


def _echo_decision(state: GameState) -> bool:
    """Echoes of the fallen (rules.tex:386): one offer per defeated frame."""
    for seat in state.seat_cycle():
        alive = [f for f in state.frames_of(seat) if f.alive]
        if not alive:
            continue
        for dead in state.frames.values():
            if dead.seat != seat or dead.alive:
                continue
            if dead.turn_flags.get("echo_offered_turn") == state.turn:
                continue
            if not dead.deck and not dead.discard:
                continue
            dead.turn_flags["echo_offered_turn"] = state.turn
            options: list[dict] = [
                {"dead": dead.id, "host": host.id, "hostName": host.spec.name}
                for host in alive
            ]
            options.append({"decline": True})
            state.pending = PendingDecision(
                kind="echo_card",
                seat=seat,
                prompt=f"Echo of {dead.id}: set its top card beside an ally?",
                options=options,
                frame_id=dead.id,
            )
            return True
    return False


def _end_planning(state: GameState) -> None:
    """Status counters come off at the *end* of planning (rules.tex:391)."""
    for frame in state.frames.values():
        tick_statuses(frame)
    for frame in state.frames.values():
        for uid in list(frame.hand):
            move_card(state, uid, "discard")
    state.phase = "action"
    state.tie_value = None
    state.tie_index = 0
    state.note(f"--- turn {state.turn}: action ---")


# --------------------------------------------------------------------------
# Initiative queue
# --------------------------------------------------------------------------


def _actors(state: GameState) -> list[tuple[int, FrameState, str]]:
    """Every card still waiting to act, with its current initiative."""
    out: list[tuple[int, FrameState, str]] = []
    for frame in state.frames.values():
        if not frame.alive:
            continue
        for uid in list(frame.committed):
            inst = state.cards[uid]
            if inst.location != "committed" or inst.is_echo:
                continue
            card = state.catalogue[inst.key]
            if inst.init_index >= len(card.initiative):
                continue
            out.append(
                (kw.effective_initiative(state, frame, card, inst.init_index),
                 frame, uid)
            )
    return out


def next_actor(state: GameState) -> Optional[tuple[FrameState, str]]:
    """Highest initiative first; ties alternate clockwise from the priority marker.

    The alternation is sticky across resolutions at the same initiative value,
    so two cards from the same seat at the same value do not resolve back to
    back while the opponent still has one waiting.
    """
    actors = _actors(state)
    if not actors:
        return None
    best = max(value for value, _, _ in actors)
    tied = [(f, uid) for value, f, uid in actors if value == best]
    cycle = state.seat_cycle()
    if state.tie_value != best:
        state.tie_value = best
        state.tie_index = 0
    for offset in range(len(cycle)):
        seat = cycle[(state.tie_index + offset) % len(cycle)]
        for frame, uid in tied:
            if frame.seat == seat:
                state.tie_index = (state.tie_index + offset + 1) % len(cycle)
                return frame, uid
    return tied[0]


def _begin_resolution(state: GameState, frame: FrameState, uid: str) -> None:
    inst = state.cards[uid]
    inst.face_down = False
    card = state.catalogue[inst.key]

    # Reload: the next attack from that weapon "has no effect or attack"
    # (rules.tex:963). The card still resolves and the frame still moves, but
    # the effect and attack steps are skipped entirely -- so no block is
    # consumed and no ability of any kind triggers, this card's own Reload
    # included (otherwise a Cannon, which prints Reload on every card, would
    # re-arm on its own dud and lock itself out for the rest of the game).
    spent_reloading = kw.is_reloading_attack(state, frame, card)
    if spent_reloading:
        kw.consume_reload(state, frame, card)

    steps = []
    if kw.movement_budget(state, frame, card) > 0:
        steps.append("movement")
    if not spent_reloading:
        if effects.has_effect_step(card, state, frame):
            steps.append("effect")
        if card.is_attack and not effects.delegates_attack(card):
            steps.append("attack")
    state.resolution = Resolution(
        frame_id=frame.id, uid=uid, steps=steps, spent_reloading=spent_reloading
    )
    state.note(
        f"{frame.id} resolves {card.key} "
        f"(initiative {kw.effective_initiative(state, frame, card, inst.init_index)})"
    )
    _beat(state, "card")
    if len(steps) > 1:
        state.pending = PendingDecision(
            kind="resolve_order",
            seat=frame.seat,
            prompt=f"Order of resolution for {card.key}",
            options=[
                {"order": list(perm)} for perm in itertools.permutations(steps)
            ],
            frame_id=frame.id,
        )


def _finish_card(state: GameState) -> None:
    """The card has finished this act: bookkeeping, then back to the queue."""
    res = state.resolution
    state.resolution = None
    if res is None:
        return
    inst = state.cards.get(res.uid)
    if inst is None or inst.location != "committed":
        return
    frame = state.frames[res.frame_id]
    card = state.catalogue[inst.key]
    inst.init_index += 1
    inst.resolved = True
    inst.persist_left = card.persistence
    if kw.is_committed(card):
        state.note(f"{card.key} is Committed and is discarded")
        discard_card(state, res.uid)
    elif kw.is_reload(card) and not res.spent_reloading:
        # A card spent as the reload dud triggers no abilities, its own
        # Reload included -- it must not re-arm the weapon it just cleared.
        kw.start_reload(state, frame, res.uid)
    effects.after_card_resolved(state, frame, res.uid)


# --------------------------------------------------------------------------
# Step execution
# --------------------------------------------------------------------------


def _run_steps(state: GameState) -> bool:
    """Run resolution steps until one needs a decision. True if parked."""
    res = state.resolution
    if res is None:
        return False
    frame = state.frames[res.frame_id]
    card = state.catalogue[state.cards[res.uid].key]
    while True:
        if not frame.alive or state.cards[res.uid].location != "committed":
            state.resolution = None
            return False
        if res.attack is not None:
            # Resume an attack that is part-way through its block decisions.
            if _block_loop(state):
                return True
            continue
        if not res.steps:
            break
        step = res.steps[0]
        if step == "movement":
            if _movement_decision(state, frame, card):
                return True
            res.steps.pop(0)
        elif step == "effect":
            res.steps.pop(0)
            decision = effects.resolve_effect(state, frame, res.uid)
            if decision is not None:
                res.effect_state["awaiting"] = True
                state.pending = decision
                return True
            _beat(state, "effect")
        else:
            res.steps.pop(0)
            if _attack_step(state, frame, res.uid):
                return True
    _finish_card(state)
    return False


def _movement_decision(state: GameState, frame: FrameState, card: Card) -> bool:
    budget = kw.movement_budget(state, frame, card)
    if budget <= 0 or frame.pos is None or state.board is None:
        return False
    reach = state.board.reachable(
        frame.pos,
        budget,
        occupied=state.occupied(exclude=frame.id),
        flying=kw.is_flying(frame),
    )
    options = [
        {"x": pos.x, "y": pos.y, "cost": cost}
        for pos, cost in sorted(reach.items(), key=lambda kv: (kv[0].y, kv[0].x))
    ]
    options = effects.adjust_move_options(state, frame, budget, options)
    if not options:
        return False
    state.pending = PendingDecision(
        kind="move",
        seat=frame.seat,
        prompt=f"Move {frame.id} (up to {budget})",
        options=options,
        frame_id=frame.id,
    )
    return True


def _attack_step(state: GameState, frame: FrameState, uid: str) -> bool:
    card = state.catalogue[state.cards[uid].key]
    options = combat.legal_targets(state, frame, card)
    if not options:
        state.note(f"{card.key} has no legal target")
        return False
    if len(options) == 1:
        _declare(state, frame, uid, options[0])
        return _block_loop(state)
    state.pending = PendingDecision(
        kind="attack_target",
        seat=frame.seat,
        prompt=f"Choose a target for {card.key}",
        options=options,
        frame_id=frame.id,
    )
    return True


def _declare(state: GameState, frame: FrameState, uid: str, choice: Mapping) -> None:
    res = state.resolution
    attack = combat.declare_attack(
        state, frame, uid,
        target_kind=str(choice["kind"]), target_id=str(choice["id"]),
    )
    if res is not None:
        res.attack = attack


def _block_loop(state: GameState) -> bool:
    """Work through blocks and damage. True if parked on a block decision."""
    res = state.resolution
    if res is None or res.attack is None:
        return False
    attack = res.attack
    while attack.current is not None:
        target = attack.current
        decision = combat.next_block_decision(state, attack)
        if decision is not None:
            zones, candidates = decision
            defender = state.frames[target.id]
            state.pending = PendingDecision(
                kind="choose_block",
                seat=effects.block_chooser(state, defender, attack),
                prompt=(
                    f"{defender.id} must block "
                    f"{'/'.join(zones)} (blocking is compulsory)"
                ),
                options=[
                    {"uid": uid, "key": state.cards[uid].key, "zones": list(zones)}
                    for uid in candidates
                ],
                frame_id=defender.id,
            )
            res.effect_state["block_zones"] = list(zones)
            return True
        combat.finish_target(state, attack)
        _beat(state, "attack")
        combat.advance_attack(state, attack)
    res.attack = None
    return False


# --------------------------------------------------------------------------
# Cleanup
# --------------------------------------------------------------------------


def cleanup_phase(state: GameState) -> None:
    """The cleanup phase itself (rules.tex:596), without advancing the turn.

    Split out from `_cleanup` so it can be exercised on its own -- calling the
    full step immediately starts the next planning phase and draws cards.
    """
    state.phase = "cleanup"
    state.note(f"--- turn {state.turn}: cleanup ---")
    for frame in state.frames.values():
        reload_markers = set(frame.reloading.values())
        for uid in list(frame.committed):
            inst = state.cards[uid]
            if inst.location != "committed":
                continue
            card = state.catalogue[inst.key]
            if inst.is_echo:
                inst.is_echo = False
                discard_card(state, uid)
                continue
            # A Reload marker stays out until the weapon fires again, whatever
            # the Persistence column says (`Railgun_Kinetic Barrage` prints 0).
            if uid in reload_markers:
                move_card(state, uid, "aside")
                inst.persist_left = None
                continue
            if not inst.resolved or card.persistence == 0:
                discard_card(state, uid)
                continue
            move_card(state, uid, "aside")
            state.note(f"{card.key} persists")
        for uid in list(frame.aside):
            inst = state.cards[uid]
            if inst.persist_left is None:
                continue                      # \infty -- permanent
            if inst.persist_left > 0:
                inst.persist_left -= 1
            else:
                discard_card(state, uid)
                state.note(f"{inst.key} expires")
        frame.reloading = {
            group: uid for group, uid in frame.reloading.items()
            if state.cards[uid].location == "aside"
        }
    kw.end_of_turn(state)
    for frame in state.frames.values():
        if frame.alive and frame.deathstrike_until is not None:
            if state.turn >= frame.deathstrike_until:
                state.note(f"{frame.id}'s Deathstrike runs out")
                frame.deathstrike_until = None
                destroy_frame(state, frame)
    objectivelib.end_of_turn(state)
    state.rotate_priority()


def _cleanup(state: GameState) -> None:
    """Cleanup, then roll the turn over into the next planning phase."""
    cleanup_phase(state)
    state.turn += 1
    if state.turn > TURNS_PER_GAME:
        state.phase = "finished"
        state.note("game over")
        objectivelib.latch_objectives(state)
    else:
        _begin_planning(state)


# --------------------------------------------------------------------------
# The driver
# --------------------------------------------------------------------------


def advance(state: GameState) -> GameState:
    """Push the game forward until it needs a human/AI decision or ends."""
    guard = 0
    while state.pending is None and state.phase != "finished":
        guard += 1
        if guard > 20000:                     # pragma: no cover - safety net
            raise RuntimeError("engine failed to make progress")
        # Ephemeral Images: the fakes follow the frame wherever it was moved
        # from, and the trick ends when there is nothing left to hide behind.
        effects.sync_images(state)
        if _team_wiped(state):
            state.phase = "finished"
            state.note("one side has no frames left")
            objectivelib.latch_objectives(state)
            break
        if state.phase == "setup":
            if _deploy_decision(state):
                break
            _finish_setup(state)
        elif state.phase == "planning":
            if _planning_decision(state):
                break
            _end_planning(state)
        elif state.phase == "action":
            if state.resolution is not None:
                if _run_steps(state):
                    break
                continue
            if effects.followup_decision(state):
                break
            actor = next_actor(state)
            if actor is None:
                _cleanup(state)
                continue
            _begin_resolution(state, *actor)
            if state.pending is not None:
                break
            if _run_steps(state):
                break
        else:
            state.phase = "finished"
    return state


def _team_wiped(state: GameState) -> bool:
    return any(not any(f.alive for f in state.frames_of(seat)) for seat in state.seats)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def legal_commands(state: GameState, seat: Team) -> list[Command]:
    pending = state.pending
    if pending is None or pending.seat != seat:
        return []
    if pending.kind == "commit_actions":
        uids = [str(o["uid"]) for o in pending.options]
        frame = state.frames.get(str(pending.frame_id)) if pending.frame_id else None
        low, high = _commit_range(state, frame, len(uids))
        return [
            Command("commit_actions", seat, {"uids": list(pair)})
            for size in range(low, high + 1)
            for pair in itertools.combinations(uids, size)
        ]
    return [Command(pending.kind, seat, dict(option)) for option in pending.options]


def _commit_range(
    state: GameState, frame: Optional[FrameState], available: int
) -> tuple[int, int]:
    """How many actions a frame may commit: `(minimum, maximum)`.

    Normally exactly `ACTIONS_PER_TURN`. Hyper ("next turn: play 1 extra
    action") raises the maximum only -- taking the extra action is the point of
    the card, but committing the usual two is never made illegal by it.
    """
    low = min(ACTIONS_PER_TURN, available)
    high = low if frame is None else min(effects.actions_to_commit(state, frame), available)
    return low, max(low, high)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise IllegalCommand(message)


def handle_command(state: GameState, cmd: Command) -> None:
    """Apply one decision to `state` (already a private copy)."""
    pending = state.pending
    _require(pending is not None, "no decision is pending")
    assert pending is not None
    _require(cmd.kind == pending.kind, f"expected {pending.kind}, got {cmd.kind}")
    _require(cmd.seat == pending.seat, f"decision belongs to seat {pending.seat}")
    state.pending = None
    handler = _HANDLERS.get(cmd.kind)
    _require(handler is not None, f"unsupported command {cmd.kind}")
    assert handler is not None
    handler(state, pending, cmd)


def _handle_commit(state: GameState, pending: PendingDecision, cmd: Command) -> None:
    frame = state.frames[str(pending.frame_id)]
    uids = [str(u) for u in cmd.payload.get("uids", [])]
    allowed = {str(o["uid"]) for o in pending.options}
    _require(len(set(uids)) == len(uids), "duplicate cards committed")
    _require(all(u in allowed for u in uids), "card is not in that frame's hand")
    low, high = _commit_range(state, frame, len(allowed))
    _require(
        low <= len(uids) <= high,
        f"commit {low} actions" if low == high
        else f"commit between {low} and {high} actions",
    )
    for uid in uids:
        move_card(state, uid, "committed")
        state.cards[uid].face_down = True
        state.cards[uid].resolved = False
        state.cards[uid].init_index = 0
    for uid in list(frame.hand):
        move_card(state, uid, "discard")
    state.note(f"{frame.id} commits {len(uids)} actions")
    if state.queue and state.queue[0] == frame.id:
        state.queue.pop(0)


def _handle_effect_choice(
    state: GameState, pending: PendingDecision, cmd: Command
) -> None:
    frame = state.frames[str(pending.frame_id)] if pending.frame_id else None
    _require(frame is not None, "effect choice without a frame")
    assert frame is not None
    payload = dict(cmd.payload)
    _require(_offered(pending, payload), "that option was not offered")
    if "mulligan" in payload:
        frame.mulligan_used = True
        if payload["mulligan"]:
            for uid in list(frame.hand):
                move_card(state, uid, "discard")
            draw(state, frame, frame.draw_count)
            state.note(f"{frame.id} mulligans its hand")
        return
    res = state.resolution
    effects.apply_effect_choice(
        state, frame, res.uid if res is not None else "", payload
    )


def _offered(pending: PendingDecision, payload: Mapping[str, object]) -> bool:
    """True if some offered option agrees with every key the caller sent."""
    return any(
        all(option.get(key) == value for key, value in payload.items())
        for option in pending.options
    )


def _handle_echo(state: GameState, pending: PendingDecision, cmd: Command) -> None:
    payload = dict(cmd.payload)
    _require(_offered(pending, payload), "that option was not offered")
    if payload.get("decline"):
        return
    dead = state.frames[str(payload["dead"])]
    host = state.frames[str(payload["host"])]
    if not dead.deck:
        from .state import reshuffle

        reshuffle(state, dead)
    if not dead.deck:
        return
    uid = dead.deck.pop(0)
    inst = state.cards[uid]
    inst.location = "committed"
    inst.is_echo = True
    inst.face_down = False
    inst.resolved = False
    host.committed.append(uid)
    state.note(f"Echo of {dead.id}: {inst.key} joins {host.id}")


def _handle_resolve_order(
    state: GameState, pending: PendingDecision, cmd: Command
) -> None:
    res = state.resolution
    _require(res is not None, "nothing is resolving")
    assert res is not None
    order = [str(s) for s in cmd.payload.get("order", [])]
    _require(
        sorted(order) == sorted(res.steps), "order must be a permutation of the steps"
    )
    res.steps = order


def _handle_move(state: GameState, pending: PendingDecision, cmd: Command) -> None:
    frame = state.frames[str(pending.frame_id)]
    dest = Pos(int(cmd.payload["x"]), int(cmd.payload["y"]))
    _require(
        any(o["x"] == dest.x and o["y"] == dest.y for o in pending.options),
        "that tile is not reachable",
    )
    old = frame.pos
    if dest != old:
        frame.pos = dest
        frame.moved_this_turn = True
        state.note(f"{frame.id} moves to ({dest.x},{dest.y})")
    objectivelib.on_move(state, frame, old)
    effects.after_move(state, frame, old, dest)
    if dest != old:
        _beat(state, "move")
    res = state.resolution
    if res is not None and res.steps and res.steps[0] == "movement":
        res.steps.pop(0)


def _handle_attack_target(
    state: GameState, pending: PendingDecision, cmd: Command
) -> None:
    res = state.resolution
    _require(res is not None, "nothing is resolving")
    assert res is not None
    kind = str(cmd.payload.get("kind"))
    target_id = str(cmd.payload.get("id"))
    _require(
        any(o["kind"] == kind and o["id"] == target_id for o in pending.options),
        "that target was not offered",
    )
    frame = state.frames[res.frame_id]
    _declare(state, frame, res.uid, {"kind": kind, "id": target_id})


def _handle_choose_block(
    state: GameState, pending: PendingDecision, cmd: Command
) -> None:
    res = state.resolution
    _require(res is not None and res.attack is not None, "no attack is pending")
    assert res is not None and res.attack is not None
    uid = str(cmd.payload.get("uid"))
    allowed = {str(o["uid"]) for o in pending.options}
    _require(uid in allowed, "blocking is compulsory, but not with that card")
    zones = [str(z) for z in res.effect_state.get("block_zones", [])]
    defender = state.frames[str(pending.frame_id)]
    block_card = state.card(uid)
    combat.apply_block(state, defender, res.attack, uid, zones)
    attacker = state.frames.get(res.attack.attacker_id)
    effects.on_block(state, defender, block_card, attacker)


def _handle_deploy(state: GameState, pending: PendingDecision, cmd: Command) -> None:
    payload = dict(cmd.payload)
    _require(_offered(pending, payload), "that deployment was not offered")
    frame = state.frames[str(payload["frame"])]
    _require(frame.seat == cmd.seat, "that frame belongs to the other seat")
    _require(frame.pos is None, "that frame is already deployed")
    frame.pos = Pos(int(payload["x"]), int(payload["y"]))
    state.note(
        f"{frame.id} deploys at ({frame.pos.x},{frame.pos.y})"
    )
    if state.queue and state.queue[0] == cmd.seat:
        state.queue.pop(0)


_HANDLERS: Mapping[str, Callable[[GameState, PendingDecision, Command], None]] = {
    "deploy": _handle_deploy,
    "commit_actions": _handle_commit,
    "effect_choice": _handle_effect_choice,
    "echo_card": _handle_echo,
    "resolve_order": _handle_resolve_order,
    "move": _handle_move,
    "attack_target": _handle_attack_target,
    "choose_block": _handle_choose_block,
}
