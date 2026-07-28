#!/usr/bin/env python3
"""Card-balance combat simulator for mobileSuitGame.

This is a deliberately *simplified* combat model for testing the balance of card
stats against each other. It does NOT implement the full rules text. The
simplifications (as requested for balance testing) are:

  * Frames are always in range and always attacking each other. Range, movement,
    line-of-sight and terrain are ignored.
  * Frame abilities are ignored. Every frame simply has HEALTH (default 4) hit
    points in each of the three zones (High / Mid / Low), independent of frame.
  * Damage types are ignored; an attack deals damage equal to its attack score
    in each zone it attacks.
  * NvN fight: every frame on a team funnels all of its attacks onto a single
    designated *target* frame on the opposing team (focus fire).
  * Cards are drawn at random from each frame's deck (decks are expected to be
    skewed to steer the test).

Modelled faithfully (the parts that matter for balance):

  * Initiative ordering. Within a round all played cards resolve from highest
    initiative to lowest (rules.tex: "Higher numbers resolve before lower
    numbers"). Ties are broken randomly.
  * Block / attack semantics. Any card in a frame's hand with a matching block
    zone can be spent to block an incoming attack -- whether or not it has
    resolved yet. An attack is negated if ANY of its zones lines up with ANY zone
    of an available block; that block card is then *consumed* (one attack per
    block). Blocking is mandatory when possible.
  * Why initiative matters. Spending a card to block *before it resolves* means
    it never resolves -- its own attack never happens ("if a card is used to
    block before it resolves then it will never itself resolve"). A card that has
    already resolved (attacked) can therefore block for free, while a not-yet-
    resolved card must give up its attack to block. The higher-initiative side
    attacks first and can then still block with those resolved cards.

Lethal: damage accumulates across rounds within a game. A frame is destroyed the
moment any single zone takes *more than or equal* than HEALTH damage ( greater than or equak).

Decks are the same one-`card/{Group}_{Name}`-per-line CSVs used elsewhere.
Balance-test decks live in simulation/decks/ and can be named by basename.

Three sub-commands:
    match       one explicit matchup (team A vs team B)
    scale       the same deck-pair at 1v1, 2v2, 3v3 (each team all one deck)
    tournament  round-robin over a deck set, rendered as an HTML heatmap

Run:
    python simulation/simulate.py match --team-a only_attack_high.csv \
                                        --team-b only_block_mid.csv
    python simulation/simulate.py scale --deck-a even_mix.csv \
                                        --deck-b only_attack_high.csv
    python simulation/simulate.py tournament --decks-dir simulation/decks \
                                        --sizes 1 2 3 --output build/tournament.html
"""

from __future__ import annotations

import argparse
import csv
import random
from dataclasses import dataclass, field
from html import escape
from pathlib import Path

ZONES = ("High", "Mid", "Low")

# Repo root = parent of the simulation/ folder, so the script works from anywhere.
ROOT = Path(__file__).resolve().parent.parent

# Action-card CSVs that decks draw from (frames / terrain are not action cards).
CARD_CSVS = [
    "Weapon actions.csv",
    "Basic actions.csv",
    "Booster actions.csv",
    "Drone actions.csv",
    "Pilot actions.csv",
]


def _to_int(value: str) -> int:
    """Parse an int from a CSV cell, treating blanks / junk as 0."""
    value = (value or "").strip()
    if not value:
        return 0
    try:
        return int(float(value))
    except ValueError:
        return 0


@dataclass(frozen=True)
class Card:
    """A single action card, reduced to what the combat model needs."""

    key: str
    name: str
    group: str
    initiative: int
    attacks: tuple  # (high, mid, low) attack scores
    blocks: frozenset  # zones this card blocks, e.g. {"High", "Low"}

    @property
    def attack_zones(self) -> frozenset:
        return frozenset(z for z, a in zip(ZONES, self.attacks) if a > 0)

    @property
    def is_attack(self) -> bool:
        return any(a > 0 for a in self.attacks)


def load_cards() -> dict:
    """Build a {group}_{name} -> Card lookup from every action CSV."""
    cards: dict[str, Card] = {}
    for fname in CARD_CSVS:
        path = ROOT / fname
        if not path.exists():
            continue
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                name = (row.get("Name") or "").strip()
                group = (row.get("Group") or "").strip()
                if not name:
                    continue
                key = f"{group}_{name}"
                attacks = tuple(_to_int(row.get(f"{z}Attack", "")) for z in ZONES)
                blocks = frozenset(
                    z for z in ZONES if _to_int(row.get(f"{z}Block", "")) > 0
                )
                cards[key] = Card(
                    key=key,
                    name=name,
                    group=group,
                    initiative=_to_int(row.get("Initiative", "")),
                    attacks=attacks,
                    blocks=blocks,
                )
    return cards


def load_deck(deck_path: str, cards: dict) -> list:
    """Read a deck CSV (one `card/{group}_{name}` per line) into a list of Cards."""
    path = Path(deck_path)
    if not path.is_absolute() and not path.exists():
        # Balance decks live in simulation/decks/, demo decks in simulation/test/;
        # fall back through those, then the repo root.
        for base in (ROOT / "simulation" / "decks", ROOT / "simulation" / "test", ROOT):
            if (base / deck_path).exists():
                path = base / deck_path
                break
    deck: list[Card] = []
    missing: list[str] = []
    with open(path, encoding="utf-8-sig") as fh:
        for line in fh:
            entry = line.strip()
            if not entry:
                continue
            key = entry[5:] if entry.startswith("card/") else entry
            card = cards.get(key)
            if card is None:
                missing.append(key)
            else:
                deck.append(card)
    if missing:
        print(f"  warning: {path.name} has unknown cards: {missing}")
    if not deck:
        raise ValueError(f"deck {path} contains no usable cards")
    return deck


class Pile:
    """A frame's draw pile: shuffled, drawn without replacement, reshuffled when
    it runs low so long games keep respecting the deck's card ratios."""

    def __init__(self, deck: list, rng: random.Random):
        self.deck = deck
        self.rng = rng
        self._pile: list = []
        self.reset()

    def reset(self) -> None:
        self._pile = list(self.deck)
        self.rng.shuffle(self._pile)

    def draw(self, n: int) -> list:
        hand = []
        for _ in range(n):
            if not self._pile:
                self.reset()
            hand.append(self._pile.pop())
        return hand


@dataclass(eq=False)  # identity-based, so Frames are hashable dict keys
class Frame:
    """A combatant. Only the two designated targets ever take damage, but every
    frame draws a hand and contributes its attacks at the enemy target."""

    name: str
    team: str
    pile: Pile
    damage: dict = field(default_factory=lambda: {z: 0 for z in ZONES})
    is_target: bool = False

    def reset(self) -> None:
        self.damage = {z: 0 for z in ZONES}
        self.pile.reset()


class Play:
    """One card chosen as an action for the current turn, with its live state.

    A card may be spent to block an incoming attack whether or not it has already
    resolved. If it is spent *before* it resolves, it never resolves (its own
    attack never happens) -- that trade-off is what makes initiative matter."""

    __slots__ = ("card", "owner", "resolved", "consumed")

    def __init__(self, card: Card, owner: "Frame"):
        self.card = card
        self.owner = owner
        self.resolved = False   # has this card's own action happened yet?
        self.consumed = False   # has it been spent as a block?


def play_game(team_a: list, team_b: list, target_a: Frame, target_b: Frame,
              hand_size: int, health: int, max_rounds: int,
              rng: random.Random) -> tuple:
    """Play one game to destruction. Returns (winner, rounds).

    winner is "A", "B" (surviving team) or "draw" if max_rounds is hit."""
    for f in team_a + team_b:
        f.reset()

    all_frames = team_a + team_b
    enemy_target = {"A": target_b, "B": target_a}

    for rnd in range(1, max_rounds + 1):
        # Everyone chooses their actions for the turn.
        hands = {f: [Play(card, f) for card in f.pile.draw(hand_size)]
                 for f in all_frames}
        # Resolve highest initiative first; random tiebreak.
        events = sorted(
            (p for plays in hands.values() for p in plays),
            key=lambda p: (p.card.initiative, rng.random()), reverse=True)

        for play in events:
            if play.consumed:
                continue  # spent as a block before resolving -> never resolves
            play.resolved = True
            card = play.card
            if not card.is_attack:
                continue

            tgt = enemy_target[play.owner.team]
            if _dead(tgt, health):
                continue

            # The defender must block if it can (mandatory blocking).
            block = _choose_block(hands[tgt], card.attack_zones)
            if block is not None:
                block.consumed = True
                continue  # attack negated; the blocking card is spent

            for z, dmg in zip(ZONES, card.attacks):
                if dmg:
                    tgt.damage[z] += dmg

            # End the game the instant a target is destroyed.
            if _dead(target_a, health):
                return "B", rnd
            if _dead(target_b, health):
                return "A", rnd

    return "draw", max_rounds


def _choose_block(plays: list, attack_zones: frozenset):
    """Pick a card to block an attack on `attack_zones`, or None if impossible.

    A block covers the attack if any of its zones lines up with any attacked
    zone. Prefer an already-resolved card (its own action is already done, so
    spending it is free); otherwise sacrifice the weakest-attacking card."""
    candidates = [p for p in plays
                  if not p.consumed and (p.card.blocks & attack_zones)]
    if not candidates:
        return None
    candidates.sort(key=lambda p: (0 if p.resolved else 1,
                                   sum(p.card.attacks),
                                   p.card.initiative))
    return candidates[0]


def _dead(frame: Frame, health: int) -> bool:
    return any(v >= health for v in frame.damage.values())


def build_team(deck_paths: list, team_name: str, cards: dict, target_index: int,
               rng: random.Random) -> tuple:
    frames = []
    for dp in deck_paths:
        deck = load_deck(dp, cards)
        frame = Frame(name=Path(dp).stem, team=team_name, pile=Pile(deck, rng))
        frames.append(frame)
    idx = max(0, min(target_index, len(frames) - 1))
    frames[idx].is_target = True
    return frames, frames[idx]


@dataclass
class SimConfig:
    """Everything a matchup needs besides the decks themselves."""
    games: int = 2500
    hand: int = 2
    health: int = 4
    max_rounds: int = 200
    target_a: int = 0
    target_b: int = 0


def run_matchup(deck_a: list, deck_b: list, cards: dict, cfg: SimConfig,
                rng: random.Random) -> dict:
    """Play cfg.games between team A (deck_a paths) and team B (deck_b paths).

    Returns a stats dict: win counts, win rates, avg rounds-to-kill, target names."""
    team_a, target_a = build_team(deck_a, "A", cards, cfg.target_a, rng)
    team_b, target_b = build_team(deck_b, "B", cards, cfg.target_b, rng)

    wins = {"A": 0, "B": 0, "draw": 0}
    rounds_on_win = {"A": [], "B": []}
    for _ in range(cfg.games):
        winner, rnds = play_game(team_a, team_b, target_a, target_b,
                                 cfg.hand, cfg.health, cfg.max_rounds, rng)
        wins[winner] += 1
        if winner in rounds_on_win:
            rounds_on_win[winner].append(rnds)

    g = cfg.games or 1
    return {
        "wins": wins,
        "rate": {k: 100.0 * v / g for k, v in wins.items()},
        "avg_rounds": {k: (sum(r) / len(r) if r else None)
                       for k, r in rounds_on_win.items()},
        "target_a": target_a.name,
        "target_b": target_b.name,
        "size": len(team_a),
    }


# --------------------------------------------------------------------------- #
# Sub-command: match  (explicit teams, the original behaviour)
# --------------------------------------------------------------------------- #

def cmd_match(args) -> None:
    rng = random.Random(args.seed)
    cards = load_cards()
    cfg = SimConfig(args.games, args.hand, args.health, args.max_rounds,
                    args.target_a, args.target_b)
    stats = run_matchup(args.team_a, args.team_b, cards, cfg, rng)

    print("=" * 64)
    print("mobileSuitGame balance simulation")
    print("=" * 64)
    print(f"Team A: {[Path(d).stem for d in args.team_a]}  (target: {stats['target_a']})")
    print(f"Team B: {[Path(d).stem for d in args.team_b]}  (target: {stats['target_b']})")
    print(f"Games: {args.games}   hand={args.hand}   health={args.health}   "
          f"seed={args.seed}")
    print("-" * 64)
    _print_stat_lines(stats)
    print("=" * 64)


def _print_stat_lines(stats: dict) -> None:
    def line(label, key):
        n = stats["wins"][key]
        avg = stats["avg_rounds"].get(key)
        avg_str = f"{avg:5.1f}" if avg is not None else "  -  "
        print(f"  {label:<26} {n:>7}  ({stats['rate'][key]:5.1f}%)   "
              f"avg rounds to kill: {avg_str}")

    line("Team A wins (B destroyed)", "A")
    line("Team B wins (A destroyed)", "B")
    print(f"  {'Draws (round cap hit)':<26} {stats['wins']['draw']:>7}  "
          f"({stats['rate']['draw']:5.1f}%)")


# --------------------------------------------------------------------------- #
# Sub-command: scale  (same deck-pair at 1v1, 2v2, 3v3 ...)
# --------------------------------------------------------------------------- #

def cmd_scale(args) -> None:
    rng = random.Random(args.seed)
    cards = load_cards()
    cfg = SimConfig(args.games, args.hand, args.health, args.max_rounds)
    a, b = args.deck_a, args.deck_b

    print("=" * 72)
    print("mobileSuitGame team-size scaling")
    print("=" * 72)
    print(f"Team A deck: {Path(a).stem}     Team B deck: {Path(b).stem}")
    print(f"Games each: {args.games}   hand={args.hand}   health={args.health}   "
          f"seed={args.seed}")
    print("-" * 72)
    print(f"  {'Size':<7}{'A wins':>10}{'B wins':>10}{'draws':>10}"
          f"{'avg rounds':>14}")
    for n in args.sizes:
        stats = run_matchup([a] * n, [b] * n, cards, cfg, rng)
        ar = [v for v in stats["avg_rounds"].values() if v is not None]
        avg = sum(ar) / len(ar) if ar else float("nan")
        print(f"  {f'{n}v{n}':<7}"
              f"{stats['rate']['A']:>9.1f}%{stats['rate']['B']:>9.1f}%"
              f"{stats['rate']['draw']:>9.1f}%{avg:>14.1f}")
    print("=" * 72)


# --------------------------------------------------------------------------- #
# Sub-command: tournament  (round-robin over a deck set -> HTML report)
# --------------------------------------------------------------------------- #

def cmd_tournament(args) -> None:
    rng = random.Random(args.seed)
    cards = load_cards()
    cfg = SimConfig(args.games, args.hand, args.health, args.max_rounds)

    deck_paths = list(args.decks)
    if args.decks_dir:
        deck_paths += sorted(str(p) for p in Path(args.decks_dir).glob("*.csv"))
    if len(deck_paths) < 2:
        raise SystemExit("tournament needs at least 2 decks "
                         "(via --decks and/or --decks-dir)")
    names = [Path(p).stem for p in deck_paths]
    d = len(deck_paths)

    total = d * d * len(args.sizes)
    print(f"Round-robin: {d} decks x sizes {args.sizes}, "
          f"{args.games} games/cell ({total} matchups) ...")

    # grids[size][i][j] = stats for row-deck i (team A) vs col-deck j (team B).
    grids = {}
    for n in args.sizes:
        grid = [[run_matchup([a] * n, [b] * n, cards, cfg, rng)
                 for b in deck_paths] for a in deck_paths]
        grids[n] = grid
        print(f"  {n}v{n} done")

    html = render_tournament_html(names, grids, cfg)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"\nWrote visual report -> {out}")

    # Text ranking per size by mean A-win% across opponents.
    for n in args.sizes:
        grid = grids[n]
        print(f"\nRanking at {n}v{n} (mean win% as team A across all opponents):")
        scored = sorted(
            ((sum(grid[i][j]["rate"]["A"] for j in range(d)) / d, names[i])
             for i in range(d)), reverse=True)
        for rate, name in scored:
            print(f"  {rate:5.1f}%  {name}")


def _diverging(pct: float) -> tuple:
    """Diverging blue<->red fill for a win%: 50% neutral gray, 100% blue (row
    deck winning), 0% red (row deck losing). Returns (r, g, b)."""
    red, gray, blue = (0xD0, 0x3B, 0x3B), (0xF0, 0xEF, 0xEC), (0x2A, 0x78, 0xD6)
    if pct >= 50:
        t, lo, hi = (pct - 50) / 50, gray, blue
    else:
        t, lo, hi = (50 - pct) / 50, gray, red
    return tuple(round(lo[k] + (hi[k] - lo[k]) * t) for k in range(3))


def _ink_on(rgb: tuple) -> str:
    """Pick black or white text for contrast against a background rgb."""
    r, g, b = (c / 255 for c in rgb)
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#0b0b0b" if lum > 0.6 else "#ffffff"


def _render_heatmap(names: list, grid: list, n: int) -> str:
    """One <section> containing the win-rate heatmap table for team size n."""
    d = len(names)
    row_avg = [sum(grid[i][j]["rate"]["A"] for j in range(d)) / d for i in range(d)]

    head_cells = "".join(f"<th class='col'><div>{escape(nm)}</div></th>" for nm in names)
    body_rows = []
    for i, nm in enumerate(names):
        cells = []
        for j in range(d):
            st = grid[i][j]
            pct = st["rate"]["A"]
            rgb = _diverging(pct)
            ar = st["avg_rounds"]["A"]
            tip = (f"{names[i]} (A) vs {names[j]} (B) @ {n}v{n}\n"
                   f"A wins {st['rate']['A']:.1f}%  |  B wins {st['rate']['B']:.1f}%"
                   f"  |  draws {st['rate']['draw']:.1f}%"
                   + (f"\navg rounds to kill: {ar:.1f}" if ar is not None else ""))
            diag = " diag" if i == j else ""
            cells.append(f"<td class='cell{diag}' style='background:rgb{rgb};"
                         f"color:{_ink_on(rgb)}' title='{escape(tip)}'>{pct:.0f}</td>")
        avg_rgb = _diverging(row_avg[i])
        cells.append(f"<td class='cell avg' style='background:rgb{avg_rgb};"
                     f"color:{_ink_on(avg_rgb)}'>{row_avg[i]:.0f}</td>")
        body_rows.append(f"<tr><th class='row'>{escape(nm)}</th>{''.join(cells)}</tr>")

    return f"""<section>
    <h2>{n}v{n}</h2>
    <div class="scroll"><table>
      <thead><tr><th class="corner"></th>{head_cells}
        <th class="col avghead"><div>row avg</div></th></tr></thead>
      <tbody>{''.join(body_rows)}</tbody>
    </table></div>
  </section>"""


def render_tournament_html(names: list, grids: dict, cfg: SimConfig) -> str:
    """Self-contained HTML with one win-rate heatmap per team size. Cell (i, j) is
    deck i's win rate as team A against deck j as team B; every cell shows its
    number, so meaning never rides on colour alone."""
    sections = "\n".join(_render_heatmap(names, grids[n], n) for n in sorted(grids))
    subtitle = (f"{len(names)} decks &middot; sizes {', '.join(f'{n}v{n}' for n in sorted(grids))} "
                f"&middot; {cfg.games} games/cell &middot; hand {cfg.hand} "
                f"&middot; {cfg.health} HP/zone")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>mobileSuitGame — deck tournament</title>
<style>
  :root {{ --surface:#fcfcfb; --ink:#0b0b0b; --muted:#52514e; --line:#e7e6e2; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --surface:#1a1a19; --ink:#ffffff; --muted:#c3c2b7; --line:#33322f; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:2rem 1.5rem 3rem; background:var(--surface); color:var(--ink);
         font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
  h1 {{ font-size:1.35rem; margin:0 0 .25rem; }}
  h2 {{ font-size:1rem; margin:2rem 0 .75rem; color:var(--muted); }}
  .sub {{ color:var(--muted); margin:0 0 .5rem; font-size:.9rem; }}
  .scroll {{ overflow-x:auto; }}
  table {{ border-collapse:separate; border-spacing:2px; }}
  th, td {{ padding:0; }}
  th.col div {{ writing-mode:vertical-rl; transform:rotate(180deg); white-space:nowrap;
               padding:.4rem .1rem; color:var(--muted); font-weight:600; font-size:.8rem; }}
  th.row {{ text-align:right; padding-right:.6rem; color:var(--muted); font-weight:600;
           white-space:nowrap; font-size:.85rem; }}
  td.cell {{ width:44px; height:38px; text-align:center; vertical-align:middle;
            font-variant-numeric:tabular-nums; font-weight:600; border-radius:4px;
            font-size:.85rem; }}
  td.cell.diag {{ outline:2px solid var(--surface); outline-offset:-2px; opacity:.85; }}
  td.cell.avg {{ font-weight:800; }}
  th.avghead div {{ color:var(--ink); }}
  .legend {{ display:flex; align-items:center; gap:.6rem; margin:1.25rem 0 .5rem; font-size:.82rem;
            color:var(--muted); flex-wrap:wrap; }}
  .bar {{ width:220px; height:12px; border-radius:6px;
         background:linear-gradient(90deg, rgb(208,59,59), rgb(240,239,236), rgb(42,120,214)); }}
  .note {{ margin-top:.5rem; color:var(--muted); font-size:.82rem; max-width:56ch; }}
</style></head>
<body>
  <h1>mobileSuitGame — deck tournament</h1>
  <p class="sub">{subtitle}</p>
  <div class="legend">
    <span>row deck loses</span>
    <span class="bar"></span>
    <span>row deck wins</span>
    <span style="margin-left:.5rem">(0% &nbsp; 50% &nbsp; 100%)</span>
  </div>
  <p class="note">Each cell is the <strong>row</strong> deck's win rate playing as
  team&nbsp;A against the <strong>column</strong> deck as team&nbsp;B. 50% (gray) is
  an even matchup; the diagonal is a deck against itself. <em>row&nbsp;avg</em> is the
  mean across all opponents — a rough power ranking. Hover any cell for the full
  A/B/draw split.</p>
  {sections}
</body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p, default_games=2500):
        p.add_argument("--games", type=int, default=default_games,
                       help="games/matchup (2500 keeps run-to-run win%% variance ~1%%)")
        p.add_argument("--hand", type=int, default=2,
                       help="actions chosen per frame per turn")
        p.add_argument("--health", type=int, default=4, help="HP per zone")
        p.add_argument("--max-rounds", type=int, default=200,
                       help="round cap before a game is called a draw")
        p.add_argument("--seed", type=int, default=None,
                       help="RNG seed for reproducibility")

    m = sub.add_parser("match", help="one explicit matchup (team A vs team B)")
    m.add_argument("--team-a", nargs="+", required=True, help="deck CSV(s) for team A")
    m.add_argument("--team-b", nargs="+", required=True, help="deck CSV(s) for team B")
    m.add_argument("--target-a", type=int, default=0,
                   help="index of team A's focus-fired frame (default 0)")
    m.add_argument("--target-b", type=int, default=0,
                   help="index of team B's focus-fired frame (default 0)")
    add_common(m)
    m.set_defaults(func=cmd_match)

    s = sub.add_parser("scale", help="same deck-pair at 1v1, 2v2, 3v3 ...")
    s.add_argument("--deck-a", required=True, help="deck CSV for every frame on team A")
    s.add_argument("--deck-b", required=True, help="deck CSV for every frame on team B")
    s.add_argument("--sizes", type=int, nargs="+", default=[1, 2, 3],
                   help="team sizes to test (default 1 2 3)")
    add_common(s)
    s.set_defaults(func=cmd_scale)

    t = sub.add_parser("tournament", help="round-robin over a deck set -> HTML report")
    t.add_argument("--decks", nargs="*", default=[], help="deck CSVs to include")
    t.add_argument("--decks-dir", help="also include every *.csv in this folder")
    t.add_argument("--sizes", type=int, nargs="+", default=[1, 2, 3],
                   help="team sizes, one heatmap each (default 1 2 3)")
    t.add_argument("--output", default="build/tournament.html", help="HTML report path")
    add_common(t, default_games=2500)
    t.set_defaults(func=cmd_tournament)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
