"""Read an exported game back -- `python -m playtest.ai.review <file.json>`.

The Log tab's **Save game** writes a file holding the config the game was
created from (the seed included), the public event log and the full
transcript: every command both seats made, in order. That is enough to
*replay* the game, and replaying it is what makes the file worth having --
the log says what happened, and the replay says what the board looked like
when it did.

So this does both. It feeds the transcript back through the engine and
reports, turn by turn, what each side committed, what landed, what was
blocked and what found nothing to hit -- then totals the same behavioural
counters the arena reports, so a game a human won can be read against the
numbers a tuning run produces.

A file exported mid-game has the AI's card identities stripped out (see
`Session.export`), so it cannot be replayed; that one falls back to reading
the event log alone, and says so.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from ..engine import (
    Command,
    GameConfig,
    apply_command,
    is_over,
    new_game,
    scores,
)

_RESOLVES = re.compile(r"^(?P<who>.+?) resolves (?P<key>.+?) \(initiative")
_DAMAGE = re.compile(r"^(?P<who>.+?) takes (?P<n>\d+) (?P<zone>High|Mid|Low) damage$")
_BLOCKS = re.compile(r"^(?P<who>.+?) blocks with (?P<key>.+?) \((?:kept|discarded)\)")
_NO_TARGET = re.compile(r"^(?P<key>.+) has no legal target$")
_DESTROYED = re.compile(r"^(?P<who>.+?) is destroyed")


def load(path: str | Path) -> dict[str, Any]:
    doc = json.loads(Path(path).read_text("utf-8"))
    if doc.get("schema") != "netframe.game-export/1":
        raise SystemExit(f"{path}: not a NetFrame game export")
    return doc


def replay(doc: Mapping[str, Any]) -> tuple[Optional[Any], list[str]]:
    """Play the transcript back through the engine.

    Returns `(final state, problems)`. A redacted export -- one saved while
    the game was still running -- has commands with their card uids removed
    and cannot be replayed at all, which is the first problem reported.
    """
    problems: list[str] = []
    transcript = list(doc.get("transcript") or ())
    if any(entry.get("redacted") for entry in transcript):
        return None, [
            "this game was exported while it was still running, so the AI's "
            "commands are redacted and it cannot be replayed -- the log "
            "summary below is all of it"
        ]
    config = dict(doc.get("config") or {})
    state = new_game(GameConfig(
        player_decks=list(config.get("playerDecks") or ()),
        ai_decks=list(config.get("aiDecks") or ()),
        seed=config.get("seed"),
        frames_per_side=int(config.get("framesPerSide") or 3),
        terrain_decks={
            int(seat): name
            for seat, name in (config.get("terrainDecks") or {}).items()
        } or None,
    ))
    for entry in transcript:
        pending = state.pending
        if pending is None:
            problems.append(f"#{entry['n']}: the game was already over")
            break
        if pending.kind != entry["kind"]:
            problems.append(
                f"#{entry['n']}: expected {pending.kind}, transcript says "
                f"{entry['kind']} -- the engine has changed since this was saved"
            )
            break
        try:
            state = apply_command(
                state, Command(entry["kind"], int(entry["seat"]),
                               dict(entry.get("payload") or {})),
            )
        except Exception as exc:               # a stale export, not a crash
            problems.append(f"#{entry['n']} ({entry['kind']}): {exc}")
            break
    return state, problems


# --------------------------------------------------------------------------
# Reading the log
# --------------------------------------------------------------------------


def _seat_of_frames(doc: Mapping[str, Any]) -> dict[str, int]:
    return {str(f["id"]): int(f["seat"]) for f in (doc.get("frames") or ())}


def tally(doc: Mapping[str, Any]) -> dict[int, dict[str, float]]:
    """The arena's behavioural counters, read off this game's event log."""
    seat_of = _seat_of_frames(doc)
    out = {
        seat: {k: 0.0 for k in (
            "cards_resolved", "damage_dealt", "damage_taken", "blocks_spent",
            "attacks_without_target", "kills",
        )}
        for seat in {v for v in seat_of.values()} or {0, 1}
    }
    resolving: Optional[int] = None
    for entry in doc.get("log") or ():
        text = str(entry.get("text", ""))
        match = _RESOLVES.match(text)
        if match:
            resolving = seat_of.get(match.group("who"))
            if resolving is not None:
                out[resolving]["cards_resolved"] += 1
            continue
        match = _DAMAGE.match(text)
        if match:
            victim = seat_of.get(match.group("who"))
            if victim is not None:
                out[victim]["damage_taken"] += int(match.group("n"))
                for seat in out:
                    if seat != victim:
                        out[seat]["damage_dealt"] += int(match.group("n"))
            continue
        match = _BLOCKS.match(text)
        if match:
            who = seat_of.get(match.group("who"))
            if who is not None:
                out[who]["blocks_spent"] += 1
            continue
        match = _DESTROYED.match(text)
        if match:
            victim = seat_of.get(match.group("who"))
            if victim is not None:
                for seat in out:
                    if seat != victim:
                        out[seat]["kills"] += 1
            continue
        if _NO_TARGET.match(text) and resolving is not None:
            out[resolving]["attacks_without_target"] += 1
    return out


def commitments(doc: Mapping[str, Any]) -> dict[int, Counter]:
    """What each side actually committed, by card, over the whole game."""
    out: dict[int, Counter] = {}
    for entry in doc.get("transcript") or ():
        if entry.get("kind") != "commit_actions":
            continue
        seat = int(entry["seat"])
        out.setdefault(seat, Counter()).update(entry.get("cards") or ())
    return out


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------


def report(doc: Mapping[str, Any], *, turns: bool = True, file=None) -> None:
    file = file or sys.stdout
    human = int(doc.get("humanSeat", 0))
    ai = int(doc.get("aiSeat", 1))
    names = {int(f["seat"]): [] for f in (doc.get("frames") or ())}
    for f in doc.get("frames") or ():
        names[int(f["seat"])].append(str(f["name"]))
    label = {human: "human", ai: "AI"}

    config = dict(doc.get("config") or {})
    print(f"game {doc.get('gameId')}  seed {config.get('seed')}  "
          f"{'finished' if doc.get('over') else 'in progress'} "
          f"at turn {doc.get('turn')}", file=file)
    print(f"  human seat {human}: {', '.join(config.get('playerDecks') or ())}",
          file=file)
    print(f"  AI    seat {ai}: {', '.join(config.get('aiDecks') or ())}",
          file=file)
    params = doc.get("aiParams") or {}
    print(f"  AI parameters: {json.dumps(params) if params else 'defaults'}"
          f"   ({doc.get('aiSource')})", file=file)

    points = {int(k): int(v) for k, v in (doc.get("scores") or {}).items()}
    kills = {int(k): int(v) for k, v in (doc.get("kills") or {}).items()}
    print("\nresult", file=file)
    for seat in sorted(points):
        print(f"  {label.get(seat, seat):<6} {points[seat]:>2} VP "
              f"({kills.get(seat, 0)} from kills, "
              f"{points[seat] - kills.get(seat, 0)} from objectives)", file=file)

    counts = tally(doc)
    print("\nhow it was played", file=file)
    header = f"  {'side':<7}" + "".join(
        f"{t:>9}" for t in ("cards", "dmg", "taken", "blocks", "noTgt", "kills")
    )
    print(header, file=file)
    for seat in sorted(counts):
        c = counts[seat]
        print(f"  {label.get(seat, seat):<7}"
              f"{c['cards_resolved']:>9.0f}{c['damage_dealt']:>9.0f}"
              f"{c['damage_taken']:>9.0f}{c['blocks_spent']:>9.0f}"
              f"{c['attacks_without_target']:>9.0f}{c['kills']:>9.0f}", file=file)
    print("  noTgt: attacks that resolved with nothing legal to hit -- a whole"
          " action spent on nothing.", file=file)

    played = commitments(doc)
    for seat in sorted(played):
        if not played[seat]:
            continue
        top = ", ".join(f"{key} x{n}" for key, n in played[seat].most_common(8))
        print(f"\n{label.get(seat, seat)} committed most: {top}", file=file)

    if turns:
        print("\nturn by turn", file=file)
        by_turn: dict[int, list[str]] = {}
        for entry in doc.get("log") or ():
            by_turn.setdefault(int(entry.get("turn", 0)), []).append(
                str(entry.get("text", "")))
        for turn in sorted(by_turn):
            lines = by_turn[turn]
            hits = sum(1 for t in lines if _DAMAGE.match(t))
            blocked = sum(1 for t in lines if _BLOCKS.match(t))
            missed = sum(1 for t in lines if _NO_TARGET.match(t))
            print(f"  turn {turn}: {len(lines)} events, {hits} damaging hits, "
                  f"{blocked} blocks, {missed} attacks with no target", file=file)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m playtest.ai.review",
        description="Read an exported NetFrame game back and report on it.",
    )
    parser.add_argument("path", help="the .json file the Log tab saved")
    parser.add_argument("--no-replay", action="store_true",
                        help="report from the log alone, without replaying")
    parser.add_argument("--log", action="store_true",
                        help="also print the whole event log")
    args = parser.parse_args(argv)

    doc = load(args.path)
    report(doc)

    if not args.no_replay:
        state, problems = replay(doc)
        print("\nreplay", file=sys.stdout)
        if problems:
            for problem in problems:
                print(f"  ! {problem}")
        if state is not None and not problems:
            final = scores(state)
            recorded = {int(k): int(v) for k, v in (doc.get("scores") or {}).items()}
            agree = all(final.get(s) == recorded.get(s) for s in recorded)
            print(f"  replayed {len(doc.get('transcript') or ())} commands; "
                  f"final score {dict(final)}; "
                  f"{'matches the export' if agree else 'DIFFERS from the export'}")
            if not agree:
                print("  (a mismatch means the engine has changed since the "
                      "game was played -- the file is still readable, but the "
                      "replay is not the same game)")
            print(f"  game over: {is_over(state)}")

    if args.log:
        print("\nevent log")
        for entry in doc.get("log") or ():
            print(f"  [{entry.get('turn')}] {entry.get('text')}")
    return 0


if __name__ == "__main__":                     # pragma: no cover
    raise SystemExit(main())
