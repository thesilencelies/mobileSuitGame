#!/usr/bin/env python3
"""Estimate each weapon group's power from its card characteristics, alongside the
summary statistics that explain it (attack/block distribution, move-penalty spread,
initiative spread). A weapon's fortunes turn on two separable things -- see
scratchpad weapon-in-basics test: how much DAMAGE it lands (magnitude, 2+ hits, and
crucially INITIATIVE, since a slow attack is blocked for free) and how many ZONES it
can BLOCK (a mono-zone/ranged blocker leaks the other two).

The power estimate is a small linear model over those features. `--fit` refits it to
the live pure-weapon-group round-robin (so it stays honest as the cards change) and
prints the fitted weights and R^2; without it, the baked weights (fitted 2026-08-07)
are used.

    python simulation/weapon_power.py            # metrics + estimate
    python simulation/weapon_power.py --fit      # also refit vs a live tournament
    python simulation/weapon_power.py --sort est # sort by estimate (default) / name / win
"""
from __future__ import annotations
import argparse, csv, sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from simulation.simulate import load_cards, weapon_groups, ZONES  # noqa: E402

# Baked linear weights (intercept + per-feature), fitted to the pure weapon-group
# round-robin on 2026-08-07 (R^2 ~0.8). Refit with --fit if the card set changes.
# The features are deliberately de-collinearised: raw damage is useless on its own
# (a big hit that resolves last is blocked for free), so offence is measured as
# damage x how-early-it-resolves ("landed offence"), and blocking as zone coverage.
BAKED = {
    "intercept": 0.1,
    "land_off": 33.2,   # init-weighted offence: mean of sum(damage) x (init/8) -- the
                        #   damage you actually LAND (a slow hit is blocked for free).
                        #   The single biggest lever by far.
    "blk_cov": 8.2,     # distinct zones the weapon can block (1-3) -- a mono-block leaks
    "gb": 5.4,          # guard-break cards -- damage that leaks past a single block
    "n_super": 6.7,     # super blocks -- free, never-spent defence
}
FEATURES = ["land_off", "blk_cov", "gb", "n_super"]


def movement_spread() -> dict:
    """min..max Movement (penalty) per weapon group, straight from the CSV."""
    mv = defaultdict(list)
    path = Path(__file__).resolve().parent.parent / "Weapon actions.csv"
    for row in csv.DictReader(open(path, encoding="utf-8-sig")):
        g = (row.get("Group") or "").strip()
        n = (row.get("Name") or "").strip()
        if not (g and n):
            continue
        try:
            mv[g].append(int((row.get("Movement") or "0").strip() or 0))
        except ValueError:
            mv[g].append(0)
    return mv


def features(cards: list) -> dict:
    nc = len(cards) or 1
    atk = {z: 0 for z in ZONES}      # total damage per zone (feints deal none)
    blk = {z: 0 for z in ZONES}      # cards blocking per zone
    n2 = n_super = gb = 0
    for c in cards:
        for i, z in enumerate(ZONES):
            a = 0 if c.feint else c.attacks[i]
            atk[z] += a
            if a >= 2:
                n2 += 1
            if z in c.blocks:
                blk[z] += 1
        n_super += len(c.super_blocks)
        gb += 1 if c.guard_break else 0
    inits = sorted(c.initiative for c in cards)
    tot = sum(atk.values())
    # Where the damage sits in the init order matters far more than the AVERAGE init:
    # damage on your fastest card lands (or forces a block before the enemy sets up);
    # damage stuck on your slowest card is blocked for free. Report the damage on the
    # fastest and slowest ATTACKING card, and the init spread.
    atkers = [c for c in cards if not c.feint and sum(c.attacks) > 0]
    fast = max(atkers, key=lambda c: c.initiative, default=None)
    slow = min(atkers, key=lambda c: c.initiative, default=None)
    fast_dmg = sum(fast.attacks) if fast else 0
    slow_dmg = sum(slow.attacks) if slow else 0
    fast_init = fast.initiative if fast else 0
    slow_init = slow.initiative if slow else 0
    init_spread = inits[-1] - inits[0]
    # Init-weighted "landed offence": each card's damage scaled by how early it
    # resolves (init/8) -- the smooth version of "damage at max vs min init". A fast
    # big hit forces/beats a block; a slow one is blocked for free, so its raw damage
    # barely counts. (Zone-weighting damage by pool scarcity was tried and *lowered*
    # fit, so damage is counted flat here.)
    land_off = sum((0 if c.feint else sum(c.attacks)) * (c.initiative / 8.0)
                   for c in cards) / nc
    return {
        "atk": atk, "blk": blk, "inits": inits, "tot_dmg": tot,
        "avg_dmg": tot / nc,
        "n2plus": n2,
        "avg_init": sum(inits) / nc,
        "init_spread": init_spread,
        "fast_dmg": fast_dmg, "fast_init": fast_init,
        "slow_dmg": slow_dmg, "slow_init": slow_init,
        "land_off": land_off,
        "blk_cov": sum(1 for z in ZONES if blk[z] > 0),
        "rng_frac": sum(1 for c in cards if c.ranged) / nc,
        "gb": gb,
        "n_super": n_super,
    }


def estimate(f: dict, weights: dict) -> float:
    return weights["intercept"] + sum(weights[k] * f[k] for k in FEATURES)


def empirical_winrates(games: int, jobs: int) -> dict:
    """Live pure-weapon-group round-robin -> mean win-ratio per weapon."""
    import random, multiprocessing as mp
    from simulation.simulate import load_deck, _run_decks, SimConfig
    WG = weapon_groups()
    specs = [f"weapon:{g}" for g in WG]
    C = load_cards()
    decks = [load_deck(s, C) for s in specs]

    def cell(t):
        i, j = t
        cfg = SimConfig(games=games, hand=2, health=4, max_rounds=20,
                        intelligent=True, pool=7)
        import random as _r
        s = _run_decks(decks[i], decks[j], cfg, _r.Random(7))
        w = s["wins"]["A"] + s["wins"]["B"]
        return (i, j, 100.0 * s["wins"]["A"] / w if w else 50.0)

    tasks = [(i, j) for i in range(len(WG)) for j in range(len(WG)) if i != j]
    # simple serial/parallel map
    if jobs and jobs > 1:
        with mp.Pool(jobs) as p:
            res = p.map(_cell_worker, [(t, games) for t in tasks])
    else:
        res = [cell(t) for t in tasks]
    M = [[50.0] * len(WG) for _ in WG]
    for i, j, wr in res:
        M[i][j] = wr
    return {WG[i]: sum(M[i][j] for j in range(len(WG)) if j != i) / (len(WG) - 1)
            for i in range(len(WG))}


_WORKER = None


def _cell_worker(arg):
    global _WORKER
    import random
    from simulation.simulate import load_deck, _run_decks, SimConfig
    (i, j), games = arg
    if _WORKER is None:
        C = load_cards()
        WG = weapon_groups()
        _WORKER = [load_deck(f"weapon:{g}", C) for g in WG]
    cfg = SimConfig(games=games, hand=2, health=4, max_rounds=20,
                    intelligent=True, pool=7)
    s = _run_decks(_WORKER[i], _WORKER[j], cfg, random.Random(7))
    w = s["wins"]["A"] + s["wins"]["B"]
    return (i, j, 100.0 * s["wins"]["A"] / w if w else 50.0)


def fit_weights(feats: dict, wins: dict) -> tuple:
    """Least-squares fit of win% ~ intercept + features. Returns (weights, R^2)."""
    import numpy as np
    names = list(feats)
    X = np.array([[1.0] + [feats[g][k] for k in FEATURES] for g in names])
    y = np.array([wins[g] for g in names])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    pred = X @ coef
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
    weights = {"intercept": coef[0], **{k: coef[i + 1] for i, k in enumerate(FEATURES)}}
    return weights, r2


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fit", action="store_true",
                    help="refit the power model to a live weapon-group round-robin")
    ap.add_argument("--games", type=int, default=200, help="games/matchup for --fit")
    ap.add_argument("--jobs", type=int, default=16, help="workers for --fit")
    ap.add_argument("--sort", choices=["est", "name", "win"], default="est")
    args = ap.parse_args()

    C = load_cards()
    G = defaultdict(list)
    for c in C.values():
        G[c.group].append(c)
    WG = weapon_groups()
    mv = movement_spread()
    feats = {g: features(G[g]) for g in WG}

    wins = None
    weights = BAKED
    if args.fit:
        print(f"Fitting to a live weapon-group round-robin ({args.games} games)...",
              file=sys.stderr)
        wins = empirical_winrates(args.games, args.jobs)
        weights, r2 = fit_weights(feats, wins)
        print(f"\nFitted weights (R^2 = {r2:.2f}):")
        print(f"  intercept {weights['intercept']:+6.1f}")
        for k in FEATURES:
            print(f"  {k:<10}{weights[k]:+6.2f}")
        print()

    est = {g: estimate(feats[g], weights) for g in WG}
    order = {"est": lambda g: -est[g], "name": lambda g: g,
             "win": lambda g: -(wins[g] if wins else est[g])}[args.sort]

    hdr = (f"{'weapon':<14}{'EST':>5}" + (f"{'win%':>6}" if wins else "")
           + f"{'atk H/M/L':>11}{'blk H/M/L':>11}{'2+':>4}{'sup':>4}"
           + f"{'init':>7}{'dmg@fast/slow':>15}{'rng%':>6}{'move':>7}")
    print(hdr)
    print("-" * len(hdr))
    for g in sorted(WG, key=order):
        f = feats[g]
        a = "/".join(str(f["atk"][z]) for z in ZONES)
        b = "/".join(str(f["blk"][z]) for z in ZONES)
        ins = f["inits"]
        win = f"{wins[g]:>6.0f}" if wins else ""
        mvs = mv[g]
        move = f"{min(mvs)}..{max(mvs)}" if mvs else "-"
        # damage on the fastest / slowest attacking card, with the init it lands at
        dfs = f"{f['fast_dmg']}@i{f['fast_init']}/{f['slow_dmg']}@i{f['slow_init']}"
        print(f"{g:<14}{est[g]:>5.0f}{win}{a:>11}{b:>11}{f['n2plus']:>4}"
              f"{f['n_super']:>4}{ins[0]:>3}-{ins[-1]:<3}{dfs:>15}{100*f['rng_frac']:>5.0f}%{move:>7}")

    print("\nEST = estimated win% from card features (intercept + weighted features; "
          "baked model R^2~0.5, so a rough guide -- the rest is matchup/nonlinear and "
          "only the sim captures it). Dominant lever: land_off (damage x how early it "
          "resolves); then block coverage & super blocks & guard break. The two it "
          "misreads are the informative ones: Great Axe (big damage, but into the "
          "over-blocked Mid + slow) reads high yet wins ~50; Plasma Rifle (guard-break "
          "makes ranged 2s land) reads low yet wins ~72. Run --fit to refit vs a live "
          "tournament and print the weights + R^2.")


if __name__ == "__main__":
    main()
