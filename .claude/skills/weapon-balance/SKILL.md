---
name: weapon-balance
description: Find action cards that are strictly better than other cards for the same printed effect, within a weapon group or across groups, and read each gap against the two groups' overall profiles. Use when asked to check card balance, find dominated or redundant cards, compare weapon groups, or audit initiative/movement costs after editing a card CSV.
---

# Weapon balance audit

`weapon_dominance.py` does the mechanical part. Your job is the judgement layer:
deciding which of the reported gaps are actually problems.

## Run it

```bash
python weapon_dominance.py                       # all three sections
python weapon_dominance.py --cross               # cross-group dominance only
python weapon_dominance.py --within              # within-group cost spread only
python weapon_dominance.py --profiles            # group profile table only
python weapon_dominance.py --group Mace --group Halberd
python weapon_dominance.py --csv "Drone actions.csv"
python weapon_dominance.py --markdown            # tables, for pasting into a report
```

Defaults to `Weapon actions.csv`, skipping `PrintID=0` rows.
Also accepts `Basic actions.csv`, `Booster actions.csv` and `Drone actions.csv` —
anything with the `High/Mid/Low Attack+Block+DType+Range` column set. It exits with an
error on a CSV without those columns (`Pilot actions.csv`). For drone
cards it additionally ranks `Drone_Health` and `Drone_MV` (higher is better).

## What it checks

**within** — per group, sets of cards with the same effect but different cost.
Same effect = identical text and persistence plus the same attack and block
values. Cost = initiative and movement. Marks any member strictly beaten by
another (>= on both, > on one).

**cross** — every pair from *different* groups where one dominates: >= on
attack, block, range, initiative and movement, with at least one strict.

Pairs are **clustered**, not listed one by one. Cards connected by dominance
form one block, so a card beaten by three others is one entry listing all three
rather than three findings — they are the same interaction. Each card's stats
and each group's profile print once per cluster. Clusters are sorted widest gap
first; dominated cards are marked and get a `loses to` line naming every card
above them and by how much.

**profiles** — per group: attack and block zone coverage, range span, initiative
spread and average, movement spread, average attack and block points per card,
damage types.

## Rules the script encodes

- **Higher initiative is better.** Cards resolve highest first (`rules/rules.tex`),
  and a resolved attack stays out to block.
- **Higher movement is better.** The value is a modifier, so `-2` beats `-3`.
- **Only identical text is comparable.** Text can be a cost (`\fullreload`) as
  readily as a bonus, so the script will not rank two different abilities against
  each other. Text is whitespace-normalised and `\\` is stripped before
  comparison — a stray double space used to hide real duplicates.
- **Zone placement is never a difference.** Attack and block values are compared
  as sorted shapes, so High 2 / Low 1 and Mid 2 / High 1 are the same card.
  Which zone a card hits is a matchup difference, not a power difference.
- **Damage type never gates a comparison.** It is printed on every pair so you
  can see it, but a cross-type domination counts exactly as much as a same-type
  one. Type steers which frame abilities apply (Adam: `pierce` +2 init;
  Elemiah: `impact` gains Knockback 1; Kamikiri: extra `cut` mid) — it is not a
  balance lever.

## Reading the output

A reported cluster is **not automatically a bug**. A card can be strictly better
one-to-one and still be fair, if its group pays for it elsewhere. That is what
the group profile lines are for: initiative average, attack and block per card,
zone coverage, range span. A 2-point initiative gap between a group averaging
6.8 and one averaging 3.0 is roughly the group delta; the same gap between two
groups both averaging 4.5 is not.

## Reporting it back

Relay the clusters as the script gives them. Keep, per cluster: every card's
exact stats (initiative, movement, per-zone attack with damage type and range,
per-zone block), the `loses to` lines, and each group's profile row.

Do **not** expand a cluster back into one entry per pair, and do not write a
paragraph of commentary under each one — the numbers and the group rows are the
finding. At most, close with a short note on which clusters have near-identical
group profiles, since those are the ones with nothing absorbing the gap.
