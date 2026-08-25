# mobileSuitGame

A data-driven card game toolkit that generates printable cards and Tabletop Simulator (TTS) assets from CSV data files.

## Core pipeline

```
CSV files → generateCards.py → build/card/*.tex → build/card_all.pdf

decks/*.csv → generate_card_sheet.py → card_sheet.tex → card_sheet.pdf
                                                              ↓
                                                    ImageMagick → PNG (TTS upload)
```

`generate_all_decks.py` automates the full sheet→PDF→PNG pipeline for every deck in `decks/`.

## Scripts

### generateCards.py
Reads all card CSVs; outputs individual `build/card/<Group>_<Name>.tex` files, a combined `build/card_all.tex`, and frame datasheets (`build/frame_*.tex`). Creates the `build/card/` subfolder if it doesn't exist. For weapon cards it also writes `build/group_indicator_<Group>.tex` — a small red-triangle/blue-square grid showing which High/Mid/Low zones that weapon *group* can attack/block across all its cards, `\input` by each card in the group at the bottom left.
It also emits the rules-reference set: one representative card per type annotated with labelled leader lines — bare `\input`-able fragments `build/rules_<type>.tex` (melee weapon, ranged weapon, drone, pilot, frame), a standalone `build/rules_<type>_doc.tex` per fragment, and a combined preview `build/rules.tex`.
Terrain tile generation (`Terrain_square.csv` → `build/terrain/<Name>.tex`) lives in `terrain_cards.py` and is called from here — see below.
Run: `python generateCards.py`

### terrain_cards.py
Generates terrain tiles (`build/terrain/<Name>.tex`) from `Terrain_square.csv`: hex/square tile geometry, elevation-wall styling (`TERRAIN_STYLE`, `STYLE_DICT`), and the objective-point icons. Not run standalone — `generateCards.py` imports `create_terrain_card()` (plus `terrain_file`/`terrianoutputfolder`) from it, creates the `build/terrain/` subfolder up front, and calls `create_terrain_card()` per-row from its `__main__` block.

### generate_card_sheet.py
Arranges card `.tex` files into a printable grid (default 10×7) for TTS; generates multi-page output as needed.
Run: `python generate_card_sheet.py --csv decks/deck_percival.csv --type card --output card_sheet.tex`
Key args: `--type` (`card`/`terrain`/`frame` — prepends the matching `build/` subfolder to bare CSV entries), `--cols`, `--rows`, `--bleed`, `--add_back`, `--back_text`, `--back_color`, `--repeat`

### generate_all_decks.py
Batch-runs the full front+back sheet→PDF→PNG pipeline for all (or specified) decks. Also renders the annotated rules-reference cards (`build/rules_*_doc.tex`) to trimmed PNGs in `RulesImages/`.
Run: `python generate_all_decks.py [deck_prefix ...]` (`--skip-individual`, `--skip-rules`)

### generate_card_json.py
Writes `json/cards.json`, one file holding every card's Tabletop Simulator metadata — 116 action cards plus 12 frames, in CSV order. Each card is keyed by the `raw.githubusercontent.com` URL of its `AllCardImages/*.png` (URL-encoded: most card images have spaces in the filename), and the value holds TTS's fixed `name`/`description`/`gm_notes`/`tags` fields — no others can be added.
`gm_notes` is every stat then the card text, newline separated (`4init\n0mv\natk:1M cut\nBlk:1H\n…`); `description` is one clarifying sentence per keyword the card prints; `tags` are numbered broadest-first — `Card`, then `Action` or `Frame`, then the action's own type (`Weapon`/`Basic`/`Booster`/`Pilot`/`Drone`, taken from the source CSV), then the faction, or `Factionless` for the shared kit that has no `Faction` cell. Two anti-drift ties into the rest of the pipeline: image names come from `generate_card_images.py`'s `card_image_name()`, and the LaTeX keyword macros (`\fulldazed` → `Dazed (-2 card)`) are expanded from `generateCards.py`'s own `ability_dict`/`numbered_ability_dict`/`status_dict`/`rules_dict`. Only the longer `description` sentences live in this script's `GLOSSARY` (wording follows the Keywords section of `rules/rules.tex`) — a startup check exits if `generateCards.py` defines a keyword `GLOSSARY` has no entry for.
Run: `python generate_card_json.py [--output json/cards.json] [--quiet]`

### plotCardStatistics.py
Generates balance analysis visualisations (stat trade-offs, attack distributions, group meta-stats, correlation heatmaps, power budgets).
Run: `python plotCardStatistics.py [--no-basics] [--no-boosters] [--melee] [--ranged]`

### check_deck_coverage.py
Reports how many times each defined card appears across all action decks; flags missing or orphaned entries.
Run: `python check_deck_coverage.py [--by-group]`

### weapon_fingerprint_report.py
Generates a scrollable HTML report (`build/weapon_fingerprint.html`), one row per weapon Group: average attack/block by zone, which ability/status keywords appear in that group's card text, and the Initiative/Movement spread across its cards.
Run: `python weapon_fingerprint_report.py [--output build/weapon_fingerprint.html] [--open]`

## image_generation/

Scripts that procedurally generate card artwork. All paths are resolved relative to the script location so they work regardless of working directory.

| Script | Output | Notes |
|---|---|---|
| `generate_backgrounds.py` | `pictures/backgrounds/<Group>_bg.png` | Deterministic radial-streak background per faction group |
| `generate_weapon_images.py` | `pictures/weapon_pictures/<name>.png` | Weapon sprite + motion-line arcs/rays per attack zone |
| `generate_booster_images.py` | `pictures/booster_pictures/booster_<name>.png` | Mech foreground over rocket-engine flare |
| `set_background_to_transparency.py` | `<input>_t.png` (sibling file) | Converts white pixels to alpha; accepts a file or folder |

Run each from any directory: `python image_generation/<script>.py`

## Card data CSVs

| File | Card type |
|---|---|
| `Weapon actions.csv` | Action cards with attack zones |
| `Basic actions.csv` | Basic action cards |
| `Booster actions.csv` | Booster/movement cards |
| `Pilot actions.csv` | Pilot cards (no attack zones) |
| `Frames.csv` | Mecha datasheets |
| `Terrain_square.csv` | Tactical map tiles |

Key columns: `Name`, `Group`, `Initiative`, `Movement`, `High/Mid/Low Attack+Block+DType+Range`, `Text`, `Persistence`, `CardImg`, `PrintID` (`0` = omit from output).

**Damage types:** `cut`, `pierce`, `impact`, `projectile`, `energy`

**Terrain tile codes:** `e1`/`e2`/`e3` (elevation), `im` (impassable), `obs` (obstacle), `obj` (objective), `tkn` (token)

## Build-system gotchas (non-obvious)

These bit us before; read before changing card layout or the rules.

### `build/card_macros.tex` is generated *and* shared with the rules
`generateCards.py`'s `createMacros()` writes `build/card_macros.tex` (colour defs, damage-type icon macros, the block-shield markers). It is `\input` by the generated card documents **and** by the hand-written `rules/rules.tex` (`\input{../build/card_macros.tex}`). Consequences:
- **Run `python generateCards.py` before building `rules/rules.tex`.** The rules will not compile if `card_macros.tex` is missing or stale — that's the usual cause of a broken rules build.
- **Shared visuals live only in `createMacros()`**, never duplicated in the rules, so the two can't drift. Anything the rules and cards must draw identically (icons, block markers, colours) belongs there.
- **Block markers are the canonical source of truth.** `\normalblock` / `\superblock` (built on `\normalblockpath` / `\superblockpath`) draw the two block shapes; `block_shield_outline()` in Python just emits those macros for cards. `super_block` = a `Block` value > 1. The super block is drawn wider than a normal block and its shape/size are tuned via constants in the macro (width scale factor) and `ZONE_CX` (nudged left so the wider marker clears the card's right edge).
- **Arity trap:** cards call the **5-arg** `\normalblock`/`\superblock` (`{cx}{cy}{colour}{half_w}{half_h}`); the rules call the **3-arg** wrappers `\blockshield`/`\superblockshield` (`{cx}{cy}{colour}`), which invoke the 5-arg macros at a fixed size and let the rules `\resizebox` them. Keep both aritys working — a call-site with the wrong count silently eats following tokens (e.g. `\end{tikzpicture}`) and yields a confusing error far away.

### Annotated "rules-reference" cards reuse the real layout code
The labelled cards in `RulesImages/` / the rules doc are produced by the *same* `make_card_from_row(..., annotate=True)` / `create_frame_sheet(..., annotate=True)` as the printed cards, with two twists:
- **Bounding box / clip is annotate-gated.** With `annotate=False` each card pins its bounding box and clips to the `cardbg` rectangle (`\useasboundingbox` + `\clip`) so nothing spills onto the sheet and every card is exactly the same size. With `annotate=True` that is deliberately skipped so leader lines can extend into the margins. If cards ever misalign on a sheet, something is drawing outside `cardbg` before the clip.
- **Callouts aim at named nodes/coords.** `WEAPON_CALLOUTS` / `PILOT_CALLOUTS` / `DRONE_CALLOUTS` / `FRAME_CALLOUTS` are lists of dicts whose `aim` references TikZ names the layout emits (`nameplate`, `initbox`, `movebox`, `setinfo`, and the zone `\coordinate`s `atk_aim` / `block_aim` / `superblock_aim` / `range_aim`, written at the zone's right edge `ZONE_CX + ZONE_HALF_W`). Those targets exist only when the element was drawn, so `make_card_from_row` builds a `present` set and `_render_callouts()` filters to it. **Rename or stop emitting a node → update the matching callout `aim`**, or the reference card fails to build.
- `_render_callouts()` appends `.east` / `.west` to the aim based on each callout's `side`, so the leader lands on the element edge nearest the card edge and doesn't cross the thing it labels.

### Coordinate system
Card tikzpictures use `[x=0.86cm, y=0.86cm]` (`card_scale`). **Node `minimum width`/`minimum height` are physical cm (unaffected by the x/y unit scaling); path/`\coordinate` positions are in scaled tikz units.** Convert physical→tikz by dividing by `card_scale` (that's why e.g. `ZONE_HALF_W = (ZONE_W_CM / 2) / card_scale`). Mixing the two is the usual source of "box is the wrong size / overlaps its neighbour" bugs.

### Working directory for `pdflatex`
Generated card docs reference assets with `../` relative to `build/`, so compile them **from `build/`**. `rules/rules.tex` uses `../build/...` and is compiled **from `rules/`**. Running `pdflatex` from the repo root will fail to find inputs.

## Asset directories

- `pictures/` — card images; subdirs: `backgrounds/`, `foreground/`, `weapons/`, `weapon_pictures/`, `booster_pictures/`
- `icons/` — mechanic icons
- `terrain/` — terrain tile PNGs
- `tts_assets/` — card back designs and tokens for tts
- `decks/` — per-faction deck CSVs (one bare card name per row; `deck_terrain_*` list terrain tiles and `deck_objective_*` the scoring terrain that pairs with them — both render with `--type terrain` — while other `deck_*` list cards; `generate_all_decks.py` picks the `--type` from the filename). Per the rulebook each player brings a 10-card terrain deck plus a 5-card objective deck, so `deck_terrain_<name>.csv` and `deck_objective_<name>.csv` are used together. `frames_deck.csv` lists bare frame names and is rendered with `--type frame` (see `.vscode/launch.json`). The root `individual_cards.csv` is a mixed deck that lists explicit folder-prefixed paths instead (no `--type`)
- `json/` — `cards.json`, the Tabletop Simulator metadata for every card, generated by `generate_card_json.py`
- `RulesImages/` — annotated rules-reference card PNGs (one per card type), rendered by `generate_all_decks.py`
- `build/` — all generated output; do not edit by hand
