"""Headless AI-vs-AI harness -- `python -m playtest.ai.arena`.

Plays N games between two configurations and reports enough to actually tune
with: win rate, victory points split into kills and objectives, how the games
ended, and a set of behavioural diagnostics (damage dealt, blocks forced,
attacks that found no target, actions burnt on a reload) that say *why* one
parameter set beat another rather than merely that it did.

Everything is seeded. Game *g* of a run uses `seed + g`, and each agent's own
randomness is seeded from the game seat, so a run reproduces exactly.

Sides are swapped between the seats every other game by default. The decks
stay pinned to the seats, so over an even number of games each side plays both
seats and both squads -- otherwise the run measures the deck matchup and the
turn-1 priority marker rather than the parameters. An identical-vs-identical
match is the control, and it should land on 50%.

Examples::

    python -m playtest.ai.arena --games 40
    python -m playtest.ai.arena --games 40 --a veteran --b random
    python -m playtest.ai.arena --games 40 --a standard --b 'standard:aggression=2'
    python -m playtest.ai.arena --matrix --games 20
    python -m playtest.ai.arena --sweep aggression=0.5,1.0,1.5,2.0 --games 20
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

from ..engine import (
    GameConfig,
    apply_command,
    catalogue_json,
    is_over,
    load_cards,
    new_game,
    scores,
    view_for,
)
from .agent import Agent
from .baseline import GreedyAgent, RandomAgent
from .params import AIParams, PRESETS, params_from_dict, preset

#: The default matchup: one squad per faction pairing, three frames a side.
DEFAULT_DECKS_A = ("deck_aegis_percival", "deck_aegis_hector", "deck_collective_adam")
DEFAULT_DECKS_B = ("deck_guild_nautilus", "deck_ouwa_kamikiri", "deck_church_elemiah")

#: A stall guard. A 5-turn 3v3 game is a few hundred decisions; anything past
#: this means the engine and the agents are not making progress.
MAX_DECISIONS = 20000


# --------------------------------------------------------------------------
# Configurations
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Side:
    """One competitor: a label, an agent factory and its parameters."""

    label: str
    factory: Callable[..., Any]
    params: Optional[AIParams] = None

    def build(self, seat: int, catalogue: Mapping[str, Any], seed: int):
        return self.factory(
            seat=seat, catalogue=catalogue, params=self.params, seed=seed,
            name=self.label,
        )


_OVERRIDE_RE = re.compile(r"^([A-Za-z_]+)=(-?[0-9.]+)$")


def parse_side(spec: str) -> Side:
    """`"veteran"`, `"random"`, `"greedy"` or `"standard:aggression=2,pool=5"`."""
    spec = spec.strip()
    if spec == "random":
        return Side("random", RandomAgent)
    if spec == "greedy":
        return Side("greedy", GreedyAgent)
    base, _, rest = spec.partition(":")
    base = base or "standard"
    if base not in PRESETS:
        raise SystemExit(
            f"unknown preset {base!r}; choose from {', '.join(sorted(PRESETS))}, "
            "'random' or 'greedy'"
        )
    params = preset(base)
    if rest:
        changes: dict[str, Any] = {}
        for chunk in rest.split(","):
            match = _OVERRIDE_RE.match(chunk.strip())
            if not match:
                raise SystemExit(f"cannot parse parameter override {chunk!r}")
            changes[match.group(1)] = float(match.group(2))
        params = params_from_dict({**params.to_dict(), **changes})
    return Side(spec, Agent, params)


# --------------------------------------------------------------------------
# One game
# --------------------------------------------------------------------------


@dataclass
class GameResult:
    seed: int
    #: Seat each side sat in this game.
    seat_of: dict[str, int]
    #: Decks each seat played. Pinned to the seat, never to the side.
    seat_decks: dict[int, list[str]]
    vp: dict[int, int]
    kills: dict[int, int]
    objective_vp: dict[int, int]
    survivors: dict[int, int]
    turns: int
    decisions: int
    seconds: float
    winner: Optional[str]                       # side label, or None for a draw
    #: Wall-clock seconds inside `agent.act()`, per side label.
    think: dict[str, list[float]] = field(default_factory=dict)
    #: Behavioural counters keyed by side label.
    diagnostics: dict[str, dict[str, float]] = field(default_factory=dict)


_DAMAGE_RE = re.compile(r"^(?P<who>.+?) takes (?P<n>\d+) (?P<zone>High|Mid|Low) damage$")
_BLOCK_RE = re.compile(r"^(?P<who>.+?) blocks with .+ \((kept|discarded)\)$")
_NO_TARGET_RE = re.compile(r"^(?P<key>.+) has no legal target$")
_RELOAD_SPENT_RE = re.compile(r"is spent reloading")
_RESOLVES_RE = re.compile(r"^(?P<who>.+?) resolves (?P<key>.+?) \(initiative")


def play_game(
    side_a: Side,
    side_b: Side,
    seed: int,
    *,
    decks_a: Sequence[str] = DEFAULT_DECKS_A,
    decks_b: Sequence[str] = DEFAULT_DECKS_B,
    swap: bool = False,
    catalogue: Optional[Mapping[str, Any]] = None,
) -> GameResult:
    """Play one full game and return everything the report needs.

    `swap` moves the *sides* between the seats; the decks stay pinned to the
    seats. That matters: an earlier version swapped the decks along with the
    sides, so side A always played the same squad, and ten identical-vs-
    identical control matches came out 57.8% to side A -- the harness was
    measuring the deck matchup and calling it a parameter difference. With the
    decks pinned, an even number of games gives each side both seats and both
    squads, and the same control lands on 50%.
    """
    catalogue = catalogue if catalogue is not None else catalogue_json(load_cards())
    seat_side = {0: side_b, 1: side_a} if swap else {0: side_a, 1: side_b}
    seat_decks = {0: list(decks_a), 1: list(decks_b)}

    config = GameConfig(
        player_decks=seat_decks[0],
        ai_decks=seat_decks[1],
        seed=seed,
        frames_per_side=min(len(seat_decks[0]), len(seat_decks[1])),
    )
    state = new_game(config)
    agents = {
        seat: seat_side[seat].build(seat, catalogue, seed * 977 + seat)
        for seat in (0, 1)
    }

    started = time.perf_counter()
    decisions = 0
    think: dict[str, list[float]] = {side.label: [] for side in seat_side.values()}
    while not is_over(state) and decisions < MAX_DECISIONS:
        pending = state.pending
        if pending is None:
            break
        seat = int(pending.seat)
        view = view_for(state, seat)
        # Time the agent alone. The engine's own work is the same whoever is
        # playing, so mixing it in would flatter a slow agent.
        tick = time.perf_counter()
        command = agents[seat].act(view)
        think[seat_side[seat].label].append(time.perf_counter() - tick)
        if command is None:
            raise RuntimeError(
                f"agent for seat {seat} declined to answer a {pending.kind} decision"
            )
        state = apply_command(state, command)
        decisions += 1
    seconds = time.perf_counter() - started

    points = scores(state)
    kills = {seat: int(state.kills.get(seat, 0)) for seat in state.seats}
    objective_vp = {
        seat: int(points.get(seat, 0)) - kills[seat] for seat in state.seats
    }
    survivors = {
        seat: sum(1 for f in state.frames.values() if f.seat == seat and f.alive)
        for seat in state.seats
    }

    label_of = {seat: seat_side[seat].label for seat in (0, 1)}
    if points.get(0, 0) > points.get(1, 0):
        winner = label_of[0]
    elif points.get(1, 0) > points.get(0, 0):
        winner = label_of[1]
    else:
        winner = None

    return GameResult(
        seed=seed,
        seat_of={label_of[0]: 0, label_of[1]: 1},
        seat_decks={seat: list(decks) for seat, decks in seat_decks.items()},
        vp={s: int(points.get(s, 0)) for s in state.seats},
        kills=kills,
        objective_vp=objective_vp,
        survivors=survivors,
        turns=min(int(state.turn), 5),
        decisions=decisions,
        seconds=seconds,
        think=think,
        winner=winner,
        diagnostics=_diagnose(state, label_of, agents),
    )


def _diagnose(state, label_of: Mapping[int, str], agents: Mapping[int, Any]) -> dict:
    """Behavioural counters, read off the event log after the game.

    The log is the same public record both seats see, so nothing here depends
    on peeking at hidden state -- it is only the harness reporting on a
    finished game anyway.
    """
    # The engine names frames by id, and an id carries its team, so reading a
    # side off a log line is exact -- no name is ever shared between seats.
    seat_of_frame = {frame.id: frame.seat for frame in state.frames.values()}

    out = {
        label: {
            "damage_dealt": 0.0,
            "damage_taken": 0.0,
            "blocks_spent": 0.0,
            "cards_resolved": 0.0,
            "attacks_without_target": 0.0,
            "actions_lost_to_reload": 0.0,
        }
        for label in label_of.values()
    }

    def label_for(frame_id: str) -> Optional[str]:
        seat = seat_of_frame.get(frame_id)
        return None if seat is None else label_of[seat]

    # "<card> has no legal target" does not name the frame, so it is charged to
    # whoever resolved the card immediately before it -- the log is strictly
    # ordered, so that is always the right owner.
    resolving: Optional[str] = None
    for entry in state.log:
        text = str(entry.get("text", ""))
        match = _RESOLVES_RE.match(text)
        if match:
            resolving = label_for(match.group("who"))
            if resolving is not None:
                out[resolving]["cards_resolved"] += 1
            continue
        match = _DAMAGE_RE.match(text)
        if match:
            victim = label_for(match.group("who"))
            if victim is not None:
                out[victim]["damage_taken"] += int(match.group("n"))
                for label in out:
                    if label != victim:
                        out[label]["damage_dealt"] += int(match.group("n"))
            continue
        match = _BLOCK_RE.match(text)
        if match:
            who = label_for(match.group("who"))
            if who is not None:
                out[who]["blocks_spent"] += 1
            continue
        if _NO_TARGET_RE.match(text):
            if resolving is not None:
                out[resolving]["attacks_without_target"] += 1
            continue
        if _RELOAD_SPENT_RE.search(text):
            who = label_for(text.split("'s ", 1)[0])
            if who is not None:
                out[who]["actions_lost_to_reload"] += 1

    for seat, agent in agents.items():
        stats = getattr(agent, "stats", {}) or {}
        out[label_of[seat]]["blunders"] = float(stats.get("blunder", 0))
        out[label_of[seat]]["fallbacks"] = float(stats.get("fallback", 0))
    return out


# --------------------------------------------------------------------------
# A match
# --------------------------------------------------------------------------


@dataclass
class MatchReport:
    side_a: str
    side_b: str
    games: int
    results: list[GameResult]

    def _per(self, label: str, pick: Callable[[GameResult, int], float]) -> list[float]:
        return [pick(r, r.seat_of[label]) for r in self.results]

    def summary(self, label: str) -> dict[str, float]:
        wins = sum(1 for r in self.results if r.winner == label)
        draws = sum(1 for r in self.results if r.winner is None)
        vp = self._per(label, lambda r, s: r.vp[s])
        kills = self._per(label, lambda r, s: r.kills[s])
        obj = self._per(label, lambda r, s: r.objective_vp[s])
        alive = self._per(label, lambda r, s: r.survivors[s])
        diag: dict[str, float] = {}
        for result in self.results:
            for key, value in result.diagnostics.get(label, {}).items():
                diag[key] = diag.get(key, 0.0) + value
        n = max(1, len(self.results))
        # Per-decision think time, pooled over every game.
        think = [t for r in self.results for t in r.think.get(label, ())]
        think.sort()
        per_turn = [
            sum(r.think.get(label, ())) / max(1, r.turns) for r in self.results
        ]
        return {
            "ms_mean": 1000.0 * statistics.fmean(think) if think else 0.0,
            "ms_p95": 1000.0 * think[min(len(think) - 1, int(0.95 * len(think)))] if think else 0.0,
            "ms_max": 1000.0 * think[-1] if think else 0.0,
            "sec_per_turn": statistics.fmean(per_turn) if per_turn else 0.0,
            "wins": wins,
            "draws": draws,
            "win_rate": wins / n,
            "score_rate": (wins + 0.5 * draws) / n,
            "vp": statistics.fmean(vp) if vp else 0.0,
            "kills": statistics.fmean(kills) if kills else 0.0,
            "objective_vp": statistics.fmean(obj) if obj else 0.0,
            "survivors": statistics.fmean(alive) if alive else 0.0,
            **{k: v / n for k, v in diag.items()},
        }

    def to_dict(self) -> dict[str, Any]:
        turns = [r.turns for r in self.results]
        return {
            "a": self.side_a,
            "b": self.side_b,
            "games": len(self.results),
            "meanTurns": statistics.fmean(turns) if turns else 0.0,
            "meanSeconds": statistics.fmean([r.seconds for r in self.results]) if self.results else 0.0,
            "meanDecisions": statistics.fmean([r.decisions for r in self.results]) if self.results else 0.0,
            "sides": {
                self.side_a: self.summary(self.side_a),
                self.side_b: self.summary(self.side_b),
            },
        }


def run_match(
    side_a: Side,
    side_b: Side,
    games: int,
    seed: int,
    *,
    decks_a: Sequence[str] = DEFAULT_DECKS_A,
    decks_b: Sequence[str] = DEFAULT_DECKS_B,
    swap: bool = True,
    catalogue: Optional[Mapping[str, Any]] = None,
    progress: bool = False,
) -> MatchReport:
    """Play `games` games, alternating seats when `swap`."""
    if side_a.label == side_b.label:
        side_b = Side(side_b.label + "'", side_b.factory, side_b.params)
    catalogue = catalogue if catalogue is not None else catalogue_json(load_cards())
    results: list[GameResult] = []
    for index in range(games):
        result = play_game(
            side_a, side_b, seed + index,
            decks_a=decks_a, decks_b=decks_b,
            swap=bool(swap and index % 2),
            catalogue=catalogue,
        )
        results.append(result)
        if progress:
            print(f"  game {index + 1}/{games}: {result.winner or 'draw'}"
                  f" ({result.seconds:.1f}s)", file=sys.stderr)
    return MatchReport(side_a.label, side_b.label, games, results)


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

_COLUMNS = (
    ("win%", "win_rate", "{:>6.1%}"),
    ("score%", "score_rate", "{:>7.1%}"),
    ("VP", "vp", "{:>5.2f}"),
    ("kills", "kills", "{:>6.2f}"),
    ("objVP", "objective_vp", "{:>6.2f}"),
    ("alive", "survivors", "{:>6.2f}"),
    ("dmg", "damage_dealt", "{:>6.1f}"),
    ("taken", "damage_taken", "{:>6.1f}"),
    ("blocks", "blocks_spent", "{:>7.1f}"),
    ("cards", "cards_resolved", "{:>6.1f}"),
    ("noTgt", "attacks_without_target", "{:>6.1f}"),
    ("reload", "actions_lost_to_reload", "{:>7.2f}"),
    ("ms/dec", "ms_mean", "{:>7.1f}"),
    ("p95ms", "ms_p95", "{:>7.1f}"),
    ("maxms", "ms_max", "{:>7.1f}"),
    ("s/turn", "sec_per_turn", "{:>7.2f}"),
)


def print_report(report: MatchReport, file=None) -> None:
    # Resolved at call time, not import time, so redirected stdout is honoured.
    file = file or sys.stdout
    data = report.to_dict()
    width = max(len(report.side_a), len(report.side_b), 10)
    print(
        f"\n{report.side_a}  vs  {report.side_b}"
        f"   ({data['games']} games, "
        f"{data['meanTurns']:.1f} turns, "
        f"{data['meanDecisions']:.0f} decisions, "
        f"{data['meanSeconds']:.2f}s each)",
        file=file,
    )
    header = "  " + "side".ljust(width) + "".join(
        f"{title:>8}" for title, _, _ in _COLUMNS
    )
    print(header, file=file)
    print("  " + "-" * (len(header) - 2), file=file)
    for label in (report.side_a, report.side_b):
        summary = report.summary(label)
        row = "  " + label.ljust(width)
        for _title, key, fmt in _COLUMNS:
            row += f"{fmt.format(summary.get(key, 0.0)):>8}"
        print(row, file=file)
    draws = report.summary(report.side_a)["draws"]
    if draws:
        print(f"  ({int(draws)} draw(s))", file=file)
    print(
        "  timing: ms/dec, p95ms and maxms are wall clock inside agent.act();"
        " s/turn is one side's total thinking per game turn.",
        file=file,
    )
    print(
        "  These are DESKTOP numbers -- a phone running CPython under Termux"
        " is several times slower.",
        file=file,
    )


def print_matrix(reports: Sequence[MatchReport], file=None) -> None:
    file = file or sys.stdout
    labels: list[str] = []
    for report in reports:
        for label in (report.side_a, report.side_b):
            if label not in labels:
                labels.append(label)
    table = {(r.side_a, r.side_b): r for r in reports}
    width = max(len(l) for l in labels) + 2
    print("\nscore rate, row vs column (win = 1, draw = 0.5)\n", file=file)
    print(" " * width + "".join(f"{l:>12}" for l in labels), file=file)
    for row in labels:
        line = row.ljust(width)
        for col in labels:
            if row == col:
                line += f"{'--':>12}"
                continue
            report = table.get((row, col))
            if report is not None:
                value = report.summary(row)["score_rate"]
            else:
                report = table.get((col, row))
                value = 1.0 - report.summary(col)["score_rate"] if report else None
            line += f"{value:>11.1%} " if value is not None else f"{'':>12}"
        print(line, file=file)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m playtest.ai.arena",
        description="Play headless AI-vs-AI games and compare parameter sets.",
    )
    parser.add_argument("--games", type=int, default=20, help="games per match")
    parser.add_argument("--seed", type=int, default=1, help="base seed; game g uses seed+g")
    parser.add_argument("--a", default="standard", help="side A: preset[:k=v,...], 'random' or 'greedy'")
    parser.add_argument("--b", default="random", help="side B, same syntax")
    parser.add_argument(
        "--matrix", action="store_true",
        help="round-robin every preset plus the random and greedy baselines",
    )
    parser.add_argument(
        "--sweep", default=None, metavar="NAME=V1,V2,...",
        help="play each value of one parameter against side B",
    )
    parser.add_argument("--decks-a", nargs="*", default=list(DEFAULT_DECKS_A))
    parser.add_argument("--decks-b", nargs="*", default=list(DEFAULT_DECKS_B))
    parser.add_argument("--no-swap", action="store_true", help="do not alternate seats")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    parser.add_argument("--progress", action="store_true", help="log each game to stderr")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    catalogue = catalogue_json(load_cards())
    swap = not args.no_swap
    common = dict(
        games=args.games, seed=args.seed,
        decks_a=args.decks_a, decks_b=args.decks_b,
        swap=swap, catalogue=catalogue, progress=args.progress,
    )

    reports: list[MatchReport] = []
    if args.matrix:
        labels = list(PRESETS) + ["greedy", "random"]
        sides = [parse_side(label) for label in labels]
        for i, first in enumerate(sides):
            for second in sides[i + 1:]:
                reports.append(run_match(first, second, **common))
    elif args.sweep:
        name, _, values = args.sweep.partition("=")
        opponent = parse_side(args.b)
        for value in values.split(","):
            spec = f"{args.a}:{name}={value}" if ":" not in args.a else f"{args.a},{name}={value}"
            reports.append(run_match(parse_side(spec), opponent, **common))
    else:
        reports.append(run_match(parse_side(args.a), parse_side(args.b), **common))

    if args.json:
        json.dump([r.to_dict() for r in reports], sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        for report in reports:
            print_report(report)
        if args.matrix:
            print_matrix(reports)
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
