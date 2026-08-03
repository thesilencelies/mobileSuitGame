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

Five sub-commands:
    match       one explicit matchup (team A vs team B)
    scale       the same deck-pair at 1v1, 2v2, 3v3 (each team all one deck)
    tournament  round-robin over a deck set, rendered as an HTML heatmap
    cards       every single card as a 1-card deck vs the chosen decks, as a table
    pool        marginal win% each card adds to a base deck vs the chosen decks

Run:
    python simulation/simulate.py match --team-a only_attack_high.csv \
                                        --team-b only_block_mid.csv
    python simulation/simulate.py scale --deck-a even_mix.csv \
                                        --deck-b only_attack_high.csv
    python simulation/simulate.py tournament --decks-dir simulation/decks \
                                        --sizes 1 2 3 --output build/tournament.html
    python simulation/simulate.py cards --decks-dir simulation/decks \
                                        --output build/card_sweep.html
    python simulation/simulate.py pool --base even_mix.csv \
                                        --decks-dir simulation/decks
"""

from __future__ import annotations

import argparse
import csv
import random
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from html import escape
from itertools import combinations
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
        # Never span a reshuffle mid-draw: that could deal the same card twice in
        # one hand. Cap at the deck size (a pool larger than the deck just means
        # "the whole deck"), and reshuffle first if the pile can't cover the draw.
        n = min(n, len(self.deck))
        if n > len(self._pile):
            self.reset()
        return [self._pile.pop() for _ in range(n)]


@dataclass(eq=False)  # identity-based, so Frames are hashable dict keys
class Frame:
    """A combatant. Only the two designated targets ever take damage, but every
    frame draws a hand and contributes its attacks at the enemy target."""

    name: str
    team: str
    pile: Pile
    damage: dict = field(default_factory=lambda: {z: 0 for z in ZONES})
    is_target: bool = False
    opp_profile: dict = None  # opposing team's aggregate profile (intelligent mode)
    strategy: tuple = None    # (defense_weight, concentration_weight); None = defaults

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


def deck_profile(cards_list: list) -> dict:
    """Aggregate a deck (or team's decks) into the tendencies used to pick cards
    intelligently: how often it blocks each zone, how much attack it throws at
    each zone (average points per card), and its initiative spread (so 'high/low
    initiative' is judged relative to THIS opponent, not an absolute scale)."""
    n = len(cards_list) or 1
    block_freq = {z: 0.0 for z in ZONES}
    atk_weight = {z: 0.0 for z in ZONES}
    for c in cards_list:
        for z in c.blocks:
            block_freq[z] += 1
        for z, a in zip(ZONES, c.attacks):
            atk_weight[z] += a
    atk_count = {z: 0 for z in ZONES}
    for c in cards_list:
        for z in c.attack_zones:
            atk_count[z] += 1
    return {"block_freq": {z: block_freq[z] / n for z in ZONES},
            "atk_weight": {z: atk_weight[z] / n for z in ZONES},
            "atk_freq": {z: atk_count[z] / n for z in ZONES},  # cards attacking z
            "inits": sorted(c.initiative for c in cards_list),
            "n": n}


def _rel_init(card: Card, prof: dict) -> float:
    """Where the card's initiative sits within the opponent's deck: ~0 = slower
    than all their cards (resolves last, blocks cost it its attack), ~1 = faster
    than all (resolves first, then blocks for free)."""
    inits, n = prof["inits"], prof["n"]
    lo = bisect_left(inits, card.initiative)
    hi = bisect_right(inits, card.initiative)
    return (lo + hi) / (2 * n) if n else 0.5


# Relative weights of the scoring terms (tune to taste).
DEFENSE_WEIGHT = 1.0
CONCENTRATION_WEIGHT = 0.6


def score_hand(cards: list, prof: dict, defense_weight: float = None,
               concentration_weight: float = None) -> float:
    """Rate a *combination* of cards chosen as one turn's actions, against the
    opponent's profile. `defense_weight` / `concentration_weight` override the
    module defaults (used for per-team strategy). Follows the requested logic:

      * Attacks — an attack is always credited for the damage it is likely to
        land (zones the opponent rarely blocks). A card that resolves BEFORE most
        of the opponent's cards gets an ADDITIONAL bonus for hitting zones they DO
        block, because it forces them to spend a card to block and lose that
        card's own attack. So low-initiative cards chase unblocked zones, while
        high-initiative cards are happy to hit blocked zones too.
      * Low-initiative cards prefer NOT to block (their block would cost them
        their attack) so they can land damage; high-initiative blocks are free.
      * Concentration — two attacks on the SAME zone stack toward overwhelming
        that zone's HP, so shared attack zones get a bonus.
      * Coverage — blocks are valued per zone up to how many attacks the opponent
        actually throws at that zone. Against a spread attacker one block each on
        several zones wins; against a deck that CONCENTRATES on one zone, a second
        (and third) block on that zone is credited too, because each incoming
        attack must be blocked separately."""
    if prof is None:
        return 0.0
    dw = DEFENSE_WEIGHT if defense_weight is None else defense_weight
    cw = CONCENTRATION_WEIGHT if concentration_weight is None else concentration_weight
    bf, aw, af = prof["block_freq"], prof["atk_weight"], prof["atk_freq"]

    offense = 0.0
    landing = {z: 0.0 for z in ZONES}       # attack expected to land, per zone
    hitters = {z: 0 for z in ZONES}         # how many chosen cards attack each zone
    block_avails = {z: [] for z in ZONES}   # availability of every block per zone
    for c in cards:
        t = _rel_init(c, prof)
        for z, a in zip(ZONES, c.attacks):
            if a:
                # base = damage likely to land; + high-init bonus for forcing a
                # block on zones they cover. hit_value = 1 - bf*(1-t).
                offense += a * (1 - bf[z] * (1 - t))
                landing[z] += a * (1 - bf[z])
                hitters[z] += 1
        # A low-init attacker is reluctant to block (it would lose its attack);
        # a pure block or a high-init card blocks essentially for free.
        avail = 1.0 if not c.is_attack else t
        for z in c.blocks:
            block_avails[z].append(avail)

    # Count only as many blocks per zone as the opponent is expected to attack it
    # (they play len(cards) actions/turn, af[z] of them at zone z).
    defense = 0.0
    for z in ZONES:
        need = max(1, round(af[z] * len(cards)))
        defense += aw[z] * sum(sorted(block_avails[z], reverse=True)[:need])
    defense *= dw

    concentration = cw * sum(landing[z] for z in ZONES if hitters[z] >= 2)
    return offense + defense + concentration


def _choose_hand(frame: Frame, hand_size: int, intelligent: bool, pool: int,
                 rng: random.Random) -> list:
    """The cards a frame commits as actions this turn. Random by default; in
    intelligent mode, draw a pool and keep the best-scoring COMBINATION of size
    hand_size (so synergy between the chosen cards counts)."""
    if intelligent and frame.opp_profile is not None:
        dw, cw = frame.strategy or (None, None)
        drawn = frame.pile.draw(max(pool, hand_size))
        k = min(hand_size, len(drawn))
        best = max(combinations(range(len(drawn)), k),
                   key=lambda idx: (score_hand([drawn[i] for i in idx], frame.opp_profile, dw, cw),
                                    rng.random()))
        return [drawn[i] for i in best]
    return frame.pile.draw(hand_size)


def play_game(team_a: list, team_b: list, target_a: Frame, target_b: Frame,
              hand_size: int, health: int, max_rounds: int,
              rng: random.Random, intelligent: bool = False, pool: int = 5) -> tuple:
    """Play one game to destruction. Returns (winner, rounds).

    winner is "A", "B" (surviving team) or "draw" if max_rounds is hit."""
    for f in team_a + team_b:
        f.reset()

    all_frames = team_a + team_b
    enemy_target = {"A": target_b, "B": target_a}

    for rnd in range(1, max_rounds + 1):
        # Everyone chooses their actions for the turn.
        hands = {f: [Play(card, f) for card in
                     _choose_hand(f, hand_size, intelligent, pool, rng)]
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
    intelligent: bool = False  # pick cards vs the opponent profile instead of random
    pool: int = 5              # cards drawn to choose from in intelligent mode
    # Per-team scoring weights (None = module default). Lets each side play a
    # different strategy, e.g. one turtling on defense while the other attacks.
    defense_a: float = None
    concentration_a: float = None
    defense_b: float = None
    concentration_b: float = None


def _apply_profiles(team_a: list, team_b: list, cfg: SimConfig) -> None:
    """In intelligent mode each frame plays against the opposing team's profile,
    using its own team's strategy weights."""
    prof_a = deck_profile([c for f in team_a for c in f.pile.deck])
    prof_b = deck_profile([c for f in team_b for c in f.pile.deck])
    for f in team_a:
        f.opp_profile = prof_b
        f.strategy = (cfg.defense_a, cfg.concentration_a)
    for f in team_b:
        f.opp_profile = prof_a
        f.strategy = (cfg.defense_b, cfg.concentration_b)


def _collect_stats(team_a: list, target_a: Frame, team_b: list, target_b: Frame,
                   cfg: SimConfig, rng: random.Random) -> dict:
    """Play cfg.games between two already-built teams and aggregate the results.

    Returns a stats dict: win counts, win rates, avg rounds-to-kill, target names."""
    wins = {"A": 0, "B": 0, "draw": 0}
    rounds_on_win = {"A": [], "B": []}
    for _ in range(cfg.games):
        winner, rnds = play_game(team_a, team_b, target_a, target_b,
                                 cfg.hand, cfg.health, cfg.max_rounds, rng,
                                 cfg.intelligent, cfg.pool)
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


def run_matchup(deck_a: list, deck_b: list, cards: dict, cfg: SimConfig,
                rng: random.Random) -> dict:
    """Play cfg.games between team A (deck_a paths) and team B (deck_b paths).

    Returns a stats dict: win counts, win rates, avg rounds-to-kill, target names."""
    team_a, target_a = build_team(deck_a, "A", cards, cfg.target_a, rng)
    team_b, target_b = build_team(deck_b, "B", cards, cfg.target_b, rng)
    if cfg.intelligent:
        _apply_profiles(team_a, team_b, cfg)
    return _collect_stats(team_a, target_a, team_b, target_b, cfg, rng)


# --------------------------------------------------------------------------- #
# Sub-command: match  (explicit teams, the original behaviour)
# --------------------------------------------------------------------------- #

def _team_weights(args) -> tuple:
    """(defense_a, conc_a, defense_b, conc_b): per-team overrides fall back to the
    global --defense / --concentration, which fall back to the module defaults."""
    g_def, g_conc = args.defense, args.concentration
    return (
        getattr(args, "defense_a", None) if getattr(args, "defense_a", None) is not None else g_def,
        getattr(args, "concentration_a", None) if getattr(args, "concentration_a", None) is not None else g_conc,
        getattr(args, "defense_b", None) if getattr(args, "defense_b", None) is not None else g_def,
        getattr(args, "concentration_b", None) if getattr(args, "concentration_b", None) is not None else g_conc,
    )


def _mode_label(cfg: SimConfig) -> str:
    """A compact ' [intelligent ...]' banner showing pool and effective weights."""
    def w(d, c):
        d = DEFENSE_WEIGHT if d is None else d
        c = CONCENTRATION_WEIGHT if c is None else c
        return f"def={d:g},conc={c:g}"
    a, b = w(cfg.defense_a, cfg.concentration_a), w(cfg.defense_b, cfg.concentration_b)
    inner = a if a == b else f"A({a}) B({b})"
    return f" [intelligent pool={cfg.pool} {inner}]"


def cmd_match(args) -> None:
    rng = random.Random(args.seed)
    cards = load_cards()
    da, ca, db, cb = _team_weights(args)
    cfg = SimConfig(args.games, args.hand, args.health, args.max_rounds,
                    args.target_a, args.target_b,
                    intelligent=args.intelligent, pool=args.pool,
                    defense_a=da, concentration_a=ca, defense_b=db, concentration_b=cb)
    stats = run_matchup(args.team_a, args.team_b, cards, cfg, rng)

    print("=" * 64)
    print("mobileSuitGame balance simulation"
          + (_mode_label(cfg) if cfg.intelligent else ""))
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
    da, ca, db, cb = _team_weights(args)
    cfg = SimConfig(args.games, args.hand, args.health, args.max_rounds,
                    intelligent=args.intelligent, pool=args.pool,
                    defense_a=da, concentration_a=ca, defense_b=db, concentration_b=cb)
    a, b = args.deck_a, args.deck_b

    print("=" * 72)
    print("mobileSuitGame team-size scaling"
          + (_mode_label(cfg) if cfg.intelligent else ""))
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
    # Each cell is row-deck (team A) vs column-deck (team B), so the A/B strategy
    # weights apply to rows vs columns respectively. Give them different weights
    # (e.g. --defense-a 0 --defense-b 4) to read attackers-vs-defenders off the grid.
    da, ca, db, cb = _team_weights(args)
    cfg = SimConfig(args.games, args.hand, args.health, args.max_rounds,
                    intelligent=args.intelligent, pool=args.pool,
                    defense_a=da, concentration_a=ca, defense_b=db, concentration_b=cb)

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

    # Text ranking per size by mean head-to-head win-ratio (draws excluded).
    for n in args.sizes:
        grid = grids[n]
        print(f"\nRanking at {n}v{n} (mean win-ratio vs all opponents, draws excluded):")
        scored = sorted(
            ((sum(_win_ratio(grid[i][j]) for j in range(d)) / d, names[i])
             for i in range(d)), reverse=True)
        for rate, name in scored:
            print(f"  {rate:5.1f}%  {name}")


# --------------------------------------------------------------------------- #
# Sub-command: cards  (every single card as a 1-card deck vs the chosen decks)
# --------------------------------------------------------------------------- #

def _card_label(card: Card) -> str:
    return f"{card.group}/{card.name}"


def _run_decks(deck_a: list, deck_b: list, cfg: SimConfig, rng: random.Random,
               name_a: str = "A", name_b: str = "B") -> dict:
    """Play cfg.games between two in-memory decks as a 1v1 (each a single frame).
    `deck_a` / `deck_b` are lists of Card. Returns the usual stats dict."""
    frame_a = Frame(name=name_a, team="A", pile=Pile(list(deck_a), rng), is_target=True)
    frame_b = Frame(name=name_b, team="B", pile=Pile(list(deck_b), rng), is_target=True)
    team_a, team_b = [frame_a], [frame_b]
    if cfg.intelligent:
        _apply_profiles(team_a, team_b, cfg)
    return _collect_stats(team_a, frame_a, team_b, frame_b, cfg, rng)


def cmd_cards(args) -> None:
    """Run every defined card, each as a deck of just that one card, against each
    of the chosen decks, and report the card's win rate as a table."""
    rng = random.Random(args.seed)
    cards = load_cards()
    da, ca, db, cb = _team_weights(args)
    cfg = SimConfig(args.games, args.hand, args.health, args.max_rounds,
                    intelligent=args.intelligent, pool=args.pool,
                    defense_a=da, concentration_a=ca, defense_b=db, concentration_b=cb)

    deck_paths = list(args.decks)
    if args.decks_dir:
        deck_paths += sorted(str(p) for p in Path(args.decks_dir).glob("*.csv"))
    if not deck_paths:
        raise SystemExit("cards needs at least one opponent deck "
                         "(via --decks and/or --decks-dir)")
    deck_names = [Path(p).stem for p in deck_paths]
    opp_decks = [load_deck(p, cards) for p in deck_paths]

    card_list = sorted(cards.values(), key=lambda c: (c.group, c.name))
    if args.attackers_only:
        card_list = [c for c in card_list if c.is_attack]

    total = len(card_list) * len(deck_paths) * cfg.games
    print(f"Card sweep: {len(card_list)} cards x {len(deck_paths)} decks, "
          f"{cfg.games} games/cell ({total} games) ...")

    # rows[i] = (card, [win% vs each deck], mean win%). Each card is a 1v1 game as
    # team A (a deck of just that card) against the chosen deck as team B.
    rows = []
    for c in card_list:
        rates = []
        for opp in opp_decks:
            st = _run_decks([c], opp, cfg, rng, name_a=c.name, name_b="opp")
            rates.append(st["rate"]["A"])
        rows.append((c, rates, sum(rates) / len(rates)))
    rows.sort(key=lambda r: r[2], reverse=True)

    _print_card_table(rows, deck_names, cfg)

    html = render_card_sweep_html(rows, deck_names, cfg)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"\nWrote visual report -> {out}")


def _print_card_table(rows: list, deck_names: list, cfg: SimConfig) -> None:
    """Text table: one row per card, one column per chosen deck (win% of the
    single-card deck), plus a mean column. Rows are sorted strongest-first."""
    label_w = max([len("Card")] + [len(_card_label(c)) for c, _, _ in rows])
    col_w = 8
    header = (f"  {'Card':<{label_w}}"
              + "".join(f"{nm[:col_w - 1]:>{col_w}}" for nm in deck_names)
              + f"{'mean':>{col_w}}")
    print("=" * len(header))
    print("mobileSuitGame card sweep — win% of a deck of just this card"
          + (_mode_label(cfg) if cfg.intelligent else ""))
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    for c, rates, mean in rows:
        cells = "".join(f"{r:>{col_w}.1f}" for r in rates)
        print(f"  {_card_label(c):<{label_w}}{cells}{mean:>{col_w}.1f}")
    print("=" * len(header))


def render_card_sweep_html(rows: list, deck_names: list, cfg: SimConfig) -> str:
    """Self-contained HTML heatmap: rows are cards, columns are the chosen decks,
    each cell the single-card deck's win rate (blue = the card wins), plus a mean
    column. Every cell shows its number so meaning never rides on colour alone."""
    head_cells = "".join(f"<th class='col'><div>{escape(nm)}</div></th>"
                         for nm in deck_names)
    body_rows = []
    for c, rates, mean in rows:
        cells = []
        for nm, r in zip(deck_names, rates):
            rgb = _diverging(r)
            tip = f"{_card_label(c)} (init {c.initiative}) vs {nm}: {r:.1f}% win"
            cells.append(f"<td class='cell' style='background:rgb{rgb};"
                         f"color:{_ink_on(rgb)}' title='{escape(tip)}'>{r:.0f}</td>")
        mrgb = _diverging(mean)
        cells.append(f"<td class='cell avg' style='background:rgb{mrgb};"
                     f"color:{_ink_on(mrgb)}'>{mean:.0f}</td>")
        body_rows.append(f"<tr><th class='row'>{escape(_card_label(c))}</th>"
                         f"{''.join(cells)}</tr>")

    def _w(d, c):
        d = DEFENSE_WEIGHT if d is None else d
        c = CONCENTRATION_WEIGHT if c is None else c
        return f"def={d:g}, conc={c:g}"
    if not cfg.intelligent:
        play = "random play"
    else:
        wa, wb = _w(cfg.defense_a, cfg.concentration_a), _w(cfg.defense_b, cfg.concentration_b)
        play = (f"intelligent play ({wa}), pool {cfg.pool}" if wa == wb
                else f"intelligent play, pool {cfg.pool} &middot; "
                     f"cards played as A [{wa}] &middot; decks played as B [{wb}]")
    subtitle = (f"{len(rows)} cards &middot; {len(deck_names)} decks "
                f"&middot; {cfg.games} games/cell &middot; hand {cfg.hand} "
                f"&middot; {cfg.health} HP/zone &middot; {play}")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>mobileSuitGame — card sweep</title>
<style>
  :root {{ --surface:#fcfcfb; --ink:#0b0b0b; --muted:#52514e; --line:#e7e6e2; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --surface:#1a1a19; --ink:#ffffff; --muted:#c3c2b7; --line:#33322f; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:2rem 1.5rem 3rem; background:var(--surface); color:var(--ink);
         font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
  h1 {{ font-size:1.35rem; margin:0 0 .25rem; }}
  .sub {{ color:var(--muted); margin:0 0 .5rem; font-size:.9rem; }}
  .scroll {{ overflow-x:auto; }}
  table {{ border-collapse:separate; border-spacing:2px; }}
  th, td {{ padding:0; }}
  thead th.col {{ position:sticky; top:0; }}
  th.col div {{ writing-mode:vertical-rl; transform:rotate(180deg); white-space:nowrap;
               padding:.4rem .1rem; color:var(--muted); font-weight:600; font-size:.8rem; }}
  th.row {{ text-align:right; padding-right:.6rem; color:var(--muted); font-weight:600;
           white-space:nowrap; font-size:.85rem; }}
  td.cell {{ min-width:44px; height:30px; padding:0 4px; text-align:center;
            vertical-align:middle; font-variant-numeric:tabular-nums; font-weight:600;
            border-radius:4px; font-size:.78rem; white-space:nowrap; }}
  td.cell.avg {{ font-weight:800; }}
  .legend {{ display:flex; align-items:center; gap:.6rem; margin:1.25rem 0 .75rem; font-size:.82rem;
            color:var(--muted); flex-wrap:wrap; }}
  .bar {{ width:220px; height:12px; border-radius:6px;
         background:linear-gradient(90deg, rgb(208,59,59), rgb(240,239,236), rgb(42,120,214)); }}
</style></head>
<body>
  <h1>mobileSuitGame — card sweep</h1>
  <p class="sub">{subtitle}</p>
  <div class="legend">
    <span>card loses</span>
    <span class="bar"></span>
    <span>card wins</span>
    <span style="margin-left:.5rem">(win% of a deck of just that card, sorted by mean)</span>
  </div>
  <div class="scroll"><table>
    <thead><tr><th class="corner"></th>{head_cells}
      <th class="col"><div>mean</div></th></tr></thead>
    <tbody>{''.join(body_rows)}</tbody>
  </table></div>
</body></html>
"""


# --------------------------------------------------------------------------- #
# Sub-command: pool  (marginal value of adding each card to a base deck)
# --------------------------------------------------------------------------- #

def cmd_pool(args) -> None:
    """For every card, measure how a base deck does WITH that card added versus
    WITHOUT it, against each of the chosen opponent decks. The per-card delta is
    that card's marginal contribution to the pool."""
    rng = random.Random(args.seed)
    cards = load_cards()
    da, ca, db, cb = _team_weights(args)
    cfg = SimConfig(args.games, args.hand, args.health, args.max_rounds,
                    intelligent=args.intelligent, pool=args.pool,
                    defense_a=da, concentration_a=ca, defense_b=db, concentration_b=cb)

    deck_paths = list(args.decks)
    if args.decks_dir:
        deck_paths += sorted(str(p) for p in Path(args.decks_dir).glob("*.csv"))
    if not deck_paths:
        raise SystemExit("pool needs at least one opponent deck "
                         "(via --decks and/or --decks-dir)")
    deck_names = [Path(p).stem for p in deck_paths]
    opp_decks = [load_deck(p, cards) for p in deck_paths]
    base_deck = load_deck(args.base, cards)
    base_name = Path(args.base).stem

    card_list = sorted(cards.values(), key=lambda c: (c.group, c.name))
    if args.attackers_only:
        card_list = [c for c in card_list if c.is_attack]

    total = (len(card_list) + 1) * len(deck_paths) * cfg.games
    print(f"Pool test: base '{base_name}' + each of {len(card_list)} cards "
          f"x {len(deck_paths)} decks, {cfg.games} games/cell ({total} games) ...")

    # Baseline: the base deck (no addition) vs each opponent. Computed once.
    baseline = [_run_decks(base_deck, opp, cfg, rng, name_a=base_name)["rate"]["A"]
                for opp in opp_decks]

    # rows[i] = (card, [with-card win% per deck], [delta per deck], mean delta).
    rows = []
    for c in card_list:
        augmented = base_deck + [c]
        withs, deltas = [], []
        for opp, base_rate in zip(opp_decks, baseline):
            wr = _run_decks(augmented, opp, cfg, rng, name_a=base_name)["rate"]["A"]
            withs.append(wr)
            deltas.append(wr - base_rate)
        rows.append((c, withs, deltas, sum(deltas) / len(deltas)))
    rows.sort(key=lambda r: r[3], reverse=True)

    _print_pool_table(base_name, baseline, rows, deck_names, cfg)

    html = render_pool_html(base_name, baseline, rows, deck_names, cfg)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"\nWrote visual report -> {out}")


def _print_pool_table(base_name: str, baseline: list, rows: list,
                      deck_names: list, cfg: SimConfig) -> None:
    """Text table: one row per card showing the win% change (with the card minus
    without) it brings to the base deck against each opponent, plus a mean. A
    leading 'base' line gives the un-augmented win rates for reference."""
    base_label = f"(base) {base_name}"
    label_w = max([len("Card"), len(base_label)]
                  + [len(_card_label(c)) for c, _, _, _ in rows])
    col_w = 8
    header = (f"  {'Card (Δ = with − without)':<{label_w}}"
              + "".join(f"{nm[:col_w - 1]:>{col_w}}" for nm in deck_names)
              + f"{'mean':>{col_w}}")
    print("=" * len(header))
    print("mobileSuitGame pool test — win% change from adding each card to the base deck"
          + (_mode_label(cfg) if cfg.intelligent else ""))
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    base_cells = "".join(f"{r:>{col_w}.1f}" for r in baseline)
    base_mean = sum(baseline) / len(baseline)
    print(f"  {base_label:<{label_w}}{base_cells}{base_mean:>{col_w}.1f}")
    print("-" * len(header))
    for c, _withs, deltas, md in rows:
        cells = "".join(f"{d:>+{col_w}.1f}" for d in deltas)
        print(f"  {_card_label(c):<{label_w}}{cells}{md:>+{col_w}.1f}")
    print("=" * len(header))


def render_pool_html(base_name: str, baseline: list, rows: list,
                     deck_names: list, cfg: SimConfig) -> str:
    """Self-contained HTML heatmap: rows are cards, columns the chosen decks, each
    cell the win% change (blue = the card helps the base deck, red = it hurts) plus
    a mean column. A muted top row shows the base deck's own win rates."""
    span = max([5.0] + [abs(d) for _c, _w, ds, _m in rows for d in ds]
               + [abs(m) for _c, _w, _ds, m in rows])

    head_cells = "".join(f"<th class='col'><div>{escape(nm)}</div></th>"
                         for nm in deck_names)

    base_mean = sum(baseline) / len(baseline)
    base_cells = "".join(f"<td class='cell base'>{r:.0f}</td>" for r in baseline)
    base_cells += f"<td class='cell base'>{base_mean:.0f}</td>"
    base_row = (f"<tr><th class='row'>(base) {escape(base_name)}</th>{base_cells}</tr>")

    body_rows = [base_row]
    for c, withs, deltas, md in rows:
        cells = []
        for nm, wr, d in zip(deck_names, withs, deltas):
            rgb = _diverging_signed(d, span)
            tip = (f"{_card_label(c)} added to {base_name} vs {nm}: "
                   f"{wr:.1f}% win ({d:+.1f} vs base)")
            cells.append(f"<td class='cell' style='background:rgb{rgb};"
                         f"color:{_ink_on(rgb)}' title='{escape(tip)}'>{d:+.0f}</td>")
        mrgb = _diverging_signed(md, span)
        cells.append(f"<td class='cell avg' style='background:rgb{mrgb};"
                     f"color:{_ink_on(mrgb)}'>{md:+.0f}</td>")
        body_rows.append(f"<tr><th class='row'>{escape(_card_label(c))}</th>"
                         f"{''.join(cells)}</tr>")

    play = "random play" if not cfg.intelligent else f"intelligent play, pool {cfg.pool}"
    subtitle = (f"base deck: {escape(base_name)} &middot; {len(rows)} candidate cards "
                f"&middot; {len(deck_names)} decks &middot; {cfg.games} games/cell "
                f"&middot; hand {cfg.hand} &middot; {cfg.health} HP/zone &middot; {play}")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>mobileSuitGame — pool test</title>
<style>
  :root {{ --surface:#fcfcfb; --ink:#0b0b0b; --muted:#52514e; --line:#e7e6e2; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --surface:#1a1a19; --ink:#ffffff; --muted:#c3c2b7; --line:#33322f; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:2rem 1.5rem 3rem; background:var(--surface); color:var(--ink);
         font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
  h1 {{ font-size:1.35rem; margin:0 0 .25rem; }}
  .sub {{ color:var(--muted); margin:0 0 .5rem; font-size:.9rem; }}
  .scroll {{ overflow-x:auto; }}
  table {{ border-collapse:separate; border-spacing:2px; }}
  th, td {{ padding:0; }}
  thead th.col {{ position:sticky; top:0; }}
  th.col div {{ writing-mode:vertical-rl; transform:rotate(180deg); white-space:nowrap;
               padding:.4rem .1rem; color:var(--muted); font-weight:600; font-size:.8rem; }}
  th.row {{ text-align:right; padding-right:.6rem; color:var(--muted); font-weight:600;
           white-space:nowrap; font-size:.85rem; }}
  td.cell {{ min-width:44px; height:30px; padding:0 4px; text-align:center;
            vertical-align:middle; font-variant-numeric:tabular-nums; font-weight:600;
            border-radius:4px; font-size:.78rem; white-space:nowrap; }}
  td.cell.avg {{ font-weight:800; }}
  td.cell.base {{ background:var(--line); color:var(--muted); font-weight:700; }}
  .legend {{ display:flex; align-items:center; gap:.6rem; margin:1.25rem 0 .75rem; font-size:.82rem;
            color:var(--muted); flex-wrap:wrap; }}
  .bar {{ width:220px; height:12px; border-radius:6px;
         background:linear-gradient(90deg, rgb(208,59,59), rgb(240,239,236), rgb(42,120,214)); }}
</style></head>
<body>
  <h1>mobileSuitGame — pool test</h1>
  <p class="sub">{subtitle}</p>
  <div class="legend">
    <span>−{span:.0f}% (card hurts the deck)</span>
    <span class="bar"></span>
    <span>+{span:.0f}% (card helps the deck)</span>
    <span style="margin-left:.5rem">(win% change vs the base deck, sorted by mean)</span>
  </div>
  <div class="scroll"><table>
    <thead><tr><th class="corner"></th>{head_cells}
      <th class="col"><div>mean</div></th></tr></thead>
    <tbody>{''.join(body_rows)}</tbody>
  </table></div>
</body></html>
"""


def _diverging(pct: float) -> tuple:
    """Diverging blue<->red fill for a win%: 50% neutral gray, 100% blue (row
    deck winning), 0% red (row deck losing). Returns (r, g, b)."""
    red, gray, blue = (0xD0, 0x3B, 0x3B), (0xF0, 0xEF, 0xEC), (0x2A, 0x78, 0xD6)
    if pct >= 50:
        t, lo, hi = (pct - 50) / 50, gray, blue
    else:
        t, lo, hi = (50 - pct) / 50, gray, red
    return tuple(round(lo[k] + (hi[k] - lo[k]) * t) for k in range(3))


def _diverging_signed(value: float, span: float) -> tuple:
    """Diverging fill centred at 0: +span blue (helps), -span red (hurts), 0 gray.
    `span` sets the saturation scale so the biggest swing reads as full colour."""
    red, gray, blue = (0xD0, 0x3B, 0x3B), (0xF0, 0xEF, 0xEC), (0x2A, 0x78, 0xD6)
    t = max(-1.0, min(1.0, value / span)) if span else 0.0
    lo, hi, tt = (gray, blue, t) if t >= 0 else (gray, red, -t)
    return tuple(round(lo[k] + (hi[k] - lo[k]) * tt) for k in range(3))


def _ink_on(rgb: tuple) -> str:
    """Pick black or white text for contrast against a background rgb."""
    r, g, b = (c / 255 for c in rgb)
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#0b0b0b" if lum > 0.6 else "#ffffff"


def _win_ratio(st: dict) -> float:
    """Head-to-head ratio row-wins / (row-wins + col-wins), as a 0-100 percent,
    ignoring draws. 50 when the two are equal OR every game drew."""
    wa, wb = st["wins"]["A"], st["wins"]["B"]
    return 100.0 * wa / (wa + wb) if (wa + wb) else 50.0


def _render_heatmap(names: list, grid: list, n: int) -> str:
    """One <section> with the heatmap for team size n. Each cell shows the row
    deck's win count over the column deck's; colour is the win ratio (draws
    excluded) so a decisive 50/50 and an all-draws cell read differently."""
    d = len(names)
    row_avg = [sum(_win_ratio(grid[i][j]) for j in range(d)) / d for i in range(d)]

    head_cells = "".join(f"<th class='col'><div>{escape(nm)}</div></th>" for nm in names)
    body_rows = []
    for i, nm in enumerate(names):
        cells = []
        for j in range(d):
            st = grid[i][j]
            wa, wb, dr = st["wins"]["A"], st["wins"]["B"], st["wins"]["draw"]
            rgb = _diverging(_win_ratio(st))
            ar = st["avg_rounds"]["A"]
            tip = (f"{names[i]} (A) vs {names[j]} (B) @ {n}v{n}\n"
                   f"A wins {wa} ({st['rate']['A']:.1f}%)  |  B wins {wb} "
                   f"({st['rate']['B']:.1f}%)  |  draws {dr} ({st['rate']['draw']:.1f}%)"
                   + (f"\navg rounds to kill: {ar:.1f}" if ar is not None else ""))
            diag = " diag" if i == j else ""
            cells.append(f"<td class='cell{diag}' style='background:rgb{rgb};"
                         f"color:{_ink_on(rgb)}' title='{escape(tip)}'>"
                         f"<b>{wa}</b><span class='sl'>/</span>{wb}</td>")
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

    def _w(d, c):
        d = DEFENSE_WEIGHT if d is None else d
        c = CONCENTRATION_WEIGHT if c is None else c
        return f"def={d:g}, conc={c:g}"
    if not cfg.intelligent:
        play = "random play"
        asym_note = ""
    else:
        wa, wb = _w(cfg.defense_a, cfg.concentration_a), _w(cfg.defense_b, cfg.concentration_b)
        if wa == wb:
            play = f"intelligent play ({wa}), pool {cfg.pool}"
            asym_note = ""
        else:
            play = (f"intelligent play, pool {cfg.pool} &middot; "
                    f"rows played as A [{wa}] &middot; columns played as B [{wb}]")
            asym_note = (" Because the two sides use different strategies, the grid is "
                         "<strong>not symmetric</strong> — cell (i,j) and (j,i) are different "
                         "matchups, so both triangles are informative.")
    subtitle = (f"{len(names)} decks &middot; sizes {', '.join(f'{n}v{n}' for n in sorted(grids))} "
                f"&middot; {cfg.games} games/cell &middot; hand {cfg.hand} "
                f"&middot; {cfg.health} HP/zone &middot; {play}")
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
  td.cell {{ min-width:58px; height:38px; padding:0 4px; text-align:center;
            vertical-align:middle; font-variant-numeric:tabular-nums; font-weight:500;
            border-radius:4px; font-size:.78rem; white-space:nowrap; }}
  td.cell b {{ font-weight:800; }}
  td.cell .sl {{ opacity:.5; margin:0 1px; font-weight:400; }}
  td.cell.diag {{ outline:2px solid var(--surface); outline-offset:-2px; opacity:.85; }}
  td.cell.avg {{ font-weight:800; min-width:44px; }}
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
    <span style="margin-left:.5rem">(head-to-head ratio, draws excluded)</span>
  </div>
  <p class="note">Each cell shows <strong><b>row&nbsp;wins</b> / column&nbsp;wins</strong>
  — the row deck (as team&nbsp;A) against the column deck (as team&nbsp;B). The colour
  is the ratio between those two, <em>ignoring draws</em>, so a genuine 50/50 fight
  (gray) is distinct from a cell that mostly drew (the two counts are both small).
  The diagonal is a deck against itself; <em>row&nbsp;avg</em> is that deck's mean
  win-ratio across all opponents — a rough power ranking. Hover any cell for the
  exact win/draw split.{asym_note}</p>
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
        p.add_argument("--intelligent", action="store_true",
                       help="pick the best actions vs the opponent's deck profile "
                            "instead of playing random cards")
        p.add_argument("--pool", type=int, default=5,
                       help="cards seen to choose actions from in intelligent mode "
                            "(default 5; capped at deck size, so a big value = the "
                            "whole deck every turn)")
        p.add_argument("--defense", type=float, default=None,
                       help="intelligent defense weight for BOTH teams "
                            f"(default {DEFENSE_WEIGHT})")
        p.add_argument("--concentration", type=float, default=None,
                       help="intelligent concentration weight for BOTH teams "
                            f"(default {CONCENTRATION_WEIGHT})")

    def add_per_team_strategy(p):
        # Per-team overrides so each side can play a different strategy.
        p.add_argument("--defense-a", type=float, default=None,
                       help="override defense weight for team A only")
        p.add_argument("--defense-b", type=float, default=None,
                       help="override defense weight for team B only")
        p.add_argument("--concentration-a", type=float, default=None,
                       help="override concentration weight for team A only")
        p.add_argument("--concentration-b", type=float, default=None,
                       help="override concentration weight for team B only")

    m = sub.add_parser("match", help="one explicit matchup (team A vs team B)")
    m.add_argument("--team-a", nargs="+", required=True, help="deck CSV(s) for team A")
    m.add_argument("--team-b", nargs="+", required=True, help="deck CSV(s) for team B")
    m.add_argument("--target-a", type=int, default=0,
                   help="index of team A's focus-fired frame (default 0)")
    m.add_argument("--target-b", type=int, default=0,
                   help="index of team B's focus-fired frame (default 0)")
    add_common(m)
    add_per_team_strategy(m)
    m.set_defaults(func=cmd_match)

    s = sub.add_parser("scale", help="same deck-pair at 1v1, 2v2, 3v3 ...")
    s.add_argument("--deck-a", required=True, help="deck CSV for every frame on team A")
    s.add_argument("--deck-b", required=True, help="deck CSV for every frame on team B")
    s.add_argument("--sizes", type=int, nargs="+", default=[1, 2, 3],
                   help="team sizes to test (default 1 2 3)")
    add_common(s)
    add_per_team_strategy(s)
    s.set_defaults(func=cmd_scale)

    t = sub.add_parser("tournament", help="round-robin over a deck set -> HTML report")
    t.add_argument("--decks", nargs="*", default=[], help="deck CSVs to include")
    t.add_argument("--decks-dir", help="also include every *.csv in this folder")
    t.add_argument("--sizes", type=int, nargs="+", default=[1, 2, 3],
                   help="team sizes, one heatmap each (default 1 2 3)")
    t.add_argument("--output", default="build/tournament.html", help="HTML report path")
    add_common(t, default_games=250)
    add_per_team_strategy(t)  # rows play the A-strategy, columns the B-strategy
    t.set_defaults(func=cmd_tournament)

    c = sub.add_parser("cards",
                       help="every card as a deck of just that card vs the chosen decks -> table")
    c.add_argument("--decks", nargs="*", default=[],
                   help="opponent deck CSV(s) to test every card against")
    c.add_argument("--decks-dir",
                   help="also include every *.csv in this folder as an opponent deck")
    c.add_argument("--attackers-only", action="store_true",
                   help="skip cards with no attack (a block-only deck can never win)")
    c.add_argument("--output", default="build/card_sweep.html", help="HTML report path")
    add_common(c, default_games=300)  # cards x decks x games gets big; keep games modest
    add_per_team_strategy(c)  # cards play the A-strategy, decks the B-strategy
    c.set_defaults(func=cmd_cards)

    p = sub.add_parser("pool",
                       help="marginal win% each card adds to a base deck vs the chosen decks -> table")
    p.add_argument("--base", required=True,
                   help="base deck CSV; every card is tried added to this pool")
    p.add_argument("--decks", nargs="*", default=[],
                   help="opponent deck CSV(s) to test the base deck against")
    p.add_argument("--decks-dir",
                   help="also include every *.csv in this folder as an opponent deck")
    p.add_argument("--attackers-only", action="store_true",
                   help="only try adding cards that have an attack")
    p.add_argument("--output", default="build/card_pool.html", help="HTML report path")
    add_common(p, default_games=300)  # cards x decks x games gets big; keep games modest
    add_per_team_strategy(p)  # base deck plays the A-strategy, opponents the B-strategy
    p.set_defaults(func=cmd_pool)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
