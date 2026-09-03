---
name: weapon-power
description: Assess how strong each weapon GROUP is and, above all, find which weapons are weak and why, using the combat simulator. Runs a suite of four analyses (feature estimate + summary stats, offence-vs-blocking isolation, marginal contribution, and pairings) and reads them together into a per-weapon verdict. Use when asked how good/bad a weapon is, why the weak weapons are weak, what to buff, or to re-check weapon power after editing card stats or the sim.
---

# Weapon power assessment

Sibling to `weapon-balance`. That skill finds cards **strictly dominated on paper**
(`weapon_dominance.py`, a static read of the CSV). This skill measures **how much a
weapon group actually wins** by playing it, using the `simulation/` combat model — so
it catches weakness that the stat sheet hides (a weapon whose damage never lands, a
weapon that only helps in company) and weakness the stat sheet over-states (big damage
aimed at over-blocked zones). The headline use is **finding what is weak and by how
much, so it can be brought up.**

All four scripts read the live card data and re-derive weapon groups every run, so they
stay honest as cards change. Run them from the repo root.

## The model they share (what "power" means here)

The sim (`simulation/simulate.py`) resolves a focus-fire fight to destruction: HP 4 per
zone, hand 2, `--intelligent --pool 7` play, attacks resolve **highest initiative
first**, and blocking is mandatory when a zone lines up. Three consequences drive almost
every weak-weapon verdict:

- **Landing.** A low-initiative attack resolves last, so the opponent blocks it with an
  *already-resolved* (free) card. Low init + no way to force a block = damage that never
  lands, however big the number.
- **Conversion.** Damage must exceed 4 in one zone to kill. A weapon that only ever
  deals 1s can't concentrate a kill before the game ends — it chips.
- **Coverage.** A weapon that blocks only one zone (many ranged weapons block only Mid)
  forces its partner to defend the other two, which is why it drags a deck down even
  when its own attacks are fine.

Two rules are modelled that the CSV does **not** show, and both matter for these numbers:
**reload** (an attack with `reload` in its text duds that weapon group's *next* attack —
no damage, no block spent — until an action is spent reloading; the trigger persists
across rounds) and the **pilot implicit High block** (every pilot card blocks High by
rule). Ignoring reload over-states Plasma Rifle and especially Cannon.

## Run it

```bash
# 1. Feature estimate + the summary stats table (start here for the roster at a glance)
python simulation/weapon_power.py            # add --fit to refit the model to a live tournament

# 2. Separate OFFENCE from BLOCKING: drop each weapon into an all-Basics deck (every zone
#    blockable) and diff vs the pure weapon-group round-robin
python simulation/weapon_quality_test.py     # --games N for tighter numbers

# 3. Power as a CONTRIBUTOR: avg lift when added to a deck already running another weapon
#    (~a Shapley value) -- the fairest single number; reveals partners solo win% hides
python simulation/weapon_contribution.py     # --games N

# 4. Two-weapon pairings (+basics): best pairs and which weapons NEVER partner well;
#    --team-size 2 shows the shift under 2v2 focus-fire pressure
python simulation/weapon_pairs.py --team-size 2
```

Scripts 3 and 4 are the slow ones (many round-robins). They accept `--games` /
`--stage*-games` to trade runtime for precision; the defaults are tuned to finish in a
few minutes on ~16 jobs.

## What each one is for

| Script | Question it answers | Best number it gives |
|---|---|---|
| `weapon_power.py` | What does each weapon look like, and what would features predict? | `EST` win% + the stats table (atk/blk by zone, super/guard-break, init span, `dmg@fast/slow`, range%, move penalty) |
| `weapon_quality_test.py` | Is this weapon weak because of its **offence** or its **blocking**? | delta = (weapon-in-basics) − (pure group); big positive = blocking was the problem, ~zero/negative = offence is |
| `weapon_contribution.py` | What does the weapon **add** to a deck already doing other things? | `power(X)` = mean marginal contribution; the primary weakness ranking |
| `weapon_pairs.py` | Which weapons synergise, and which never make a good pair? | top pairs + an appearance count (0× among top pairs = a partner nobody wants) |

**Read them together.** `contribution` is the spine — rank weakest-first off `power(X)`.
Then explain *each* weak weapon with the others: `weapon_power`'s stats table says
whether it's slow / low-damage / mono-block; `weapon_quality_test`'s delta says whether
fixing its blocking would rescue it; `weapon_pairs`' appearance count confirms nobody
wants it as a partner. A verdict backed by all four is a real finding; one script alone
can mislead (solo win% especially — Great Axe solos ~46% but contributes −2).

## The `weapon_power.py` stats columns

`atk H/M/L` and `blk H/M/L` are summed attack/block points per zone across the group;
the two numbers after `blk` are **guard-break count** and **super-block count**; `init`
is the min–max initiative span; `dmg@fast/slow` is damage at the group's fastest and
slowest card with their inits (this is the texture that a *mean* initiative hides — a
weapon can average init 4 by being 7 and 1, which plays nothing like a flat 4); `rng%`
is the share of cards that are ranged; `move` is the movement-penalty span.

`EST` is a linear feature fit (R²≈0.5) — a rough guide, not the verdict. It is
deliberately kept alongside the sim numbers so its **misreads are visible**: it over-
rates damage into over-blocked zones (Great Axe) and under-rates guard-break that makes
ranged chip land (Plasma Rifle). Where `EST` and the sim disagree, trust the sim and the
disagreement is itself informative about what the features miss.

In general, ranged weapons will only block mid, and are lower initiative, so they perform poorly on these simulations but they have much more power on the battlefield than this would suggest - they are expected to lose and its a point of concern if they are winning at point blank to melee weapons.

## Reporting it back

Lead with a **weakest-first table**: weapon, contribution `power(X)`, solo win%, and a
one-line problem tagged with its failure mode. Then a short section per failure mode
grouping the weapons that share it, each with the concrete stat that proves it (the
`dmg@fast/slow`, the mono-Mid block, the all-1s). Close with the highest-leverage single
knob per weak weapon (+init vs +damage vs new zone) and offer to prototype one and
re-run `contribution` to measure the move — that loop (edit stat → re-run → read the
delta) is the point of the tool.

Keep it grounded in the printed numbers. Don't present `EST` as the verdict, don't rank
off solo win% alone, and state the model caveats when they bear on a specific weapon
(reload for Cannon/Plasma Rifle; that range/terrain/abilities are ignored, so a weapon
whose value is its range will read low here).
