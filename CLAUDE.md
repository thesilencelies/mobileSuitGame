# mobileSuitGame

A data-driven card game toolkit that generates printable cards and Tabletop Simulator (TTS) assets from CSV data files.

## Core Workflow

```
CSV files (card data)
    └─> generateCards.py        → build/card_*.tex (individual card LaTeX files)
                                → build/card_all.tex (master file)
                                → pdflatex → build/card_all.pdf

decks/*.csv (lists of card .tex filenames)
    └─> generate_card_sheet.py  → card_sheet.tex
                                → pdflatex → card_sheet.pdf

card_sheet.pdf
    └─> ImageMagick (convert)   → PNG images for upload to TTS
```

## Scripts

### generateCards.py
Reads all card CSV files and generates LaTeX/TikZ card files.

- Output goes to `build/`
- Groups cards by `Group` column, resets counter per group
- `PrintID` column controls how many copies of a card to generate
- Produces individual `build/card_<Group>_<N>.tex` files plus a combined `build/card_all.tex`
- Also generates frame datasheets (`build/frame_*.tex`) and terrain tiles (`build/terrain_*.tex`)

Run: `python generateCards.py`

### generate_card_sheet.py
Arranges card `.tex` files into a printable grid for TTS.

- Input: a deck CSV from `decks/` (one `.tex` filename per row)
- Default grid: 10 columns × 7 rows (70 cards/sheet)
- Generates multi-page output if card count exceeds grid

Run: `python generate_card_sheet.py --csv decks/deck_percival.csv --output card_sheet.tex`

Key args: `--cols`, `--rows`, `--bleed`, `--add_back`, `--back_text`, `--back_color`

### plotCardStatistics.py
Generates balance analysis visualizations from the CSV data. Produces 5 matplotlib figures covering stat trade-offs, attack distributions, group meta-stats, correlation heatmaps, and power budgets.

Run: `python plotCardStatistics.py [--no-basics] [--no-boosters] [--melee] [--ranged]`

## Card Data CSVs

| File | Card Type | Key Columns |
|---|---|---|
| `Weapon actions.csv` | Action cards with attacks | Name, Group, Initiative, Movement, High/Mid/Low Attack+Block+DType+Range, Text, OneUse, CardImg, PrintID |
| `Basic actions.csv` | Basic action cards | Same as Weapon actions |
| `Booster actions.csv` | Booster cards | Same as Weapon actions |
| `Pilot actions.csv` | Pilot cards (no attack zones) | Name, Group, Initiative, Movement, Text, OneUse, CardImg, Flavor, PrintID |
| `Frames.csv` | Mecha/frame datasheets | Name, Movement, Weapon Slots, Deck size, Abilities, Top/Side/Low armour, Boosters, CardImg |
| `Terrain_square.csv` | Tactical map tiles | CardImg, Rules, Defend/Attack Points, Tokens, tile_0_0…tile_3_2 (3×4 grid codes) |

**Damage types (DType):** `cut`, `pierce`, `impact`, `projectile`

**Terrain tile codes:** `e1`/`e2`/`e3` (elevation), `im` (impassable), `obs` (obstacle), `obj` (objective), `tkn` (token) — space-separated combinations allowed

## Deck CSVs (`decks/`)

Each file is a single-column list of `card_<Group>_<N>.tex` filenames. One file per faction/frame deck plus `frames_deck.csv` and `terrain_deck.csv` for their respective sheet types.

## Asset Directories

- `pictures/` — card background images (JPEG/PNG, referenced by `CardImg` column)
- `icons/` — mechanic icons (attack types, block, range, initiative, movement, etc.)
- `terrain/` — terrain tile PNG images
- `backs/` — card back designs
- `build/` — all generated output (LaTeX, PDF, aux files) — do not edit by hand

## LaTeX / TikZ Notes

Cards are rendered entirely with TikZ. Each card `.tex` file is standalone and `\input{}`-ed into the master file. Coordinates are in centimeters; card size is fixed in `generateCards.py` at the top of the file.
