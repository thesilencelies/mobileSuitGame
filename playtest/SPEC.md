# NetFrame Playtest App — build specification

This is the **shared contract** for the playtest app. Four workstreams build against it in
parallel; the seams defined here are fixed and must not be renamed or restructured without
saying so explicitly in your final report.

Everything in `playtest/` is new. Nothing outside `playtest/` may be modified except
`generate_card_images.py` (workstream E) — the card-generation pipeline stays as it is.

## What we are building

A mobile-playable app for playtesting NetFrame against an AI. Decisions already taken:

| Decision | Choice |
|---|---|
| Stack | Python rules engine + FastAPI + no-build mobile-first web client, played in a phone browser over LAN |
| Match size | **3v3** (rulebook default), engine generic over team size |
| Card text scope | v1 = weapons/basics/boosters + **all keywords** + statuses. Pilot & drone card *effects* land in a later pass behind an effects registry — but pilot/drone cards must still load, block and deal damage |
| Terrain | Real dealing from terrain + objective decks; **all 8 objectives scripted** |

`simulation/simulate.py` is a *balance abstraction* — it deliberately ignores range,
movement, terrain, LoS, abilities, damage types and card text. It is **not** the engine and
must not be imported by it. Its scorer is a reference for workstream D only.

## Source of truth

The rulebook is `rules/rules.tex`. Read it — sections `The Battlefield` (248), `Setup` (290),
`The turn` (366) through `Cleanup` (596), `Tokens` (773), `Status` (790), `Deck
Construction` (812), `Keywords` (953). Card data lives in the repo-root CSVs:
`Weapon actions.csv`, `Basic actions.csv`, `Booster actions.csv`, `Pilot actions.csv`,
`Drone actions.csv`, `Frames.csv`, `Terrain_square.csv`. Decks are in `decks/deck_*.csv`
(one bare `Group_Name` per line), terrain decks in `decks/deck_terrain_*.csv`.

Where this spec and the rulebook disagree, the rulebook wins — tell me in your report.

## Layout

```
playtest/
  SPEC.md              this file
  engine/
    types.py           SHARED CONTRACT — already written, treat as frozen (see below)
    cards.py       (B2) card + deck loading, deck legality
    terrain.py     (B1) terrain card parsing
    board.py       (B1) board assembly, adjacency, movement, line of sight
    setup.py       (B1) terrain dealing, objective placement, deployment
    state.py       (B2) GameState, FrameState, statuses, damage, VP
    resolve.py     (B2) turn/phase state machine, initiative queue
    combat.py      (B2) attack/block matching, elevation shift, damage
    keywords.py    (B2) keyword behaviours
    effects.py     (B2) card-text effect registry
    objectives.py  (B2) the 8 objective scorers
    serialize.py   (B2) GameState -> client JSON
  ai/              (D)  AI agent + tunable parameters
  server/          (C)  FastAPI app
    static/        (C)  index.html, app.js, app.css
  tests/           (B1/B2 own theirs; C and D add their own)
```

Run tests with `python -m pytest playtest/tests -q` from the repo root.

## `engine/types.py` is frozen

It defines `Zone`, `ZONES`, `Pos`, `Tile`, `Card`, `FrameSpec`, `Team`, `Phase`,
`StatusKind`, `BoardProtocol`, and the tuning constants. **Read it before writing anything.**
Add fields if you genuinely need them, but do not rename or repurpose what is there, and
call out any addition in your report so the other workstreams learn about it.

## Rules subtleties that are easy to get wrong

These are the traps. Each one needs a test.

1. **Planning is simultaneous and hidden.** Each frame draws 7 (modified), commits 2 face
   down, discards the rest. Drawing from an empty deck reshuffles that frame's discard pile.
   The AI must commit without seeing the human's cards — no peeking. Enforce this in the
   engine's API shape, not by good manners in the AI.
2. **Status counters are removed at the *end* of planning**, one of each type, after cards are
   drawn and committed. Opposite statuses annihilate on application, leaving the difference.
   A status modifies by a fixed amount regardless of how many counters are stacked; the count
   is only its duration.
3. **Initiative** resolves highest first. Ties resolve alternately, clockwise from the
   priority marker. Initiative is modified by −1 when the High zone is at its last hit, and
   ∓2 by Stunned/Stimmed. A card's printed initiative can be a **list** — `Quick Step` is
   `"8,3"` and acts twice if not consumed. Parse it as a tuple.
4. **Resolution is movement, effect, attack in any order**, chosen by the controller.
   Movement cannot be split around the other two steps.
5. **Movement** is base + card modifier, 8-way including diagonals. Climbing costs 1 extra
   per elevation and you cannot stop part-way up. Descending any number of levels is free.
   Obstacles and impassable tiles cannot be entered. Frames block movement.
   `Flying` ignores obstacles and elevation costs.
6. **Ranged attacks may not target an adjacent frame**; melee attacks (no range listed) must.
   On a multi-range attack only the zones actually in range count.
7. **Line of sight** is the fiddliest predicate in the game. The rule is "draw a line from
   anywhere on the attacking frame to anywhere on the target" — so it is *permissive*: LoS is
   clear if **any** line between the two tiles' extents is unobstructed, not just centre to
   centre. Obstructions are: impassable terrain; terrain higher than the attacker; obstacles
   *adjacent to the target*; terrain higher than the target *adjacent to the target*. A tile
   with a frame on it counts as one elevation higher. `rules/rules.tex` lines 436–488 have
   three worked figures — turn all three into tests.
8. **Blocking is compulsory.** If any of the defender's remaining cards blocks any attacked
   in-range zone, they must block. "Remaining" includes both face-down (unresolved) and
   already-resolved cards still on the field. The blocking card is discarded, and if it had
   not yet resolved its own action is forfeit. A **super block** (`Block >= 2`) blocks
   identically but is *not* discarded. One matching zone stops the entire attack.
9. **Melee elevation shift.** Attacks shift one bracket per elevation of difference,
   toward High when the attacker is higher and toward Low when the attacker is lower.
   Anything pushed past High or below Low is out of range. Ranged attacks are unaffected.
   The rulebook prose at line 563 says "moved up" but both worked examples say otherwise —
   **the examples are authoritative**, and both are tests.
10. **Cleanup:** cards without persistence are discarded; persistent cards that are still
    active are set aside and neither resolve nor block; expired ones are discarded. The
    priority marker then moves one step anticlockwise. `Persistence` of `\infty` is permanent,
    an integer is a turn count, `0` means none.
11. **Echoes of the fallen** — after a frame dies, at the end of planning its controller may
    reveal the top card of its deck and set it sideways alongside a surviving frame's actions,
    where it can block for that frame (ignore the rest of its text).
12. **Frame abilities are effects too.** All 12 in `Frames.csv` are in scope for v1 — they are
    short and mechanical (Hector's first block each turn is not discarded, Adam's pierce
    attacks get +2 initiative, Fenrir cannot use ranged weapons, Kamikiri's first melee attack
    each turn deals an extra cut Mid, and so on). `Flying`, `Shield(X)` and `Deathstrike` are
    keyword-driven; the rest need registry entries.
13. **Game length is 5 turns.** VP = 1 per enemy frame defeated + objective values.

### Open rules question — resolve it this way and flag it

`rules/rules.tex:578` says a frame is destroyed when a zone takes *more* damage than its
armour, which would make an armour-4 zone survive 4 hits. But the worked example at line 591
says an armour-4 Kuwagata on 3 damage is at −1 initiative and **one more hit destroys it**.
The two readings differ by a full hit of durability on every frame.

Implement the **worked example**: `damage >= armour` destroys, and the last-hit penalty
applies at `damage == armour - 1`. Put it behind `ARMOUR_KILLS_AT` in `types.py` so the other
reading is one constant away, and note it in your report. (For reference, `simulation/` uses
the other reading — that discrepancy is real and I am raising it with the author.)

## Objectives

Nine objective rows in `Terrain_square.csv` carry non-zero points; `Helpcard` is a legend, not
a real objective, so **8 are in scope**. The defender is whoever brought the card; green
(`Defend Points`) is the defender's score, red (`Attack Points`) the attacker's.

| Objective | Behaviour |
|---|---|
| Power Reactors | 4 tokens, 2 HP each. Attacker scores if ≥3 destroyed, else defender |
| Shiny Thing | 1 token, picked up on contact, dropped on damage to the adjacent tile nearest the damage source. Team holding it at the end scores |
| Triangle | If only one team has a frame on the triangle, that team scores |
| Fugitive | Token placed anywhere in the enemy back row after deployment; moves with any adjacent ally. Defender scores if it reaches the objective point, else attacker |
| Holo Spires | Attacker scores if any attacking frame is on a spire at the end, else defender |
| Church | If only one team has a frame within 2 of the church, they score |
| The Tower | 4 HP token; attacker scores if destroyed, else defender |
| The Egg | First frame to end two consecutive turns standing on the Egg scores it |

Scoring timing is under-specified in the rulebook. **Assume:** everything is evaluated at the
end of the game (after turn 5), except The Egg, Power Reactors, The Tower and Fugitive, which
*latch* the moment their condition is met and stay latched. Flag this assumption.

Tokens are attackable like frames (`rules/rules.tex:778`): a single health stat, damage from
any zone counts the same, they never block, and they cannot be repaired.

## Engine public API

`playtest/engine/__init__.py` exports exactly this. Workstreams C and D code against it and
nothing else.

```python
def new_game(config: GameConfig) -> GameState
def legal_commands(state: GameState, seat: int) -> list[Command]
def apply_command(state: GameState, cmd: Command) -> GameState   # returns a NEW state
def view_for(state: GameState, seat: int) -> dict                # redacted JSON, hides hidden info
def is_over(state: GameState) -> bool
def scores(state: GameState) -> dict[int, int]
```

**The state machine drives everything.** `GameState.pending` is either `None` (the game can
advance itself) or a `PendingDecision` naming the seat to act, the kind of decision, and the
legal options. The server loop is: apply command → advance until `pending` belongs to a human
→ send the redacted view. The AI plugs into the exact same interface, so an AI-vs-AI game is
just two agents in a loop and needs no separate code path. That headless mode must work — it
is how the AI gets tuned.

Decision kinds: `commit_actions` (choose 2 cards), `resolve_order` (movement/effect/attack
order), `move` (choose destination path), `attack_target` (choose target + zones),
`choose_block` (which card blocks — compulsory when any option exists), `effect_choice`
(card-text prompts), `echo_card` (Echoes of the fallen).

`apply_command` must be **pure** — no mutation of the input state. Deep-copy or rebuild.
The engine must be deterministic given a seed: all randomness goes through a single
`random.Random` stored on the state, so a game can be replayed exactly.

## Client JSON schema

`view_for(state, seat)` returns, with everything the seat is not entitled to see already
removed (opponent hands, face-down commitments, deck order):

```jsonc
{
  "gameId": "…", "turn": 2, "phase": "action", "priority": 0, "seat": 0,
  "board": {
    "width": 15, "height": 16,
    "tiles": [{"x":0,"y":0,"elev":0,"impassable":false,"obstacle":false,
               "objective":null,"card":"Warehouse"}],
    "objectives": [{"name":"The Tower","owner":1,"defend":2,"attack":2,
                    "tiles":[[3,4]],"status":"unscored"}]
  },
  "frames": [{
    "id":"a0","seat":0,"name":"Percival MkIV","faction":"Aegis",
    "pos":{"x":4,"y":15},"elev":0,"alive":true,
    "armour":{"High":4,"Mid":4,"Low":4}, "damage":{"High":1,"Mid":0,"Low":0},
    "movement":4,"shields":0,
    "statuses":{"stunned":0,"dazed":2,"slowed":0,"stimmed":0,"lucid":0,"boosted":0,"revealed":0},
    "committed":[{"uid":"c17","key":"Spear_Thrust","resolved":false,"faceDown":true}],
    "onField":[{"uid":"c04","key":"Basic_Block","resolved":true}],
    "deckCount":18,"discardCount":3
  }],
  "tokens":[{"id":"t0","kind":"reactor","pos":{"x":7,"y":9},"hp":2,"maxHp":2}],
  "pending": {"seat":0,"kind":"choose_block","prompt":"…","options":[…]},
  "log": [{"turn":2,"text":"Percival attacks Kuwagata High for 2 — blocked by Basic_Block"}],
  "vp": {"0":1,"1":0}
}
```

Card *stats* are not repeated per instance — the client fetches the static card catalogue once
from `GET /api/cards` and looks up by `key`. `key` is `"{Group}_{Name}"`, which is also the
card image filename stem (workstream E), so the client can show real card art.

## HTTP API

```
GET  /api/cards                  static catalogue: every card keyed by "{Group}_{Name}"
GET  /api/frames                 frame stats from Frames.csv
GET  /api/decks                  available decks from decks/
GET  /api/ai/params              AI parameter schema: name, label, min, max, default, help
POST /api/game                   {seed?, playerDecks[], aiDecks[], aiParams{}} -> {gameId, view}
GET  /api/game/{id}              current redacted view for the human seat
POST /api/game/{id}/command      {kind, …} -> new view; advances until a human decision
POST /api/game/{id}/undo         step back one human decision (nice to have)
GET  /api/game/{id}/log          full event log
```

Games live in an in-process dict keyed by game id. No database, no auth — this runs on a
laptop on a home network.

## Workstreams

### B1 — spatial layer (`terrain.py`, `board.py`, `setup.py`)

Terrain cards are a **4-row × 3-column** grid of tiles (`tile_0_0` … `tile_3_2`). Cell codes are
space-separated and may combine: `e1`/`e2`/`e3` elevation, `im` impassable, `obs` obstacle,
`obj` objective, `tkn` token spawn. An empty cell is ground (elevation 0). `BehindText` in the
Helpcard row is a legend artifact — ignore it.

Board assembly for N frames per player: each player lays 2 rows of (N+2) cards, so 3v3 gives a
5-cards-wide × 4-cards-tall board = **15 × 16 tiles**. The opponent's two rows are dealt
rotated 180°, so their tiles must be rotated too. Each player places one objective card per
row, in a slot of their choosing; the rest of the grid is filled from the shuffled terrain
deck. Deployment alternates, each frame onto the outermost tile row of its own half.

Deliver: parsing, board assembly, 8-way adjacency, Chebyshev range, movement/reachability with
elevation and obstacle costs, and the LoS predicate. Pure functions over `BoardProtocol` —
**no game state, no cards, no imports from `state.py`**. This layer is independently testable
and must have tests for all three LoS figures and for climb/descend asymmetry.

### B2 — combat & state layer

Everything else in `engine/`. Depends on B1 only through `BoardProtocol` in `types.py`; stub it
in your own tests so you are not blocked. Owns the state machine, initiative queue, the
compulsory-block logic, elevation shift, damage and destruction, statuses, keywords, frame
abilities, objectives, cleanup, VP, and `serialize.py`.

Deck legality (`rules/rules.tex:829`): exact deck size; max 4 pilot cards all from one pilot
with no duplicates; at most the frame's booster count (duplicates allowed); up to N weapons
where each weapon slot contributes at most one of each card in that group; any number of
basics; one of each frame card for that faction; faction-locked cards only in matching decks.
Validate the shipped `decks/*.csv` against this and report which fail rather than "fixing" them.

### C — server and web client

FastAPI + a **no-build** client: plain HTML/CSS/ES modules, no npm, no bundler, no CDN (this
machine has no node and the phone may be offline). Mobile-first, portrait, one-handed.

The board is 15×16 tiles on a phone screen — that is the whole design problem. Pan/zoom with a
minimap or a fit-to-screen toggle; tap a frame to select, tap a tile to move, with reachable
tiles and threat range shaded. Planning is a card-picking screen showing real card images
(`CardImages` from workstream E) with the two committed slots prominent. Card art should be
lazily loaded and downscaled — do not ship 300dpi PNGs to a phone.

Expose AI parameters in the new-game screen and in a settings drawer, built dynamically from
`GET /api/ai/params` so D can add parameters without you touching the UI.

Include a `playtest/README.md` with how to run it and how to reach it from a phone.

### D — AI

`playtest/ai/` implementing the same command interface as a human seat. Port the scorer ideas
from `simulation/simulate.py` — relative initiative (is my card resolving before theirs?),
zone coverage against the opponent's attack profile, concentration, dominated-card filtering,
reload-awareness — then add what the abstraction never had: **movement, positioning, range,
LoS and objectives**.

The AI must decide, in order: which 2 cards to commit (without seeing the opponent's), the
resolve order, where to move, what to attack, and which card to block with. Movement is the
genuinely new problem — closing to melee range, holding objectives, breaking LoS from ranged
threats, and using elevation to shift its melee attacks into zones the target cannot block.

Parameters are a dataclass with a schema exposed over HTTP: at minimum `defense`,
`concentration`, `aggression`, `objective_weight`, `focus_fire`, `pool`, `temperature`, plus
difficulty presets. Every parameter needs a one-line `help` the app can show.

Ship a headless harness (`python -m playtest.ai.arena`) that plays N games AI-vs-AI and reports
win rates and VP, so parameter sets can be compared. That is how this gets tuned.

### E — card images

Add a flag to `generate_card_images.py` that renders **every** card to its own PNG in a
separate folder, so the app can show real art. Enumerate from all the action CSVs plus frames
and terrain, skipping `PrintID == 0`. Filenames must be exactly `{Group}_{Name}.png` for action
cards (matching the engine's card `key`), plus frames and terrain. Keep the existing
`--csv` behaviour working unchanged.
