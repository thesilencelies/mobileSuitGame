# Playtest engine: what bit us, and the rulings behind it

Working notes for `playtest/` — the phone-playable engine, server and client.
`playtest/README.md` is the full architecture document and stays authoritative;
this is the shorter list of things that are **not** obvious from reading the
code, and that have each cost real debugging time.

Root `GEMINI.md` covers the card/TTS build pipeline. This covers the engine.

---

## 1. The card text is the source of truth. Parse it.

The stated principle is "card text is *parsed* rather than keyed wherever
possible, so a CSV balance edit needs no engine change". The failure mode is
always the same and it is **silent**: a number gets copied into a module
constant, the CSV moves, and the engine keeps enforcing the old value while the
card, the client and the rulebook all print the new one.

This has happened at least four times. Most recently a balance pass moved ten
"within N" values and three of them did nothing, because `DARKNESS_RADIUS`,
`STORM_RADIUS` and `GRAVITY_RADIUS` were constants.

The readers, in `engine/effects.py`:

| helper | reads |
|---|---|
| `_reach_from_text(text, default)` | the **first** "within N" |
| `_reaches_from_text(text, *defaults)` | **every** "within N", in order |
| `_spaces_from_text(text, default)` | "N space" — Set the trap only |
| `_count_from_text` | "Summon **two** attack dogs" |

**Rules of thumb**

- A card that names two distances names two different things. Displace grabs
  "within 5" and throws "within 8"; Gravity Well is *placed* within 5 and
  *drags* within 4. Reading only the first is reading half the card.
- A card that leaves an **area** behind carries the radius on the token it
  creates (`TokenState.aura_radius`), parsed from the second "within N".
  `effects.token_aura(token)` returns that same number to the client. The ring
  drawn and the rule enforced must come from one place — a ring drawn at 5 over
  a rule enforced at 4 is worse than no ring at all.
- The `*_RADIUS` names still in `effects.py` are **fallbacks for text that
  stops saying**, not the rule. Do not "fix" a balance change by editing them.
- Numbers checked at *attack* time, when the card is already in the `aside`
  pile, read from `state.catalogue[KEY].text` — Fog of war and Utter darkness
  both do this.

## 2. Never pin a CSV number in a test

Same bug, other side. Tests that assert `initiative == (8, 3)` or `reach == 7`
turn a one-line balance edit into a test failure in two unrelated files, and
teach nothing when they fail. Read the value from `CATALOGUE` and assert the
*shape*:

```python
early, late = CATALOGUE["Booster_Quick Step"].initiative
assert early > late, "the second act is later in the turn"
```

## 3. The public log is part of the hidden-information surface

`GameState.note` ships **verbatim to both seats**. `view_for` being airtight is
not enough: Ephemeral Images redacted the frame's tile from the view and then
printed it in the log on the next move.

Anything that could name a hidden frame's position goes through a seam:

- `effects.move_note(state, frame, dest, verb)` — moves, shoves, knockbacks,
  boomerang snap-backs. Says "an image of Blue Mystic moves to (7,1)" while
  cloaked.
- `objectives._holder_name(state, frame, via)` — picking a token up and
  dropping it. The token's tile is public, so naming the frame that walked onto
  it places the frame exactly.

**Ordering is a leak too.** The movement step used to ask the frame first and
queue the decoys behind it, so the first "an image moves" line every turn was
the answer. `effects.begin_image_walk` shuffles all three into one queue and
`resolve._movement_decision` drains it; the real image's entry *is* the frame's
own move. Teleport shuffles its own queue the same way.

When touching this, sweep for it rather than reasoning about it — run 30-40 AI
games and grep every log line written while a frame was cloaked.

## 4. `BoardProtocol` is frozen: degrade optional kwargs one at a time

New board capabilities are added to the concrete `Board` with a `try/except
TypeError` fallback at the call site (precedent: `flying_target`, `climb_free`,
now `blocking`). With **two** optional kwargs a single try/except is wrong — it
drops both, so a board that supports `flying_target` but not `blocking` quietly
stops being told about the flyer. `combat._line_of_sight` walks a ladder:

```python
extras = ({"blocking": …, "flying_target": …}, {"flying_target": …}, {})
```

## 5. Rulings from playtesting

Decisions the code cannot justify on its own. Each is now in
`rules/rules.tex` and in the relevant docstring.

**Ephemeral Images**
- Each image is a frame in all regards until exposed: targeting, interactions,
  blocking sight and movement, picking up and carrying tokens
  (`TokenState.carrier_via`, `objectives._holder_at` / `on_image_move`). If
  only the real image could carry the relic, the piece holding it would answer
  the card for free.
- A debuff aimed at any image lands on the frame (`_frame_options` /
  `_target_frame` is the single targeting seam).
- All three act at once; where they must be resolved one at a time the order is
  **random**.
- Anything the action counts may be counted from **any** image
  (`effects_state.origins`). One action still makes one thing — a single storm,
  placed within range of whichever image suits.
- **An attack that connects ends the trick.** A decoy cannot hurt anything, so
  a hit that lands says the swing was real and every image comes down. The test
  is *connecting*, not hit points coming off: **a shield counter is a
  replacement, not a negation** — the damage was dealt and the counter was
  spent instead of armour. Gating on damage taken would make a shield a way to
  shoot from cover for free. A **blocked** attack gives nothing away: no zone
  lands, so `combat.finish_target` never calls in.

**Line of sight**
- An obstacle screens only what stands at **its own elevation**, and it is the
  **target's** elevation that counts, not the shooter's. Cover belongs to the
  frame behind it, so rubble beside a target screens it from a rooftop too.
  What it must not do is protect a frame standing *above* it. An obstacle
  higher than the target already obstructs under "higher terrain next to the
  target".
- Impassable terrain a **card** put down blocks sight like printed impassable
  terrain: Barricade, Cage Fight walls. `GameState.sight_blockers()`. Passing
  them as `occupied` only makes them one elevation higher, which lets anyone
  above see through a wall.
- `occupied()` raises a tile one level; `sight_blockers()` stops the line
  outright. That is the difference between a frame and a wall. Both read
  `GameState.SOLID_TOKENS` so they cannot drift.

**Other**
- Kuwagata's mulligan is spent by *mulliganing*, not by being asked. Declining
  costs nothing and the offer returns every planning phase; a turn flag stops
  it being asked twice in one phase.
- A Reload card's **spent dud is discarded** ("and then this card is
  discarded", rules.tex:963). Reload cards print `\infty` so the *marker* stays
  out until the weapon fires; letting the card that fired it inherit that
  persistence parked it in the aside row for the rest of the game.
- Movement: diagonals are steps; you may move *through* a friendly frame but
  not stop on one; climbing costs 1 per level and descending is free; a gravity
  well is priced step by step, not once.

## 6. Client conventions worth knowing

- **A frame tile's four corners are spoken for.** Ordinal badge top-left,
  status pips top-right, damage strip down the right edge, tile elevation `▲N`
  bottom-left, bomb carrier `B` bottom-right. Adding a fifth mark means taking
  one of these.
- **`decisions.js` dispatches on option *shape*, not decision kind.** One
  `effect_choice` covers every card effect, so the renderer is chosen by what
  the options look like. A target option carrying `zones` gets the full attack
  sheet (`targetBox`); one without gets the plain list. That is how a drone's
  shot came to be told the same way as the frame's own attack — the engine
  simply started shipping the `zones` it was already computing.
- **Nothing in the client identifies a frame from prompt text.** `frameId`, or
  the frame the player picked.
- The player must never be shown a number the engine does not enforce. Auras,
  movement costs, out-of-reach tiles and objective tallies all come from the
  engine as data or prose.

## 7. Traps in the test harness

- **`make_state()` uses `StubBoard`**, whose `has_line_of_sight` returns a flat
  boolean and ignores geometry entirely. Any line-of-sight test needs a real
  `engine.board.Board` built from `terrain.parse_cell`. `FlatBoard` refuses to
  answer at all unless constructed with `los_always_clear=True` — deliberately,
  so a featureless board cannot quietly play a different game.
- `StubBoard` also lacks the optional kwargs, which is what the degradation
  ladder in §4 exists for — and what a test asserting the fallback relies on.
- **`resolve.cleanup_phase(state)` does the pile work**; `_finish_cleanup` only
  asks what cleanup owes and rolls the turn. Calling the wrong one makes a
  reload/persistence trace look broken when it is not.
- Driving a card by hand is `R._begin_resolution(...)` then `R._finish_card(...)`.
  `R.advance` will park on the movement decision and never reach `_finish_card`.
- API shapes that are easy to get wrong: `new_game(cfg)` takes one argument;
  `Agent(seat, catalogue_json(state.catalogue), params, seed=)` wants the JSON
  catalogue, not the `Card` objects; `agent.act(view_for(state, seat))` wants
  the *view*; the pending kind for movement is `"move"`.

## 8. Verification stack

Run all of it before reporting a change to the engine:

```bash
.venv/bin/python -m pytest playtest/tests -q                    # ~665 tests
.venv/bin/python -m playtest.ai.arena                          # AI still plays
python generateCards.py                                        # cards + macros
python generate_card_json.py --quiet                           # TTS metadata
python check_deck_coverage.py
```

Plus, whenever `effects.py` changes:

```python
from playtest.engine import effects
from playtest.engine.cards import load_cards
assert not effects.deferred_effects(load_cards())     # must stay empty
```

For anything touching hidden information or a card that resolves rarely, play
real games rather than trusting unit tests — 30 seeds with the decks that
actually contain the card, driving `apply_command` through `Agent`.

**After a CSV edit** the card image and the phone bundle are stale:

```bash
python generateCards.py
python generate_card_images.py --all                  # or --csv for one card
python -m playtest.server.images                      # 240px bundle
```

Beware: `--all` names action-card images `"{Group}_{Name}.png"` with **spaces
preserved**, while the `--csv` path runs the name through `card_image_name()`,
which collapses whitespace to underscores. Rendering one card with `--csv`
writes a *new* file rather than replacing the tracked one. TeX Live's kpathsea
silently case-folds a failed graphics lookup, which once masked a nine-file
naming mismatch for months.

## 9. Constraints that are not negotiable

- The app runs **entirely on the user's phone, offline**: stdlib `http.server`
  only, loopback-bound, all art committed because a phone cannot regenerate it.
  A static test asserts `playtest/server/__main__.py` names no third-party
  package.
- `playtest/ai/*` may import only `..engine.board`, `..engine.types` and
  `..engine.hazards`. An AST test enforces it.
- `apply_command` is pure: `GameState.clone()` deep-copies, so nothing in the
  effects bag may hold a reference to a frame or card object.
- Hidden information is enforced **structurally** by `view_for`, never by the
  client behaving itself.
