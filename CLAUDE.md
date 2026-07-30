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
Reads all card CSVs; outputs individual `build/card/<Group>_<Name>.tex` files, a combined `build/card_all.tex`, frame datasheets (`build/frame_*.tex`), and terrain tiles (`build/terrain/<Name>.tex`). Creates the `build/card/` and `build/terrain/` subfolders if they don't exist. For weapon cards it also writes `build/group_indicator_<Group>.tex` — a small red-triangle/blue-square grid showing which High/Mid/Low zones that weapon *group* can attack/block across all its cards, `\input` by each card in the group at the bottom left.
It also emits the rules-reference set: one representative card per type annotated with labelled leader lines — bare `\input`-able fragments `build/rules_<type>.tex` (melee weapon, ranged weapon, drone, pilot, frame), a standalone `build/rules_<type>_doc.tex` per fragment, and a combined preview `build/rules.tex`.
Run: `python generateCards.py`

### generate_card_sheet.py
Arranges card `.tex` files into a printable grid (default 10×7) for TTS; generates multi-page output as needed.
Run: `python generate_card_sheet.py --csv decks/deck_percival.csv --type card --output card_sheet.tex`
Key args: `--type` (`card`/`terrain`/`frame` — prepends the matching `build/` subfolder to bare CSV entries), `--cols`, `--rows`, `--bleed`, `--add_back`, `--back_text`, `--back_color`, `--repeat`

### generate_all_decks.py
Batch-runs the full front+back sheet→PDF→PNG pipeline for all (or specified) decks. Also renders the annotated rules-reference cards (`build/rules_*_doc.tex`) to trimmed PNGs in `RulesImages/`.
Run: `python generate_all_decks.py [deck_prefix ...]` (`--skip-individual`, `--skip-rules`)

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

## Asset directories

- `pictures/` — card images; subdirs: `backgrounds/`, `foreground/`, `weapons/`, `weapon_pictures/`, `booster_pictures/`
- `icons/` — mechanic icons
- `terrain/` — terrain tile PNGs
- `tts_assets/` — card back designs and tokens for tts
- `decks/` — per-faction deck CSVs (one bare card name per row; `deck_terrain_*` list terrain tiles, other `deck_*` list cards — `generate_all_decks.py` picks the `--type` from the filename). `frames_deck.csv` lists bare frame names and is rendered with `--type frame` (see `.vscode/launch.json`). The root `individual_cards.csv` is a mixed deck that lists explicit folder-prefixed paths instead (no `--type`)
- `RulesImages/` — annotated rules-reference card PNGs (one per card type), rendered by `generate_all_decks.py`
- `build/` — all generated output; do not edit by hand
