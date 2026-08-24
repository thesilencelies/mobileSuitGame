"""Baselines the real agent has to beat.

`RandomAgent` plays a legal command chosen uniformly at random at every
decision. It is the control: if the scoring agent does not beat it by a wide
margin, the scorer is wrong, and no amount of parameter tuning will save it.

It has the same `act(view) -> Command` shape as `Agent`, and the same
restriction -- it only ever sees the seat's own redacted view -- so the arena
can drop either into either seat.
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
