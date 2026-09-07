"""Baselines the real agent has to beat.

`RandomAgent` plays a legal command chosen uniformly at random at every
decision. It is the control: if the scoring agent does not beat it by a wide
margin, the scorer is wrong, and no amount of parameter tuning will save it.

`GreedyAgent` adds targeting and nothing else. `CamperAgent` is the one that
is not a weaker version of the real thing at all: it plays a *strategy* --
walk to the objectives and stand on them -- with no evaluation function
behind it. That matters for tuning. A panel made only of parameter variants
of `Agent` measures a parameter set against its own family, and a change that
exploits a shared blind spot scores well on all of them at once; the camper
does not share the blind spot, because it is not looking at the same things.
It is also roughly what a human does on their first game, and the objectives
are half the victory points.

All three have the same `act(view) -> Command` shape as `Agent`, and the same
restriction -- they only ever see the seat's own redacted view -- so the arena
can drop any of them into either seat.
"""

from __future__ import annotations

import random
from typing import Any, Mapping, Optional, Sequence

from ..engine.types import Command


class RandomAgent:
    """Uniformly random legal play."""

    name = "random"

    def __init__(self, seat: int, catalogue: Mapping[str, Any] | None = None,
                 params: Any = None, seed: int = 0, name: str = "random") -> None:
        self.seat = int(seat)
        self.rng = random.Random(seed)
        self.name = name
        self.stats: dict[str, int] = {}

    def act(self, view: Mapping[str, Any]) -> Optional[Command]:
        pending = view.get("pending")
        if (
            not pending
            or pending.get("waiting")
            or int(pending.get("seat", -1)) != self.seat
        ):
            return None
        kind = str(pending.get("kind"))
        options: Sequence[Mapping[str, Any]] = list(pending.get("options") or ())
        if not options:
            return None
        self.stats[kind] = self.stats.get(kind, 0) + 1
        if kind == "commit_actions":
            uids = [str(o["uid"]) for o in options]
            take = min(2, len(uids))
            return Command(kind, self.seat, {"uids": self.rng.sample(uids, take)})
        return Command(kind, self.seat, dict(self.rng.choice(list(options))))


class GreedyAgent:
    """A shallower control: attacks whatever is biggest, moves at random.

    Useful for telling apart "the scorer helps" from "any targeting at all
    helps" -- it takes the highest-damage option at `attack_target`, blocks
    with the first legal card and is otherwise random.
    """

    name = "greedy"

    def __init__(self, seat: int, catalogue: Mapping[str, Any] | None = None,
                 params: Any = None, seed: int = 0, name: str = "greedy") -> None:
        self.seat = int(seat)
        self.rng = random.Random(seed)
        self.name = name
        self.stats: dict[str, int] = {}

    def act(self, view: Mapping[str, Any]) -> Optional[Command]:
        pending = view.get("pending")
        if (
            not pending
            or pending.get("waiting")
            or int(pending.get("seat", -1)) != self.seat
        ):
            return None
        kind = str(pending.get("kind"))
        options: Sequence[Mapping[str, Any]] = list(pending.get("options") or ())
        if not options:
            return None
        self.stats[kind] = self.stats.get(kind, 0) + 1
        if kind == "commit_actions":
            uids = [str(o["uid"]) for o in options]
            take = min(2, len(uids))
            return Command(kind, self.seat, {"uids": self.rng.sample(uids, take)})
        if kind == "attack_target":
            best = max(
                options,
                key=lambda o: (
                    sum(int(v) for v in (o.get("zones") or {}).values()),
                    str(o.get("id")),
                ),
            )
            return Command(kind, self.seat, {"kind": str(best["kind"]), "id": str(best["id"])})
        if kind == "choose_block":
            return Command(kind, self.seat, {"uid": str(options[0]["uid"])})
        return Command(kind, self.seat, dict(self.rng.choice(list(options))))


class CamperAgent:
    """Walks to the objectives and sits on them. No evaluation function.

    A strategy rather than a scorer, and a good one: objectives are about half
    the victory points, they are scored by standing somewhere, and standing
    somewhere is free. It commits whatever blocks most (it expects to be shot
    at while it squats), attacks whatever is biggest when something wanders
    into range, and otherwise walks at the nearest objective tile it is
    allowed to score.

    Deliberately built on the raw view dict and nothing else -- no `scoring`,
    no `view.Snapshot` -- so that when the real agent is measured against it,
    the two are not sharing a model of the board and cannot share a mistake.
    """

    name = "camper"

    def __init__(self, seat: int, catalogue: Mapping[str, Any] | None = None,
                 params: Any = None, seed: int = 0, name: str = "camper") -> None:
        self.seat = int(seat)
        self.catalogue = dict(catalogue or {})
        self.rng = random.Random(seed)
        self.name = name
        self.stats: dict[str, int] = {}

    # -- the board, in the plainest possible terms -----------------------

    @staticmethod
    def _distance(a: Mapping[str, Any], b: Mapping[str, Any]) -> int:
        """Chebyshev, which is what the engine's square grid uses."""
        return max(abs(int(a["x"]) - int(b["x"])), abs(int(a["y"]) - int(b["y"])))

    def _goal_tiles(self, view: Mapping[str, Any]) -> list[dict[str, int]]:
        """Every tile worth standing on, best first."""
        tiles: list[tuple[int, dict[str, int]]] = []
        for obj in ((view.get("board") or {}).get("objectives") or ()):
            if obj.get("settled"):
                continue
            mine = int(obj.get("owner", 0)) == self.seat
            stake = int(obj.get("defend" if mine else "attack", 0))
            if stake <= 0:
                continue
            for tile in (obj.get("tiles") or ()):
                tiles.append((stake, {"x": int(tile[0]), "y": int(tile[1])}))
        tiles.sort(key=lambda pair: -pair[0])
        return [tile for _stake, tile in tiles]

    def _enemies(self, view: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        return [
            f for f in (view.get("frames") or ())
            if int(f.get("seat", 0)) != self.seat and f.get("alive") and f.get("pos")
        ]

    def _target_tile(self, view: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
        """Where to walk: the best objective tile, else at the nearest enemy."""
        goals = self._goal_tiles(view)
        if goals:
            return goals[0]
        enemies = self._enemies(view)
        return enemies[0]["pos"] if enemies else None

    # -- decisions -------------------------------------------------------

    def act(self, view: Mapping[str, Any]) -> Optional[Command]:
        pending = view.get("pending")
        if (
            not pending
            or pending.get("waiting")
            or int(pending.get("seat", -1)) != self.seat
        ):
            return None
        kind = str(pending.get("kind"))
        options: Sequence[Mapping[str, Any]] = list(pending.get("options") or ())
        if not options:
            return None
        self.stats[kind] = self.stats.get(kind, 0) + 1

        if kind == "commit_actions":
            return Command(kind, self.seat, {"uids": self._commit(view, options)})
        if kind in ("move", "deploy", "effect_choice", "move_token",
                    "place_objective"):
            tiled = [o for o in options if "x" in o and "y" in o]
            if tiled:
                goal = self._target_tile(view)
                if goal is not None:
                    best = min(tiled, key=lambda o: self._distance(o, goal))
                    return Command(kind, self.seat, dict(best))
        if kind == "attack_target":
            best = max(options, key=lambda o: (
                sum(int(v) for v in (o.get("zones") or {}).values()), str(o.get("id"))))
            return Command(kind, self.seat,
                           {"kind": str(best["kind"]), "id": str(best["id"])})
        if kind == "choose_block":
            return Command(kind, self.seat, {"uid": str(options[0]["uid"])})
        return Command(kind, self.seat, dict(self.rng.choice(list(options))))

    def _commit(
        self, view: Mapping[str, Any], options: Sequence[Mapping[str, Any]]
    ) -> list[str]:
        """Blocks first, then whatever moves furthest: it is going somewhere."""
        def rank(option: Mapping[str, Any]) -> tuple[int, int]:
            card = self.catalogue.get(str(option.get("key"))) or {}
            blocks = sum(
                1 for v in (card.get("blocks") or {}).values() if int(v or 0) > 0
            )
            return (blocks, int(card.get("movement") or 0))

        take = min(int((view.get("pending") or {}).get("pickMax") or 2), len(options))
        best = sorted(options, key=rank, reverse=True)[:take]
        return [str(o["uid"]) for o in best]


class BaiterAgent:
    """Strips the guard with cheap fast attacks, then lands one killing blow.

    Modelled on how a human actually beat this AI. Blocking is compulsory and
    spends the blocker, so an attack that is *certain* to be blocked is not a
    wasted action -- it is a card taken off the defender. Two real games went
    the same way: cheap fast hits pulled the blocks out, the defender's big
    slow weapons were eaten before they swung, and the kill was a single card
    taking a zone from full armour to destroyed.

    That is a strategy no parameter setting of `Agent` plays, which is the
    point of it being here. A panel of tuned variants of one scorer cannot
    price a defence against a tactic none of its members use, and `bait` was
    measured as a loss against exactly such a panel.

    The rule, in full:

    * commit the **fastest cheap attack** it holds and the **biggest single
      zone hit**, in that order of preference -- the first to force a block,
      the second to arrive after the guard is gone;
    * attack whichever enemy zone its damage would *finish*, else the softest;
    * block with the least valuable card that is legal, never the best one.

    Like the other baselines it reads only the seat's own view.
    """

    name = "baiter"

    def __init__(self, seat: int, catalogue: Mapping[str, Any] | None = None,
                 params: Any = None, seed: int = 0, name: str = "baiter") -> None:
        self.seat = int(seat)
        self.catalogue = dict(catalogue or {})
        self.rng = random.Random(seed)
        self.name = name
        self.stats: dict[str, int] = {}

    # -- reading a card off the raw catalogue JSON -----------------------

    def _card(self, key: str) -> Mapping[str, Any]:
        return self.catalogue.get(str(key)) or {}

    @staticmethod
    def _damage(card: Mapping[str, Any]) -> int:
        return sum(int(v or 0) for v in (card.get("attacks") or {}).values())

    @staticmethod
    def _biggest_zone(card: Mapping[str, Any]) -> int:
        values = [int(v or 0) for v in (card.get("attacks") or {}).values()]
        return max(values) if values else 0

    @staticmethod
    def _init(card: Mapping[str, Any]) -> int:
        got = card.get("initiative")
        if isinstance(got, (list, tuple)) and got:
            return int(got[0])
        return int(got or 0)

    def act(self, view: Mapping[str, Any]) -> Optional[Command]:
        pending = view.get("pending")
        if (
            not pending
            or pending.get("waiting")
            or int(pending.get("seat", -1)) != self.seat
        ):
            return None
        kind = str(pending.get("kind"))
        options: Sequence[Mapping[str, Any]] = list(pending.get("options") or ())
        if not options:
            return None
        self.stats[kind] = self.stats.get(kind, 0) + 1

        if kind == "commit_actions":
            return Command(kind, self.seat, {"uids": self._commit(view, options)})
        if kind == "attack_target":
            return Command(kind, self.seat, self._target(view, options))
        if kind == "choose_block":
            # Never spend a good card blocking: that is the trade it is trying
            # to force on the other side, not to take itself.
            worst = min(options, key=lambda o: self._damage(self._card(o.get("key"))))
            return Command(kind, self.seat, {"uid": str(worst["uid"])})
        if kind in ("move", "deploy", "effect_choice", "move_token",
                    "place_objective"):
            tiled = [o for o in options if "x" in o and "y" in o]
            marks = [
                f["pos"] for f in (view.get("frames") or ())
                if int(f.get("seat", 0)) != self.seat and f.get("alive") and f.get("pos")
            ]
            if tiled and marks:
                best = min(tiled, key=lambda o: min(
                    max(abs(int(o["x"]) - int(m["x"])), abs(int(o["y"]) - int(m["y"])))
                    for m in marks))
                return Command(kind, self.seat, dict(best))
        return Command(kind, self.seat, dict(self.rng.choice(list(options))))

    def _commit(
        self, view: Mapping[str, Any], options: Sequence[Mapping[str, Any]]
    ) -> list[str]:
        """One cheap fast attack to pull a block, one heavy hit to land."""
        take = min(int((view.get("pending") or {}).get("pickMax") or 2), len(options))
        attacks = [o for o in options if self._damage(self._card(o.get("key"))) > 0]
        if len(attacks) < take:
            return [str(o["uid"]) for o in options[:take]]
        chosen: list[Mapping[str, Any]] = []
        # The stripper: fastest, and cheap, so losing it to a block costs least.
        stripper = max(
            attacks,
            key=lambda o: (self._init(self._card(o.get("key"))),
                           -self._damage(self._card(o.get("key")))),
        )
        chosen.append(stripper)
        # The hammer: the biggest hit on any one zone, which is what kills.
        rest = [o for o in attacks if o is not stripper]
        while rest and len(chosen) < take:
            hammer = max(rest, key=lambda o: self._biggest_zone(self._card(o.get("key"))))
            chosen.append(hammer)
            rest = [o for o in rest if o is not hammer]
        for option in options:
            if len(chosen) >= take:
                break
            if option not in chosen:
                chosen.append(option)
        return [str(o["uid"]) for o in chosen[:take]]

    def _target(
        self, view: Mapping[str, Any], options: Sequence[Mapping[str, Any]]
    ) -> dict[str, str]:
        """Aim where the hit finishes a zone, else where it hurts most."""
        frames = {str(f.get("id")): f for f in (view.get("frames") or ())}

        def value(option: Mapping[str, Any]) -> tuple[int, int]:
            zones = {str(z): int(d) for z, d in (option.get("zones") or {}).items()}
            frame = frames.get(str(option.get("id")))
            if frame is None or not zones:
                return (0, sum(zones.values()))
            armour = frame.get("armour") or {}
            damage = frame.get("damage") or {}
            kills = 0
            for zone, hit in zones.items():
                left = int(armour.get(zone, 0)) - int(damage.get(zone, 0))
                if left > 0 and hit >= left:
                    kills = 1
            return (kills, sum(zones.values()))

        best = max(options, key=value)
        return {"kind": str(best["kind"]), "id": str(best["id"])}
