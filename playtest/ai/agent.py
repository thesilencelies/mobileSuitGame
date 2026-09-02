"""The AI seat.

`Agent.act(view)` takes **only** the dict `view_for(state, seat)` returns and
gives back one `Command`. It never sees a `GameState`, so it cannot read the
opponent's hand, deck order or face-down commitments even by accident: those
keys are not in its input. Planning stays simultaneous because the agent
commits its two cards from a view in which the other seat's `pending` is just
`{"waiting": true}`.

What it knows about the opponent, it knows the way a player does:

* the public card list (deck construction is faction-locked, so the frame's
  faction bounds what can be in its deck);
* every card of theirs that has been revealed -- resolved, face up while
  resolving, spent blocking, or set aside -- accumulated across the game from
  the views it has been handed and from the shared event log.

Decisions, in the order the engine asks for them: `commit_actions`,
`resolve_order`, `move`, `attack_target`, `choose_block`, `effect_choice`,
`echo_card`.
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, replace
from itertools import combinations
from typing import Any, Mapping, Optional, Sequence

from ..engine.types import ACTIONS_PER_TURN, Command, Pos, ZONES
from .params import AIParams, params_from_dict
from . import scoring as S
from .view import Catalogue, CardInfo, FrameView, Snapshot, build_board

_RESOLVES_RE = re.compile(r"^(?P<who>.+?) resolves (?P<key>.+?) \(initiative")
_BLOCKS_RE = re.compile(r"^(?P<who>.+?) blocks with (?P<key>.+?) \((?:kept|discarded)\)")

#: Quantile of the faction card pool that counts as the opponent's peak hit on
#: a zone, before anything of theirs has been seen. The plain maximum is the
#: single scariest card in the game and made the survival term fire on every
#: zone from turn one.
PRIOR_PEAK_QUANTILE = 0.9

#: Weights inside the focus-target score. All are multiplied by `focus_fire`
#: at the point of use, so a single parameter still controls the whole tactic.
FOCUS_CONVERGENCE = 1.6      # per extra frame that can threaten the target
FOCUS_CLOSING = 1.2          # per frame, scaled by how near it is to threatening
FOCUS_LETHAL = 5.0           # the squad can plausibly finish it this turn
FOCUS_LAST_HIT = 2.0         # a zone already one hit from destroying it
FOCUS_EXHAUSTION = 1.1       # per blocking card it has already spent
FOCUS_SEQUENCING = 1.4       # we hold both a fast stripper and a slow hammer

#: How strongly the shared target biases the three decisions that use it.
#: Deliberately a preference, not a rule -- a frame with a clearly better local
#: option (a free kill elsewhere, an objective) can still defect.
FOCUS_MOVE_WEIGHT = 0.9      # scale on the target's value when choosing a tile
FOCUS_TARGET_WEIGHT = 0.7    # scale when choosing what to attack
FOCUS_COMMIT_BONUS = 1.3     # board value for a card that can reach the target
FOCUS_ZONE_BONUS = 0.7       # extra for a card hitting the target's soft zone

#: How much cheaper each *extra* zone a card covers makes it when answering a
#: Guard Break. Every zone the chosen blocker covers is a zone the defender
#: does not have to spend a second card on, so this is worth roughly a card.
GUARD_BREAK_WIDTH = 1.2

#: Fewest candidates a decision will ever look at, however tight the budget.
#: Below this the AI stops being an AI, so the time ceiling never goes here --
#: it degrades to this floor and no further.
MIN_CANDIDATES = 6


@dataclass
class TeamPlan:
    """Who the squad is trying to kill this turn, and where it is soft.

    Formed once, on the seat's first `commit_actions` of a turn, and then read
    by every frame's card scoring, movement and target selection for the rest
    of that turn. That shared reference is the whole point: a defender holds
    two committed cards, blocking is compulsory and spends the blocker, so
    several attacks arriving on *one* frame in a turn are not merely additive
    -- the early ones are eaten by blocks and the later ones land unopposed.
    Spread across three enemies the same damage cripples all of them and kills
    none.
    """

    turn: int
    target_id: Optional[str]
    #: The target's softest zone -- where to concentrate once its guard is gone.
    zone: Optional[str] = None
    score: float = 0.0
    #: How many of our frames could bring something to bear on it.
    converging: int = 0


class Agent:
    """One AI seat. Construct once per game; it remembers what it has seen.

    **Compute budget.** This ships onto a phone running CPython under Termux,
    so every decision is bounded twice. `params.search_width` is a hard,
    deterministic cap on how many candidates a decision scores -- that is what
    actually sets the cost, and it is the knob to turn for a slower device.
    `params.think_ms` is a wall-clock safety net on top: if a decision is still
    going when the budget runs out, it stops widening and answers from the
    candidates it has already scored, which are the pre-rank's best guesses.
    It degrades to a shallower search, never to a random one, and never below
    `MIN_CANDIDATES`.

    **Determinism.** Given a seed and the same sequence of views, play is
    reproducible *as long as the time ceiling is not reached* -- a clock-driven
    cutoff is by nature machine-dependent. Set `think_ms=0` to remove the
    ceiling and get bit-identical replays on any hardware; the deterministic
    `search_width` cap still bounds the work.
    """

    def __init__(
        self,
        seat: int,
        catalogue: Mapping[str, Mapping[str, Any]],
        params: AIParams | Mapping[str, Any] | None = None,
        seed: int = 0,
        name: str = "ai",
    ) -> None:
        self.seat = int(seat)
        self.catalogue = (
            catalogue if isinstance(catalogue, Catalogue) else Catalogue(catalogue)
        )
        if isinstance(params, AIParams):
            self.params = params
        else:
            self.params = params_from_dict(params)
        self.rng = random.Random(seed)
        self.name = name

        self._board = None
        self._prior: dict[str, S.Profile] = {}
        self._seen: list[str] = []
        self._seen_keys: set[tuple[str, str]] = set()
        self._log_index = 0
        self._los: dict = {}
        self._deadline = float("inf")
        #: The squad's shared target for the current turn.
        self._plan: Optional[TeamPlan] = None
        #: Decision counters, for the arena's diagnostics.
        self.stats: dict[str, int] = {}

    # -- compute budget ---------------------------------------------------

    @property
    def move_candidates(self) -> int:
        """Destinations a `move` decision scores in full.

        A 15x16 board can offer well over a hundred reachable tiles and the
        expensive part is line of sight, so the rest are dropped by a cheap
        pre-rank. This is the deterministic half of the budget.
        """
        return max(MIN_CANDIDATES, int(self.params.search_width))

    @property
    def opportunity_candidates(self) -> int:
        """Tiles the commit-time "can this card do anything" scan looks at."""
        return max(MIN_CANDIDATES, int(self.params.search_width) * 3 // 4)

    def _start_clock(self) -> None:
        budget = int(self.params.think_ms)
        self._deadline = (
            time.monotonic() + budget / 1000.0 if budget > 0 else float("inf")
        )

    def _out_of_time(self) -> bool:
        return time.monotonic() > self._deadline

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def act(self, view: Mapping[str, Any]) -> Optional[Command]:
        """The command this seat plays, or None if it is not its decision."""
        pending = view.get("pending")
        if not pending or pending.get("waiting") or int(pending.get("seat", -1)) != self.seat:
            return None
        if int(view.get("seat", -1)) != self.seat:
            raise ValueError(
                f"agent for seat {self.seat} was handed seat {view.get('seat')}'s view"
            )
        self._start_clock()
        snap = self._snapshot(view)
        self._los = {}
        self._observe(snap)
        kind = str(pending.get("kind"))
        options = list(pending.get("options") or ())
        self.stats[kind] = self.stats.get(kind, 0) + 1
        if not options:
            return None

        if self.params.blunder_rate > 0 and self.rng.random() < self.params.blunder_rate:
            self.stats["blunder"] = self.stats.get("blunder", 0) + 1
            return self._random_choice(kind, options)

        handler = {
            "deploy": self._deploy,
            "place_objective": self._place_objective,
            "choose_frame": self._choose_frame,
            "move_token": self._move_token,
            "choose_actor": self._choose_actor,
            "commit_actions": self._commit_actions,
            "resolve_order": self._resolve_order,
            "move": self._move,
            "attack_target": self._attack_target,
            "choose_block": self._choose_block,
            "effect_choice": self._effect_choice,
            "echo_card": self._echo_card,
        }.get(kind)
        if handler is None:
            return self._random_choice(kind, options)
        try:
            return handler(snap, pending, options)
        except Exception:                       # pragma: no cover - never stall
            self.stats["fallback"] = self.stats.get("fallback", 0) + 1
            return self._random_choice(kind, options)

    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------

    def _snapshot(self, view: Mapping[str, Any]) -> Snapshot:
        board_json = view.get("board") or {}
        if self._board is None or (
            self._board.width != board_json.get("width")
            or self._board.height != board_json.get("height")
        ):
            self._board = build_board(board_json)
        return Snapshot(view, self._board)

    def _random_choice(self, kind: str, options: Sequence[Mapping[str, Any]]) -> Command:
        if kind == "commit_actions":
            uids = [str(o["uid"]) for o in options]
            take = min(2, len(uids))
            return Command(kind, self.seat, {"uids": self.rng.sample(uids, take)})
        return Command(kind, self.seat, dict(self.rng.choice(list(options))))

    def card(self, key: Optional[str]) -> Optional[CardInfo]:
        return self.catalogue.get(key) if key else None

    # ------------------------------------------------------------------
    # Opponent model
    # ------------------------------------------------------------------

    def _observe(self, snap: Snapshot) -> None:
        """Record every enemy card this seat is entitled to have seen."""
        for frame in snap.enemies(alive_only=False):
            refs = [(c.uid, c.key) for c in frame.committed + frame.on_field if c.key]
            refs += [(uid, key) for uid, key in frame.aside]
            for uid, key in refs:
                if (uid, key) not in self._seen_keys and key in self.catalogue:
                    self._seen_keys.add((uid, key))
                    self._seen.append(key)
        # The event log is shared, so anything named there was revealed in play.
        names = {}
        for frame in snap.frames.values():
            names.setdefault(frame.name, set()).add(frame.seat)
        entries = list(snap.log)
        for index in range(self._log_index, len(entries)):
            text = str(entries[index].get("text", ""))
            for pattern in (_RESOLVES_RE, _BLOCKS_RE):
                match = pattern.match(text)
                if not match:
                    continue
                seats = names.get(match.group("who"))
                key = match.group("key")
                if seats == {1 - self.seat} and key in self.catalogue:
                    marker = (f"log{index}", key)
                    if marker not in self._seen_keys:
                        self._seen_keys.add(marker)
                        self._seen.append(key)
        self._log_index = len(entries)

    def opponent_profile(self, snap: Snapshot) -> S.Profile:
        """Prior from the enemy's faction card pool, updated with what we saw."""
        factions = tuple(sorted({f.faction for f in snap.enemies(alive_only=False)}))
        key = "|".join(factions)
        prior = self._prior.get(key)
        if prior is None:
            pool: list[CardInfo] = []
            for faction in factions:
                pool.extend(self.catalogue.playable_for(faction))
            # The prior spans every card the enemy's faction could legally
            # bring, so its "peak" hit is damped to a high quantile rather
            # than the single scariest card in the pool -- see `S.profile`.
            prior = S.profile(pool, peak_q=PRIOR_PEAK_QUANTILE) if pool else S.Profile(n=1)
            self._prior[key] = prior
        if not self._seen:
            return prior
        observed = S.profile([self.catalogue[k] for k in self._seen])
        return S.blend(prior, observed, min(0.85, len(self._seen) / 14.0))

    def _pressure(self, snap: Snapshot, frame: FrameView, prof: S.Profile) -> float:
        """How much the enemy can plausibly hurt this frame this turn, 0..1."""
        if frame.pos is None:
            return 0.0
        best = 0.0
        for enemy in snap.enemies():
            if enemy.pos is None:
                continue
            distance = snap.distance(frame.pos, enemy.pos)
            reach = enemy.movement + 3          # +1 adjacency, +2 for a booster
            if distance <= reach:
                best = max(best, 1.0)
            elif distance <= reach + 3:
                best = max(best, 0.55)
            else:
                best = max(best, 0.15)
            if prof.ranged_share > 0.05 and 1 < distance <= 9:
                if S.has_los(snap, frame.pos, enemy, frame, self._los):
                    best = max(best, 0.5 + 0.5 * prof.ranged_share)
        return best

    # ------------------------------------------------------------------
    # The squad's shared target for the turn
    # ------------------------------------------------------------------

    def team_plan(self, snap: Snapshot) -> Optional[TeamPlan]:
        """The focus target for this turn, forming it on first use.

        Cached per turn so all three frames plan against the same enemy.
        Abandoned and re-formed when the target dies or leaves the board --
        piling onto a corpse is worse than not focusing at all.
        """
        plan = self._plan
        if plan is not None and plan.turn == snap.turn:
            target = snap.frame(plan.target_id)
            if target is not None and target.alive and target.pos is not None:
                return plan
            self.stats["focus_abandoned"] = self.stats.get("focus_abandoned", 0) + 1
            plan = None
        plan = self._choose_focus(snap)
        # A target-less plan is not cached. During the setup phase no frame has
        # a position yet, and caching the empty result would leave the whole of
        # turn 1 believing there was nobody to converge on.
        self._plan = plan if plan.target_id is not None else None
        return plan

    def _squad_cards(self, frame: FrameView) -> list[CardInfo]:
        """What this frame could bring to bear: its hand, or what it committed.

        During planning we can see every one of our own frames' hands, which is
        what makes a squad-wide plan possible at all. Once a frame has
        committed, its hand is gone and the committed pair is the answer.
        """
        keys = [key for _uid, key in frame.hand]
        if not keys:
            keys = [ref.key for ref in frame.committed if ref.key]
        return [c for c in (self.card(k) for k in keys) if c is not None]

    def _can_threaten(
        self, snap: Snapshot, mine: FrameView, card: CardInfo, enemy: FrameView
    ) -> bool:
        """Could `mine` bring `card` to bear on `enemy` this turn?

        Straight-line reckoning rather than a reachability search: this runs
        over every frame/enemy/card triple and only needs to be roughly right.
        """
        if mine.pos is None or enemy.pos is None or not card.is_attack:
            return False
        if not S.can_use(mine, card):
            return False
        gap = S.reach_gap(snap, mine, card, mine.pos, enemy.pos)
        if gap > S.movement_budget(mine, card):
            return False
        if card.is_ranged and gap == 0:
            # Already at range: it only counts if it can actually see them.
            return S.has_los(snap, mine.pos, enemy, mine, self._los)
        return True

    def _choose_focus(self, snap: Snapshot) -> Optional[TeamPlan]:
        """Score every living enemy as a convergence target and take the best."""
        mine = [f for f in snap.mine() if f.pos is not None]
        enemies = [e for e in snap.enemies() if e.pos is not None]
        if not mine or not enemies:
            return TeamPlan(turn=snap.turn, target_id=None)

        best: Optional[TeamPlan] = None
        for enemy in enemies:
            converging = 0
            inits: list[int] = []
            damage = 0.0
            closing = 0.0
            for frame in mine:
                cards = [
                    c for c in self._squad_cards(frame)
                    if c.is_attack and S.can_use(frame, c)
                ]
                usable = [c for c in cards if self._can_threaten(snap, frame, c, enemy)]
                if usable:
                    converging += 1
                    damage += max(sum(c.attacks.values()) for c in usable)
                    inits.extend(S.effective_init(frame, c) for c in usable)
                if cards and enemy.pos is not None and frame.pos is not None:
                    # How near this frame is to being able to threaten it at
                    # all. On turn 1 the squads are the length of the board
                    # apart and nothing reaches anything, but that is exactly
                    # when agreeing where to go matters most -- so a target is
                    # still chosen, on how close the squad collectively is.
                    shortfall = min(
                        max(0, S.reach_gap(snap, frame, c, frame.pos, enemy.pos)
                            - S.movement_budget(frame, c))
                        for c in cards
                    )
                    closing += 1.0 / (1.0 + shortfall)
            score_closing = FOCUS_CLOSING * closing

            # How soft a target is only counts to the extent we can act on it.
            # A frame one hit from death on the far side of the board is not a
            # focus target, it is a wish -- so everything below is scaled by
            # whether the squad can actually touch it this turn.
            reach = 1.0 if converging else 0.3 * (closing / max(1, len(mine)))

            # Lethality: can we plausibly finish it, and is a zone already at
            # its last hit? Armour is per zone, so the softest zone is the one
            # the kill actually comes through.
            soft = min(ZONES, key=lambda z: enemy.remaining(z))
            weakest = enemy.remaining(soft)
            score = FOCUS_CONVERGENCE * max(0, converging - 1) + score_closing
            softness = 0.0
            if weakest > 0 and damage >= weakest:
                softness += FOCUS_LETHAL
            if enemy.last_hit.get(soft) or weakest <= 1:
                softness += FOCUS_LAST_HIT

            # Card exhaustion: a frame that has already spent its cards
            # blocking is close to defenceless, which is exactly when to pile on.
            spent = max(0, ACTIONS_PER_TURN - len(enemy.committed + enemy.on_field))
            softness += FOCUS_EXHAUSTION * spent

            # Initiative sequencing: a cheap fast attack strips the guard so the
            # slow heavy one lands. Only worth anything if we hold both.
            if len(inits) >= 2 and max(inits) - min(inits) >= 3:
                softness += FOCUS_SEQUENCING

            # A target already hurt is worth more than raw damage suggests.
            softness += 2.0 * (
                1.0 - enemy.total_remaining / max(1, sum(enemy.armour.values()))
            )
            score += reach * softness
            if best is None or score > best.score:
                best = TeamPlan(
                    turn=snap.turn, target_id=enemy.id, zone=soft,
                    score=score, converging=converging,
                )
        if best is None:
            return TeamPlan(turn=snap.turn, target_id=None)
        self.stats["focus_formed"] = self.stats.get("focus_formed", 0) + 1
        return best

    def _focus_weight(self, plan: Optional[TeamPlan], scale: float) -> tuple[Optional[str], float]:
        """`(target id, weight)` for a decision, or `(None, 0)` when not focusing."""
        if plan is None or plan.target_id is None or self.params.focus_fire <= 0:
            return None, 0.0
        return plan.target_id, self.params.focus_fire * scale

    def _reloading(self, frame: FrameView) -> set[str]:
        """Weapon groups whose reload marker is still set aside."""
        groups = set()
        for _uid, key in frame.aside:
            card = self.card(key)
            if card is not None and card.reload:
                groups.add(card.group)
        return groups

    # ------------------------------------------------------------------
    # commit_actions
    # ------------------------------------------------------------------

    def _deploy(
        self,
        snap: Snapshot,
        pending: Mapping[str, Any],
        options: Sequence[Mapping[str, Any]],
    ) -> Command:
        """Setup: place one frame on its own edge.

        The row is fixed, so this is a choice of column, and it sets up the
        whole game: which objectives the frame can contest, what ground it
        starts on, and whether the squad arrives together or piecemeal. Scored
        with the same objective and terrain terms the movement evaluator uses,
        plus a spacing term -- frames deployed on top of each other funnel into
        the same approach lane and get blocked by their own squadmates, while
        frames scattered to opposite corners never converge on anything.
        """
        params = self.params
        frame = snap.frame(str(options[0].get("frame")))
        placed = [
            f.pos for f in snap.mine()
            if f.pos is not None and (frame is None or f.id != frame.id)
        ]
        values: list[float] = []
        tiles = [Pos(int(o["x"]), int(o["y"])) for o in options]
        for tile in tiles:
            value = S.terrain_value(snap, tile, params)
            if frame is not None:
                value += S.objective_value(snap, frame, tile, params)
            if placed:
                gap = min(snap.distance(tile, p) for p in placed)
                # Two or three tiles apart: close enough to support, far enough
                # not to queue up behind each other.
                value -= 0.6 * abs(gap - 3)
            values.append(value)
        index = S.softmax_pick(values, params.temperature * 0.3, self.rng)
        chosen = options[index]
        return Command("deploy", self.seat, {
            "frame": str(chosen["frame"]),
            "x": int(chosen["x"]), "y": int(chosen["y"]),
        })

    def _place_objective(
        self,
        snap: Snapshot,
        pending: Mapping[str, Any],
        options: Sequence[Mapping[str, Any]],
    ) -> Command:
        """Where to put down a token the objectives hand you at setup.

        Fugitive, gangs, refugees: every one of them is a token the side
        placing it wants to *survive*, and every one has to live through five
        turns of somebody hunting it. So the only two things worth optimising
        are the head start and not stacking the whole set on one tile, where
        one splash attack would take the lot.
        """
        marks = [f.pos for f in snap.enemies() if f.pos is not None]
        mine = [
            t.pos for t in snap.tokens
            if t.alive and t.pos is not None and t.owner == self.seat
        ]
        if not marks and not mine:
            return Command("place_objective", self.seat, dict(options[0]))

        def value(option: Mapping[str, Any]) -> float:
            pos = Pos(int(option["x"]), int(option["y"]))
            away = min((snap.distance(pos, p) for p in marks), default=0)
            spread = min((snap.distance(pos, p) for p in mine), default=0)
            return away + 0.75 * min(spread, 4)

        return Command("place_objective", self.seat,
                       dict(max(options, key=value)))

    def _choose_frame(
        self,
        snap: Snapshot,
        pending: Mapping[str, Any],
        options: Sequence[Mapping[str, Any]],
    ) -> Command:
        """One of your own frames, for what an objective needs one for.

        Two ask. **Dome Campus**, at setup: who runs the bomb in, which wants
        the frame that can actually get there -- fewest turns of walking, tie
        broken toward the one that can take a hit on the way. **The Lake
        Ritual**, at the end of a turn: who carries the relic, which wants the
        opposite kind of frame -- it is dropped the moment its carrier is
        damaged, so it goes to whichever of them is hardest to hurt.
        """
        if str(snap.phase) == "cleanup":
            return Command("choose_frame", self.seat, dict(max(
                options,
                key=lambda o: (
                    sum(f.health.values()) if (f := snap.frame(
                        str(o.get("frame")))) is not None else -1.0
                ),
            )))
        obj = next((o for o in snap.objectives if o.name == "Dome Campus"), None)
        tiles = tuple(obj.tiles) if obj is not None else ()

        def turns(option: Mapping[str, Any]) -> tuple[float, float]:
            frame = snap.frame(str(option.get("frame")))
            if frame is None or frame.pos is None or not tiles:
                return (99.0, 0.0)
            gap = min(snap.distance(frame.pos, t) for t in tiles)
            speed = max(1, frame.movement)
            return (gap / float(speed), -float(sum(frame.health.values())))

        return Command("choose_frame", self.seat,
                       dict(min(options, key=turns)))

    def _move_token(
        self,
        snap: Snapshot,
        pending: Mapping[str, Any],
        options: Sequence[Mapping[str, Any]],
    ) -> Command:
        """A gang or a refugee, moved by the side that created it.

        Both cards score for the creator only while the token is *alive*, so
        this is a flight: away from anything that could shoot it, and out of
        the corner it would otherwise be pinned in.
        """
        marks = [f.pos for f in snap.enemies() if f.pos is not None]
        if not marks:
            return Command("move_token", self.seat, dict(options[0]))

        def value(option: Mapping[str, Any]) -> float:
            pos = Pos(int(option["x"]), int(option["y"]))
            return min(snap.distance(pos, p) for p in marks)

        return Command("move_token", self.seat, dict(max(options, key=value)))

    def _choose_actor(
        self,
        snap: Snapshot,
        pending: Mapping[str, Any],
        options: Sequence[Mapping[str, Any]],
    ) -> Command:
        """Which of its own tied cards to resolve first.

        Move before swing: closing the distance can only make an attack better,
        and a card that repositions first may find a target the other one could
        not reach. Ties inside that break on the higher-rated card.
        """
        def rank(option: Mapping[str, Any]) -> tuple[float, float]:
            card = self.card(str(option.get("key")))
            if card is None:
                return (0.0, 0.0)
            return (1.0 if not card.is_attack else 0.0, float(card.movement))

        return Command("choose_actor", self.seat,
                       dict(max(options, key=rank)))

    def _commit_actions(
        self,
        snap: Snapshot,
        pending: Mapping[str, Any],
        options: Sequence[Mapping[str, Any]],
    ) -> Command:
        frame = snap.frame(pending.get("frameId"))
        uids = [str(o["uid"]) for o in options]
        keys = [str(o.get("key", "")) for o in options]
        # Usually two, but Hyper buys a third and the engine says so on the
        # decision. Taking the extra action is the entire point of the card,
        # so the AI commits the ceiling rather than the constant.
        take = min(int(pending.get("pickMax") or ACTIONS_PER_TURN), len(uids))
        if frame is None or take < 2:
            return Command("commit_actions", self.seat, {"uids": uids[:take]})

        cards = [self.catalogue.get(k) for k in keys]
        live = [i for i, c in enumerate(cards) if c is not None]
        if len(live) < take:
            return Command("commit_actions", self.seat, {"uids": uids[:take]})

        prof = self.opponent_profile(snap)
        params = self.params
        health = frame.health
        reloading = self._reloading(frame)
        pressure = self._pressure(snap, frame, prof)

        budgets = {i: S.movement_budget(frame, cards[i]) for i in live}
        reach_cache: dict[int, Mapping[Pos, int]] = {}

        def reach(budget: int) -> Mapping[Pos, int]:
            if budget not in reach_cache:
                if frame.pos is None or snap.board is None:
                    reach_cache[budget] = {}
                else:
                    reach_cache[budget] = snap.board.reachable(
                        frame.pos,
                        budget,
                        occupied=snap.occupied(exclude=frame.id),
                        flying=frame.flying,
                    )
            return reach_cache[budget]

        # -- what each card can actually do this turn ----------------------
        best_by_budget: dict[tuple[int, int], float] = {}

        def opportunity_of(index: int, budget: int) -> float:
            card = cards[index]
            memo = (index, budget)
            if memo in best_by_budget:
                return best_by_budget[memo]
            value = 0.0
            if card is not None and card.is_attack and S.can_use(frame, card):
                for index, tile in enumerate(
                    self._candidate_tiles(snap, reach(budget), card, frame)
                ):
                    # Strictly "can this card hit something *this turn*" -- the
                    # approach gradient belongs to movement, not to whether an
                    # action has a target.
                    value = max(
                        value,
                        S._best_attack_from(
                            snap, frame, card, tile, prof, params, self._los,
                            include_approach=False,
                        ),
                    )
                    if value > 0:
                        break              # the question is yes/no, so stop
                    if index >= MIN_CANDIDATES and self._out_of_time():
                        self.stats["timeout"] = self.stats.get("timeout", 0) + 1
                        break
            best_by_budget[memo] = value
            return value

        max_budget = max((budgets[i] for i in live), default=0)
        opp_scale: dict[int, float] = {}
        for i in live:
            card = cards[i]
            if card is None or not card.is_attack:
                opp_scale[i] = 1.0
                continue
            own = opportunity_of(i, budgets[i])
            if own > 0:
                opp_scale[i] = 1.0
            elif opportunity_of(i, max_budget) > 0:
                opp_scale[i] = S.PAIR_ASSIST
            else:
                opp_scale[i] = S.NO_TARGET

        # -- positional worth of the movement each card buys ---------------
        # Deliberately cheap: objectives, high ground and closing distance, all
        # of which are pure arithmetic on tile coordinates. The expensive
        # line-of-sight scoring belongs to the `move` decision, where the frame
        # is actually choosing a tile rather than choosing how far it could go.
        enemy_positions = [e.pos for e in snap.enemies() if e.pos is not None]

        def ground_value(tile: Pos) -> float:
            value = S.objective_value(snap, frame, tile, params) + S.terrain_value(
                snap, tile, params
            )
            if enemy_positions and params.approach > 0:
                nearest = min(snap.distance(tile, e) for e in enemy_positions)
                value -= params.approach * 0.35 * nearest
            return value

        base_pos = ground_value(frame.pos) if frame.pos is not None else 0.0
        gain_cache: dict[int, float] = {}

        def positional_gain(budget: int) -> float:
            if budget not in gain_cache:
                best = base_pos
                for tile in reach(budget):
                    best = max(best, ground_value(tile))
                gain_cache[budget] = max(0.0, best - base_pos)
            return gain_cache[budget]

        board_value = {
            i: params.positioning * 0.5 * positional_gain(budgets[i]) for i in live
        }

        # -- converge on the squad's shared target -------------------------
        # A preference, not a rule: a card that cannot reach the focus target
        # simply misses this bonus rather than being ruled out, so a frame with
        # a clearly better local option still takes it.
        plan = self.team_plan(snap)
        if plan is not None and plan.target_id and params.focus_fire > 0:
            target = snap.frame(plan.target_id)
            if target is not None and target.alive:
                for i in live:
                    card = cards[i]
                    if card is None or not card.is_attack:
                        continue
                    if not self._can_threaten(snap, frame, card, target):
                        continue
                    board_value[i] += params.focus_fire * FOCUS_COMMIT_BONUS
                    if plan.zone and card.attacks.get(plan.zone, 0) > 0:
                        # Armour is per zone, so the kill comes through the
                        # soft one: concentrate there once the guard is gone.
                        board_value[i] += params.focus_fire * FOCUS_ZONE_BONUS

        # -- trim to the pool, then score every pair -----------------------
        singles = {
            i: S.score_hand(
                [cards[i]], prof, params, health,
                reloading=reloading,
                opportunity={0: opp_scale[i]},
                board_value={0: board_value[i]},
                pressure=pressure,
            )
            for i in live
        }
        pool = max(take, min(int(params.pool), len(live)))
        considered = sorted(live, key=lambda i: (-singles[i], i))[:pool]

        hands = list(combinations(considered, take))
        if not hands:
            return Command("commit_actions", self.seat, {"uids": uids[:take]})

        scores: list[float] = []
        deficits: list[float] = []
        for combo in hands:
            hand = [cards[i] for i in combo]
            scores.append(
                S.score_hand(
                    hand, prof, params, health,
                    reloading=reloading,
                    opportunity={k: opp_scale[i] for k, i in enumerate(combo)},
                    board_value={k: board_value[i] for k, i in enumerate(combo)},
                    pressure=pressure,
                )
            )
            deficits.append(S.survival_deficit(hand, prof, health))

        # Never gamble with survival: mix only among the survival-safest hands.
        floor = min(deficits)
        safe = [i for i, d in enumerate(deficits) if d <= floor + 1e-9]

        # Do not spend an action on a card another *available* card dominates.
        dominators = {
            i: {j for j in considered if j != i and S.dominates(cards[j], cards[i])}
            for i in considered
        }
        def has_dead(index: int) -> bool:
            chosen = set(hands[index])
            return any(dominators[i] - chosen for i in hands[index])

        keep = [i for i in safe if not has_dead(i)] or safe
        pick = keep[S.softmax_pick([scores[i] for i in keep], params.temperature, self.rng)]
        return Command("commit_actions", self.seat,
                       {"uids": [uids[i] for i in hands[pick]]})

    def _candidate_tiles(
        self,
        snap: Snapshot,
        reach: Mapping[Pos, int],
        card: CardInfo,
        frame: FrameView,
    ) -> list[Pos]:
        """The reachable tiles worth testing this card's attack from."""
        enemies = [e for e in snap.enemies() if e.pos is not None]
        targets = [e.pos for e in enemies] + [
            t.pos for t in snap.tokens if t.alive and t.max_hp > 0 and t.pos is not None
        ]
        if not targets or not reach:
            return []
        if card.is_ranged:
            ideal = (2 + card.max_range + S.range_bonus(frame, card)) / 2.0
        else:
            ideal = 1.0

        def key(tile: Pos) -> float:
            return min(abs(snap.distance(tile, t) - ideal) for t in targets)

        return sorted(reach, key=lambda p: (key(p), p.y, p.x))[: self.opportunity_candidates]

    # ------------------------------------------------------------------
    # resolve_order
    # ------------------------------------------------------------------

    def _resolve_order(
        self,
        snap: Snapshot,
        pending: Mapping[str, Any],
        options: Sequence[Mapping[str, Any]],
    ) -> Command:
        frame = snap.frame(pending.get("frameId"))
        card = self._resolving_card(snap, frame)
        move_first = True
        if frame is not None and card is not None and card.is_attack and frame.pos is not None:
            prof = self.opponent_profile(snap)
            here = S._best_attack_from(
                snap, frame, card, frame.pos, prof, self.params, self._los
            )
            budget = S.movement_budget(frame, card)
            elsewhere = here
            if budget > 0 and snap.board is not None:
                reach = snap.board.reachable(
                    frame.pos, budget,
                    occupied=snap.occupied(exclude=frame.id),
                    flying=frame.flying,
                )
                for tile in self._candidate_tiles(snap, reach, card, frame):
                    elsewhere = max(
                        elsewhere,
                        S._best_attack_from(
                            snap, frame, card, tile, prof, self.params, self._los
                        ),
                    )
            # Already in the best spot? Then shoot first and reposition after --
            # the move can break line of sight or grab an objective for free.
            move_first = elsewhere > here + 1e-9

        def rank(order: Sequence[str]) -> tuple:
            order = list(order)
            effect_first = order.index("effect") if "effect" in order else 99
            if "movement" in order and "attack" in order:
                moves_first = order.index("movement") < order.index("attack")
            else:
                moves_first = move_first
            return (0 if moves_first == move_first else 1, effect_first)

        best = min(options, key=lambda o: rank(list(o.get("order") or ())))
        return Command("resolve_order", self.seat, {"order": list(best["order"])})

    def _resolving_card(
        self, snap: Snapshot, frame: Optional[FrameView]
    ) -> Optional[CardInfo]:
        """Which of `frame`'s cards the engine is part-way through resolving.

        `snap.resolving` answers this outright when the caller supplies it --
        it comes from `GameState.resolution`, which carries the frame id and
        the card uid, and the card is face up by the time it exists.

        Without it (a bare `view_for`, as the arena uses) fall back to the
        structural signal: the engine turns a card face up when it starts
        resolving, so a face-up unresolved card in front of the frame is the
        one. A card kept after a super block is face up too, so the log breaks
        the tie -- but only when the frame's *name* is unambiguous, because the
        log identifies frames by name and several decks share a frame.
        """
        if frame is None:
            return None
        told = snap.resolving
        if told is not None:
            if str(told.get("frameId")) != frame.id:
                return None          # the engine is resolving somebody else
            key = told.get("key")
            if key:
                return self.card(str(key))
        face_up = [c.key for c in frame.committed if c.key and not c.face_down]
        if len(face_up) == 1:
            return self.card(face_up[0])
        if self._name_is_unique(snap, frame.name):
            for entry in reversed(list(snap.log)):
                match = _RESOLVES_RE.match(str(entry.get("text", "")))
                if match and match.group("who") == frame.name:
                    key = match.group("key")
                    if not face_up or key in face_up:
                        return self.card(key)
                    break
        return self.card(face_up[0]) if face_up else None

    def _name_is_unique(self, snap: Snapshot, name: str) -> bool:
        """True when exactly one frame on the board carries this name.

        The event log says "Percival MkIV resolves ...", so with the same frame
        on both sides the log cannot say whose it was -- and guessing produces
        a confidently wrong answer rather than an error.
        """
        return sum(1 for f in snap.frames.values() if f.name == name) == 1

    def _resolving_uid(self, frame: Optional[FrameView], card: Optional[CardInfo]) -> Optional[str]:
        if frame is None:
            return None
        for ref in frame.committed:
            if not ref.face_down and ref.key and (card is None or ref.key == card.key):
                return ref.uid
        return None

    # ------------------------------------------------------------------
    # move
    # ------------------------------------------------------------------

    def _move(
        self,
        snap: Snapshot,
        pending: Mapping[str, Any],
        options: Sequence[Mapping[str, Any]],
    ) -> Command:
        frame = snap.frame(pending.get("frameId"))
        if frame is None:
            return self._random_choice("move", options)
        prof = self.opponent_profile(snap)
        params = self.params
        primary = self._resolving_card(snap, frame)
        primary_uid = self._resolving_uid(frame, primary)
        others = [
            c for c in (
                self.card(ref.key)
                for ref in frame.committed
                if ref.key and ref.uid != primary_uid
            ) if c is not None
        ]

        tiles = [Pos(int(o["x"]), int(o["y"])) for o in options]
        if len(tiles) > self.move_candidates:
            tiles = self._prerank(snap, frame, tiles, primary, others, params)

        focus_id, focus_weight = self._focus_weight(
            self.team_plan(snap), FOCUS_MOVE_WEIGHT
        )
        values: list[float] = []
        for tile in tiles:
            values.append(
                S.position_value(
                    snap, frame, tile, prof, params,
                    cards=others, primary=primary, los_cache=self._los,
                    focus_id=focus_id, focus_weight=focus_weight,
                )
            )
            if len(values) >= MIN_CANDIDATES and self._out_of_time():
                # Out of budget: choose among the tiles already scored. They
                # are the pre-rank's best guesses, so this degrades to a
                # shallower search rather than to a random one.
                self.stats["timeout"] = self.stats.get("timeout", 0) + 1
                tiles = tiles[: len(values)]
                break
        if frame.seat != self.seat:
            # System Override: "you choose which tile target frame moves to".
            # The scorer just worked out where that frame would *like* to
            # stand, and the whole point of the card is to put it somewhere
            # else -- so the same numbers are read upside down.
            values = [-v for v in values]
        # Movement is far less forgiving than card choice, so the policy is much
        # sharper here than the headline temperature suggests.
        index = S.softmax_pick(values, params.temperature * 0.35, self.rng)
        best = tiles[index]
        return Command("move", self.seat, {"x": best.x, "y": best.y})

    def _prerank(
        self,
        snap: Snapshot,
        frame: FrameView,
        tiles: Sequence[Pos],
        primary: Optional[CardInfo],
        others: Sequence[CardInfo],
        params: AIParams,
    ) -> list[Pos]:
        """Cheaply shortlist destinations before the expensive LoS scoring."""
        attackers = [c for c in list(others) + ([primary] if primary else []) if c and c.is_attack]
        ideal = 1.0
        if attackers and all(c.is_ranged for c in attackers):
            ideal = (2 + max(c.max_range for c in attackers) + S.range_bonus(frame, attackers[0])) / 2.0
        enemies = [e.pos for e in snap.enemies() if e.pos is not None]

        def key(tile: Pos) -> float:
            score = S.objective_value(snap, frame, tile, params) + S.terrain_value(
                snap, tile, params
            )
            if enemies:
                score -= 0.5 * min(abs(snap.distance(tile, e) - ideal) for e in enemies)
            return -score

        # A frame that is not ours is being moved *against* its own interest
        # (System Override), so the shortlist is taken from the other end.
        sign = 1 if frame.seat == self.seat else -1
        shortlist = sorted(
            tiles, key=lambda p: (sign * key(p), p.y, p.x)
        )[: self.move_candidates]
        if frame.pos is not None and frame.pos in tiles and frame.pos not in shortlist:
            shortlist.append(frame.pos)
        return shortlist

    # ------------------------------------------------------------------
    # attack_target
    # ------------------------------------------------------------------

    def _attack_target(
        self,
        snap: Snapshot,
        pending: Mapping[str, Any],
        options: Sequence[Mapping[str, Any]],
    ) -> Command:
        frame = snap.frame(pending.get("frameId"))
        card = self._resolving_card(snap, frame)
        prof = self.opponent_profile(snap)
        if frame is None or card is None:
            return self._random_choice("attack_target", options)
        focus_id, focus_weight = self._focus_weight(
            self.team_plan(snap), FOCUS_TARGET_WEIGHT
        )
        values = []
        for option in options:
            zones = {str(z): int(d) for z, d in (option.get("zones") or {}).items()}
            if str(option.get("kind")) == "frame":
                target = snap.frame(str(option.get("id")))
                if target is None:
                    values.append(0.0)
                    continue
                value = S.zone_attack_value(card, zones, target, prof, self.params)
                if target.id == focus_id:
                    value *= 1.0 + focus_weight
                values.append(value)
            else:
                token = next(
                    (t for t in snap.tokens if t.id == str(option.get("id"))), None
                )
                if token is None or frame.pos is None:
                    values.append(0.0)
                    continue
                if token.kind == "image":
                    values.append(
                        S.image_value(snap, frame, card, token, prof, self.params)
                    )
                else:
                    values.append(
                        S.token_value(
                            snap, frame, card, frame.pos, token, self.params
                        )
                    )
        index = S.softmax_pick(values, self.params.temperature * 0.3, self.rng)
        chosen = options[index]
        return Command(
            "attack_target",
            self.seat,
            {"kind": str(chosen["kind"]), "id": str(chosen["id"])},
        )

    # ------------------------------------------------------------------
    # choose_block  (compulsory -- there is no "decline")
    # ------------------------------------------------------------------

    def _choose_block(
        self,
        snap: Snapshot,
        pending: Mapping[str, Any],
        options: Sequence[Mapping[str, Any]],
    ) -> Command:
        frame = snap.frame(pending.get("frameId"))
        costs = []
        hector_free = (
            frame is not None
            and frame.name == "Hector MkI"
            and not self._hector_used(snap, frame)
        )
        # Against Guard Break the order matters and the engine deliberately
        # leaves it to the player. Blocking continues while any remaining card
        # covers any still-open zone, and one card clears every zone it has a
        # block in -- so spending the widest blocker first can answer the whole
        # attack with one card where spending a narrow one first costs two.
        guard_break = self._incoming_is_guard_break(snap)
        for option in options:
            card = self.card(option.get("key"))
            zones = [str(z) for z in (option.get("zones") or ZONES)]
            if card is None:
                costs.append(10.0)
                continue
            width = 0.0
            if guard_break:
                covered = sum(1 for z in zones if card.blocks.get(z, 0) > 0)
                width = GUARD_BREAK_WIDTH * max(0, covered - 1)
            if any(card.blocks.get(z, 0) >= 2 for z in zones):
                costs.append(-2.0 - width)      # super block: never discarded
                continue
            cost = self._card_loss(snap, frame, card, option)
            # Hector's first block each turn is not discarded either, so the
            # first one is nearly as cheap as a super block.
            costs.append((cost * 0.1 - 1.0 if hector_free else cost) - width)
        index = min(range(len(costs)), key=lambda i: (costs[i], i))
        return Command(
            "choose_block", self.seat, {"uid": str(options[index]["uid"])}
        )

    def _incoming_card(self, snap: Snapshot) -> Optional[CardInfo]:
        """The card currently resolving -- during a block, the attack itself.

        Deliberately not filtered to our own frames: what is hitting us belongs
        to the opponent, so `snap.resolving["frameId"]` is the *attacker* while
        `pending["frameId"]` is us, the defender. This wants the attacker.

        The log fallback matches on the card key only, never on a frame name,
        so it is unaffected by two frames sharing a name.
        """
        told = snap.resolving
        if told is not None:
            key = told.get("key")
            return self.card(str(key)) if key else None
        for entry in reversed(list(snap.log)):
            match = _RESOLVES_RE.match(str(entry.get("text", "")))
            if match:
                return self.card(match.group("key"))
        return None

    def _incoming_is_guard_break(self, snap: Snapshot) -> bool:
        """Is the attack we are being asked to block a Guard Break?

        The engine says so directly on the attack in flight when the caller
        supplies `resolving`; otherwise read it off the attacking card.
        """
        told = snap.resolving
        attack = told.get("attack") if told else None
        if attack is not None and "guardBreak" in attack:
            return bool(attack["guardBreak"])
        incoming = self._incoming_card(snap)
        return bool(incoming and incoming.guard_break)

    def _hector_used(self, snap: Snapshot, frame: FrameView) -> bool:
        marker = "Hector's first block of the turn is not discarded"
        return any(
            entry.get("turn") == snap.turn and marker in str(entry.get("text", ""))
            for entry in snap.log
        )

    def _card_loss(
        self,
        snap: Snapshot,
        frame: Optional[FrameView],
        card: CardInfo,
        option: Mapping[str, Any],
    ) -> float:
        """What it costs to spend this card as a block.

        A card that has already resolved costs almost nothing -- its action is
        spent. An unresolved one forfeits whatever it was going to do, which
        is exactly the trade a cheap fast attack is trying to force.
        """
        uid = str(option.get("uid"))
        resolved = False
        if frame is not None:
            for ref in frame.committed + frame.on_field:
                if ref.uid == uid:
                    resolved = ref.resolved
                    break
        if resolved:
            return 0.1 + 0.05 * len(card.block_zones)
        if frame is not None and card.is_attack and card.group in self._reloading(frame):
            # It is about to be swallowed by the reload and do nothing at all,
            # so spending it on a block forfeits nothing.
            return 0.15
        forfeit = sum(card.attacks.values()) * self.params.aggression
        forfeit += 0.4 * len(card.block_zones)
        forfeit += 0.05 * card.init
        if frame is not None and frame.pos is not None and card.is_attack:
            # Only count the attack we were actually going to land.
            reachable = any(
                S.can_reach_target(snap, frame, card, frame.pos, e, self._los)
                for e in snap.enemies()
            )
            if not reachable:
                forfeit *= S.NO_TARGET + 0.35
        return forfeit

    # ------------------------------------------------------------------
    # effect_choice
    # ------------------------------------------------------------------

    def _effect_choice(
        self,
        snap: Snapshot,
        pending: Mapping[str, Any],
        options: Sequence[Mapping[str, Any]],
    ) -> Command:
        if any("mulligan" in o for o in options):
            keep = self._mulligan(snap, pending)
            for option in options:
                if bool(option.get("mulligan")) == (not keep):
                    return Command("effect_choice", self.seat, dict(option))
        if any("frame" in o and "x" in o for o in options):
            return Command(
                "effect_choice", self.seat, dict(self._shove(snap, options))
            )
        # A bare list of tiles: a drone's move, Ace Reflexes, a Teleport. All
        # of them want the same thing -- somewhere useful to stand.
        if options and all(
            "x" in o and "y" in o and "frame" not in o for o in options
        ):
            return Command(
                "effect_choice", self.seat, dict(self._pick_tile(snap, options))
            )
        # A list of things to shoot at: the drone's target, which may be an
        # enemy frame or one of its Ephemeral Images.
        if options and all(("frame" in o) or ("token" in o) for o in options):
            return Command(
                "effect_choice", self.seat, dict(self._pick_victim(snap, options))
            )
        # Parallel Action: a second hand, and which of the actions already face
        # down to throw away for it.
        if any("swap" in o for o in options):
            return Command(
                "effect_choice", self.seat, dict(self._pick_swap(snap, pending, options))
            )
        return Command("effect_choice", self.seat, dict(options[0]))

    def _pick_swap(
        self,
        snap: Snapshot,
        pending: Mapping[str, Any],
        options: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        """Parallel Action, both halves: worst card out, best card in.

        Rated one card at a time with the same function that rates a whole
        turn's hand, which is crude -- it cannot see that the two remaining
        actions want to be from the same weapon -- but it is the same yardstick
        the planner uses, so the swap at least moves in the planner's direction.
        """
        frame = snap.frame(pending.get("frameId"))
        prof = self.opponent_profile(snap)
        health = frame.health if frame is not None else {z: 3 for z in ZONES}
        rated: list[tuple[float, Mapping[str, Any]]] = []
        for option in options:
            card = self.card(str(option.get("key")))
            if card is None:
                continue
            rated.append(
                (S.score_hand([card], prof, self.params, health), option)
            )
        if not rated:
            return options[0]
        outgoing = any(o.get("swap") == "out" for o in options)
        if outgoing:
            worst_score, worst = min(rated, key=lambda pair: pair[0])
            done = next((o for o in options if o.get("done")), None)
            # Only spend the swap on something actually poor: a hand rated at
            # or above what the planner already chose is not worth churning.
            if done is not None and worst_score >= self._swap_floor(rated):
                return done
            return worst
        return max(rated, key=lambda pair: pair[0])[1]

    @staticmethod
    def _swap_floor(rated: Sequence[tuple[float, Mapping[str, Any]]]) -> float:
        """The middle of what is on offer -- swap the below-average half."""
        scores = sorted(score for score, _ in rated)
        return scores[len(scores) // 2]

    def _pick_tile(
        self, snap: Snapshot, options: Sequence[Mapping[str, Any]]
    ) -> Mapping[str, Any]:
        """Stand where whatever is going there can actually do its job.

        Deliberately crude -- this answers a drone's move, a drone's placement,
        a reflex step and a Teleport with one rule, and none of them is worth a
        search. The rule is "get to the distance the thing wants to be at":
        that is 1 for anything melee, and its printed range for something that
        shoots, which the engine puts on the option as `reach`. Without that a
        Gun Tower -- immobile, two hit points, range 8 -- would be built in the
        enemy's face and die for nothing. Ties break toward the frame the team
        is already focusing on.
        """
        focus_id, _ = self._focus_weight(self.team_plan(snap), 0.0)
        marks = [
            f for f in snap.enemies() if f.pos is not None
        ]
        if not marks:
            return options[0]
        best, best_score = options[0], -1e9
        for option in options:
            pos = Pos(int(option["x"]), int(option["y"]))
            want = max(1, int(option.get("reach") or 0))
            gaps = [(snap.distance(pos, f.pos), f) for f in marks]
            gap, nearest = min(gaps, key=lambda pair: pair[0])
            # For melee (`want` 1) this is the old "adjacent is best, then as
            # close as you can get", in the same order.
            score = -abs(float(gap) - want)
            if nearest.id == focus_id:
                score += 0.5
            if score > best_score:
                best, best_score = option, score
        return best

    def _pick_victim(
        self, snap: Snapshot, options: Sequence[Mapping[str, Any]]
    ) -> Mapping[str, Any]:
        """Whom a drone shoots: the focus target, else whoever is closest to dead."""
        focus_id, _ = self._focus_weight(self.team_plan(snap), 0.0)
        best, best_score = options[0], -1e9
        for option in options:
            if option.get("token"):
                # An image: worth a third of a real hit, and worth stripping.
                score = 0.5
            else:
                target = snap.frame(str(option.get("frame")))
                if target is None:
                    continue
                score = 3.0 - 0.4 * target.total_remaining
                if target.seat == self.seat:
                    # Several cards offer "target frame" without saying whose
                    # (Lockdown, Doom, System Override). Ours is never the
                    # answer when an enemy is on the list; when the card is a
                    # kindness (Battlefield Repairs, Encode) every option is
                    # ours and this shifts them all alike.
                    score -= 5.0
                if target.id == focus_id:
                    score += 1.5
            if score > best_score:
                best, best_score = option, score
        return best

    def _mulligan(self, snap: Snapshot, pending: Mapping[str, Any]) -> bool:
        """True to keep the hand. Kuwagata's once-per-game redraw."""
        frame = snap.frame(pending.get("frameId"))
        if frame is None or not frame.hand:
            return True
        cards = [self.card(key) for _uid, key in frame.hand]
        cards = [c for c in cards if c is not None]
        if not cards:
            return False
        prof = self.opponent_profile(snap)
        # Redraw a hand that cannot cover a lethal threat, or one with nothing
        # to hit with at all.
        best_pair = min(
            (
                S.survival_deficit([a, b], prof, frame.health)
                for a, b in combinations(cards, 2)
            ),
            default=0.0,
        )
        if best_pair > 0:
            return False
        attackers = sum(1 for c in cards if c.is_attack)
        blockers = sum(1 for c in cards if c.block_zones)
        return attackers >= 1 and blockers >= 1

    def _shove(
        self, snap: Snapshot, options: Sequence[Mapping[str, Any]]
    ) -> Mapping[str, Any]:
        """`Frame_Call of Nature`: pick where to shove an enemy.

        Drag them off objective tiles and into the reach of whatever we still
        have committed.
        """
        prof = self.opponent_profile(snap)
        params = self.params
        objective_tiles = {t for obj in snap.objectives if not obj.settled for t in obj.tiles}
        best = options[0]
        best_score = -1e9
        for option in options:
            target = snap.frame(str(option.get("frame")))
            if target is None:
                continue
            dest = Pos(int(option["x"]), int(option["y"]))
            score = 0.0
            if target.pos in objective_tiles and dest not in objective_tiles:
                score += 3.0 * params.objective_weight
            if dest in objective_tiles:
                score -= 3.0 * params.objective_weight
            for mine in snap.mine():
                if mine.pos is None:
                    continue
                for ref in mine.committed:
                    card = self.card(ref.key)
                    if card is None or not card.is_attack or ref.resolved:
                        continue
                    moved = replace(target, pos=dest)
                    if S.can_reach_target(snap, mine, card, mine.pos, moved, self._los):
                        score += S.attack_value(
                            snap, mine, card, mine.pos, moved, prof, params
                        )
            if score > best_score:
                best_score, best = score, option
        return best

    # ------------------------------------------------------------------
    # echo_card
    # ------------------------------------------------------------------

    def _echo_card(
        self,
        snap: Snapshot,
        pending: Mapping[str, Any],
        options: Sequence[Mapping[str, Any]],
    ) -> Command:
        """Echoes of the fallen: a free extra card that can block. Always take it."""
        prof = self.opponent_profile(snap)
        hosts = [o for o in options if o.get("host")]
        if not hosts:
            return Command("echo_card", self.seat, dict(options[0]))
        best = max(
            hosts,
            key=lambda o: self._echo_host_value(snap, str(o["host"]), prof),
        )
        return Command(
            "echo_card", self.seat, {"dead": str(best["dead"]), "host": str(best["host"])}
        )

    def _echo_host_value(self, snap: Snapshot, host_id: str, prof: S.Profile) -> float:
        host = snap.frame(host_id)
        if host is None:
            return -1.0
        # The frame most likely to be shot at, and most likely to die if it is.
        frailty = 1.0 - host.total_remaining / max(1, sum(host.armour.values()))
        return self._pressure(snap, host, prof) * (1.0 + frailty)
