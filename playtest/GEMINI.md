# Playtest Guidelines & Rules

Directory-specific rules for `playtest/` (the phone-playable engine, server, client, and AI).
Detailed runbooks and background notes are in `.agents/skills/playtest-engine/SKILL.md` and `.agents/skills/playtest-ai/SKILL.md`.

---

## 1. The card text is the source of truth. Parse it.
Card text is *parsed* rather than keyed wherever possible, so a CSV balance edit needs no engine change.
- Never hardcode numbers or radii in module constants. Use `_reach_from_text`, `_reaches_from_text`, `_spaces_from_text`, and `_count_from_text` in `engine/effects.py`.
- If a card leaves an area behind, store the radius on `TokenState.aura_radius` parsed from the second "within N" and return it to the client via `effects.token_aura(token)`.
- Fallbacks named `*_RADIUS` in `effects.py` are only for text that stops specifying distance, not the primary rule.

## 2. Never pin a CSV number in a test
Do not assert exact CSV values like `initiative == (8, 3)` or `reach == 7`. Read values from `CATALOGUE` and test the structural relationship or shape.

## 3. The public log is part of the hidden-information surface
`GameState.note` ships **verbatim to both seats**.
- Hidden frame positions must pass through seams (`effects.move_note`, `objectives._holder_name`).
- Resolution order must not leak hidden identity: decoy queues and frame movement must be shuffled before resolution.

## 4. `BoardProtocol` is frozen: degrade optional kwargs one at a time
When handling optional kwargs (such as `blocking` and `flying_target`), never drop both in a single `try/except`. Degrade through a stepwise ladder:
`extras = ({"blocking": ..., "flying_target": ...}, {"flying_target": ...}, {})`

## 5. Non-negotiable constraints
- **Offline on phone**: The shipped server uses Python stdlib `http.server` only, loopback-bound. No third-party packages in `playtest/server/__main__.py`. All art must be bundled in `playtest/server/static/`.
- **AI isolation**: `playtest/ai/*` may import only `..engine.board`, `..engine.types`, and `..engine.hazards`. Enforced by AST tests.
- **Pure command application**: `GameState.clone()` deep-copies; no references to frame or card objects may be held in the effects bag.
- **Structural hidden information**: Hidden info is enforced by `view_for`, never by trusting the client.

## 6. Verification stack
Run before reporting changes to the engine:
```bash
.venv/bin/python -m pytest playtest/tests -q
.venv/bin/python -m playtest.ai.arena
python generateCards.py
python generate_card_json.py --quiet
python check_deck_coverage.py
```
Whenever `effects.py` changes:
```python
from playtest.engine import effects
from playtest.engine.cards import load_cards
assert not effects.deferred_effects(load_cards())
```
After CSV edits, regenerate images:
```bash
python generateCards.py
python generate_card_images.py --all
python -m playtest.server.images
```
