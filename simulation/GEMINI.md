# Combat Simulation Guidelines & Rules

Directory-specific rules for `simulation/`.

## Scope and Model Contract
`simulation/simulate.py` is a **balance abstraction** for testing card stats against each other in focused combat to destruction (HP 4 per zone, hand 2, intelligent pool 7 play).
- **Intentionally ignored**: Range, movement, terrain, line of sight, frame abilities, damage types, and card ability text.
- **Do not import into `playtest/`**: The simulation is a design and balance instrument, not the game engine.
- Key mechanics modelled: highest initiative resolves first, compulsory zone blocking, spent-card free blocks vs unspent-card sacrificial blocks, reload duds, and pilot implicit High block.

## Associated Skills
- **`weapon-power`** (`.agents/skills/weapon-power/SKILL.md`): Assess weapon group power across four analyses (feature estimate, offence vs blocking isolation, marginal contribution, pairings).
- **`weapon-balance`** (`.agents/skills/weapon-balance/SKILL.md`): Static audit for dominated or redundant cards via `weapon_dominance.py`.

## Core Commands
```bash
# Run simulator matches
python simulation/simulate.py match --team-a only_attack_high.csv --team-b only_block_mid.csv
python simulation/simulate.py scale --deck-a even_mix.csv --deck-b only_attack_high.csv
python simulation/simulate.py tournament --sim --sizes 1 2 3 --output build/tournament.html

# Weapon balance suite
python simulation/weapon_power.py
python simulation/weapon_quality_test.py
python simulation/weapon_contribution.py
python simulation/weapon_pairs.py --team-size 2

# Model tests
python simulation/test_simulate.py
```
