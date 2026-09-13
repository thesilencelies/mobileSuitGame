# Playtest AI: what was learned, and what is still open

Notes from the tuning work on `playtest/ai/`, written so the next session does
not re-derive it or re-run the experiments that failed. The narrative version,
with more detail on each finding, is in `playtest/README.md` under "AI
settings"; this is the short form plus the open tasks.

---

## 1. How to measure anything here

```bash
python -m playtest.ai.arena --panel --games 12                 # the standard run
python -m playtest.ai.arena --panel --games 12 --terrain siege # objectives pinned
```

`--panel` plays a candidate against several opponent *styles* over all twelve
orderings of four cross-faction squads, both seats. Anything less is not a
measurement. Five traps, all of which cost real time:

1. **A single matchup moves ~10 points between seeds.** Every promising
   one-at-a-time result must be re-run on a **fresh seed** before it is
   believed. A whole round of tuning evaporated that way.
2. **Read the VP margin, not the win rate.** Over the same games the win rate
   is far noisier, and most single-parameter effects are smaller than its
   noise.
3. **The panel moves when the defaults move.** Its members are `standard:...`
   overrides, so they inherit every default change. To compare before/after,
   use opponents that do not move — `camper`, `baiter`, `greedy`, `random` —
   or an explicit `standard:<param>=<old value>`.
4. **The objective mix is a variable, not a backdrop.** `control` is five
   objectives scored by standing somewhere; `siege` scores three of five by
   destroying tokens. The default random deal averages over them and the
   average is nobody's answer — the best `objective_weight` swings ~0.45 VP
   between the two, and the "playing for damage loses" result turned out to be
   an artefact of the deal (−0.23 VP random, ~0 on either fixed mix).
5. **Check `aiParams` in any shared log before analysing it.** See §5.

`camper` and `baiter` (in `ai/baseline.py`) are strategies built on the raw
view dict with no model shared with the agent. They are the only panel members
that cannot share a blind spot with it.

---

## 2. The one big lesson

**Model fixes pay. Strategic levers do not.**

| Kind | Change | Result |
|---|---|---|
| model | `objective_weight` 1.0 → 2.5 (kills and objectives were in different units) | **+0.77 VP** |
| model | `caution` — one-shot risk as a *rate*, not a quantile | **+0.32 VP** head to head, camper 86→97% |
| model | `move_temperature` split from `temperature`, 0.28 → 0.1 | +0.3 VP |
| model | `approach_falloff` 0.72 → 0.55 | +0.3 VP |
| model | block budget spent in **initiative** order, not by damage | correctness; no cost |
| strategy | `bait`, `token_greed`, `contact`, `planning`, `retreat` | all **neutral or negative** |

Together the model fixes took the default from **55.7% to 62.9%** over 1008
panel games (VP +0.28 → +1.11), and it beats the AI it replaced **64.6%** head
to head.

The reason the strategic levers measure flat is structural: **the arena
detects when the AI misreads the game, not when it plays it badly**, because
none of its opponents punish bad strategy. That is not an argument for
shipping unmeasured levers on — it is an argument for building the opponent
that would measure them (see §6, task 1).

Also true and worth not re-testing: more search buys nothing. `pool` already
sees the whole hand, `search_width` 48 → 96 is flat, and `temperature 0` is
*worse* than sampling.

---

## 3. Tried and does not pay — do not redo these

| Tried | What happened |
|---|---|
| `bait` (price the compulsory block that eats your big attack) | Negative at every setting, **including against `baiter`**, which was built specifically to exploit it. The behaviour is real (blocks and wasted actions fall); the win is not. Probably the wrong shape: it penalises *holding* blocks when the answer is to hold a cheap one beside the big attack. |
| `contact` (bonus for ending a move able to strike) | Cuts wasted attacks 6.9 → 5.7/game, raises damage and kills, and **loses VP** — the tiles it walks to are tiles it leaves objectives to reach. |
| `endgame` (discount objectives until they score) | Worse in both directions. Ground counted at the end still has to be taken *early and held*, because frames cannot walk through each other. |
| Rebalancing toward offence (`aggression` up, `defense`/`survival` down) | `aggression=1.6` buys damage and kills and loses 2.5 points. Cutting `defense`/`survival` does nothing. And the penalty is an artefact of the random deal (trap 4). |
| `planning=1.0` (full objective commitment) | **−0.17 VP on `control`**: on contested ground a frame hovering between two objectives takes whichever is free at the end, and an assignment throws that option away. Ships at 0. |
| `retreat` (hurt frames go stand on ground) | Arena-flat (+0.05 VP at best). With `caution` on, the AI keeps 2.3 of 3 frames alive, so there are few hurt frames for it to fire on. Ships at 0. |
| Deployment pulled toward the frame's own objective | **Worse at every strength**: mean column gap 3.9 → 1.6, distinct objectives covered 2.2 → 1.6. The objectives worth assigning sit centrally, so pulling each frame to its own drags them all inward. The blunt `abs(gap - 3)` spacing rule beats it. |

---

## 4. What the human games actually showed

Six exported games. The patterns, in order of how much they explain:

* **Every frame that died, died to one card, from full armour on that zone.**
  Four of four across the first games. The old survival test asked whether the
  pool's *damped peak* hit was lethal — a question about the typical card — and
  on the zone that killed the frame it answered no: 12 of 127 Mid-attacking
  cards could kill outright while the 0.9 quantile read 2. Fixed by `caution`.
* **Card economy.** Human committed 7/5/7 cards that pay on a later turn
  (drones, extra actions, a second hand) against the AI's 0/5/1 — and the one
  game it matched (5-5) is the one it nearly won. The scorer rated a permanent
  free-attacking drone at **0.15**, its worst card, because `CardInfo` did not
  carry drone stats at all. Fixed by `tempo` + exposing `droneHealth`.
* **Block economy.** The AI spent 8 and 6 blocks to the human's 3, and in one
  game gave up **18 damage of its own attacks to stop 9** — blocking a
  1-damage drone poke with a 5-damage Cannon. Real, and `bait` still did not
  fix it (§3).
* **In one 6-0, the AI's 16 attacks produced 6 "no legal target", 9 blocked
  and 1 landed.** Its whole-game damage was 4.

---

## 5. Traps and bugs found along the way

* **`blunder_rate` is gone** (removed 2026-09-09). It was a slider in a
  scrolling drawer, trivially nudged, and silent: a game at 0.85 is dice, and
  looks exactly like an AI that has forgotten how to play. One shared log read
  that way and the "it refused to stand on its own Egg" it seemed to show was
  not real — replayed with the dice off, the AI takes that Egg by turn 2 in
  **6 runs of 8** (0 of 8 at 0.85). **If a log looks inexplicably bad, read
  `aiParams` first.** Old configs carrying the key still load; it is ignored.
* **Exports did not record the resolved seed**, so seedless games — i.e. every
  real one — could not be replayed. Fixed; `config.seed` is now what the engine
  used and `requestedSeed` is what the client asked for.
* **`/api/health` already meant "how many cards" by the key `cards`**, and a
  card-data hash was silently landing on top of that count. It is `cardData`.
* **Drone attacks emitted no beat**, so a drone's damage never moved the
  replay baseline and its burst was drawn on the next card revealed. Found
  only because teaching the AI to value summons made it play them.
* **Two players bringing the same terrain deck hung the game forever.** It is
  a legal deal, and `assault` twice means two Dome Campuses; the bomb carrier
  was answered by objective *name*, so both answers landed on the first copy
  and the second asked 593 times and counting.

Every one of these has a regression test, and each was verified to fail
without its fix.

---

## 6. Open tasks, in priority order

1. **Build an opponent that plays the whole human gameplan.** Economy cards on
   turn 1, cheap fast attacks to strip the guard, then one card that takes a
   zone from full armour to destroyed. `baiter` is the middle third of this and
   is too weak to serve (it loses 86%). *This is the missing instrument* — until
   it exists, "play for kills" will keep measuring as a loss while losing to
   someone doing it, and `bait`/`retreat`/`aggression` cannot be honestly
   judged.
2. **Make objective-plan exclusivity depend on the objective type.** A token
   hunt divides between frames cleanly; contested ground does not. This is what
   the `control`/`siege` split is pointing at, and `ObjectivePlan` is already
   the structure to hang it on.
3. **Deployment is not objective-aware at all**, and not for want of a plan:
   the objective term's falloff spans ~6 tiles while objectives sit 9–13 tiles
   from a deployment row, and the board's Chebyshev distance saturates on the
   *row* difference, so every column is equally far from anything. A direct
   lateral pull was tried and is worse (§3). Needs a different idea.
4. **Re-judge `bait` and `retreat`** once task 1 exists. Both are implemented,
   tested, and shipped at 0.
5. **Keep collecting human logs.** They replay exactly now, and they have been
   the source of every model fix that paid. `python -m playtest.ai.review
   <file.json>` reads one back and reports what changed in the card data since.
