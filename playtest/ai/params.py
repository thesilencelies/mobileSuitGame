"""Tunable AI parameters and the schema the app serves from `GET /api/ai/params`.

`AIParams` is the dataclass the agent reads. `PARAM_SCHEMA` is a plain list of
dicts -- no engine types, no dataclasses -- so the server can hand it straight
to `json.dumps` and the client can build sliders from it without knowing
anything about this module.

Every entry has `name`, `label`, `min`, `max`, `default` and `help`, plus
`type` and `step` so a slider knows what to draw. Extra keys are additive;
nothing is ever removed, so a client built against the six required keys keeps
working.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any, Mapping


@dataclass(frozen=True)
class AIParams:
    """Weights for the action scorer and the positional evaluator.

    Everything is a plain float/int so a parameter set round-trips through
    JSON unchanged (`AIParams(**payload)` / `asdict`).
    """

    # -- scoring the two committed actions ---------------------------------
    # `defense` and `survival` sit where `simulation/`'s scorer put them, but
    # not for its reasons, and getting here took a wrong turn worth recording.
    # Arena sweeps first said to cut them hard (defense 0.25 scored 60.0% and
    # survival 1.5 scored 55.0% over 60 games each against 1.0/8.0). That was
    # a symptom, not the disease: the opponent profile was taking its "peak
    # hit per zone" as the maximum over the whole faction card pool -- the
    # scariest card in the game, which the opponent probably does not even
    # hold -- so the survival term fired on every zone from turn one and the
    # AI turtled. With that fixed (`scoring.profile(peak_q=...)`), the low
    # weights lose: 46.9% over 80 games and 43.3% over 120 against 1.0/8.0,
    # on a control that reads 48.3/51.7 over 120. Fix the model, keep the
    # weight.
    defense: float = 1.0
    concentration: float = 0.6
    aggression: float = 1.0
    survival: float = 8.0
    focus_fire: float = 0.6

    # -- the board ----------------------------------------------------------
    objective_weight: float = 1.0
    positioning: float = 1.0
    approach: float = 1.0
    elevation: float = 1.5
    # Both re-tuned after line of sight became source-permissive/target-centre.
    # That change removed 17.7% of sight lines and added none, so terrain now
    # breaks ranged threat on its own: paying position to dodge a line of sight
    # that mostly is not there double-counts, and 0.8 was over-cautious
    # (los_caution 0.3 beats 0.8 by 57.2/42.8 over 200 games). The mirror of the
    # same fact is that getting inside the narrower band where you *do* have a
    # line is worth more, so standoff went up (1.6 beats 0.8 by 54.2/45.8).
    los_caution: float = 0.3
    standoff: float = 1.6

    # -- policy -------------------------------------------------------------
    pool: int = 7
    temperature: float = 0.8
    blunder_rate: float = 0.0

    # -- compute budget -----------------------------------------------------
    # This runs on a phone under Termux, so the search is bounded twice over:
    # `search_width` is a deterministic cap on how much work a decision does,
    # and `think_ms` is a wall-clock safety net for hardware slower than the
    # width was tuned for. See the note on determinism in `agent.Agent`.
    search_width: int = 48
    think_ms: int = 400

    # -- housekeeping -------------------------------------------------------

    def replace(self, **changes: Any) -> "AIParams":
        data = asdict(self)
        data.update(changes)
        return AIParams(**data)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


#: The one-line `help` strings the UI shows next to each control.
PARAM_SCHEMA: list[dict[str, Any]] = [
    {
        "name": "defense",
        "label": "Defence",
        "type": "float",
        "min": 0.0,
        "max": 3.0,
        "step": 0.05,
        "default": AIParams.defense,
        "help": "How much the AI values holding blocking cards over attacking.",
    },
    {
        "name": "concentration",
        "label": "Concentration",
        "type": "float",
        "min": 0.0,
        "max": 3.0,
        "step": 0.05,
        "default": AIParams.concentration,
        "help": "Bonus for pointing both of a turn's actions at the same armour zone.",
    },
    {
        "name": "aggression",
        "label": "Aggression",
        "type": "float",
        "min": 0.0,
        "max": 3.0,
        "step": 0.05,
        "default": AIParams.aggression,
        "help": "How hard the AI chases damage and kills rather than playing safe.",
    },
    {
        "name": "survival",
        "label": "Survival instinct",
        "type": "float",
        "min": 0.0,
        "max": 20.0,
        "step": 0.5,
        "default": AIParams.survival,
        "help": "Priority on covering zones where one more hit would destroy the frame.",
    },
    {
        "name": "focus_fire",
        "label": "Focus fire",
        "type": "float",
        "min": 0.0,
        "max": 3.0,
        "step": 0.05,
        "default": AIParams.focus_fire,
        "help": "How hard the whole squad converges on one enemy each turn to strip its blocks and kill it.",
    },
    {
        "name": "objective_weight",
        "label": "Objectives",
        "type": "float",
        "min": 0.0,
        "max": 4.0,
        "step": 0.05,
        "default": AIParams.objective_weight,
        "help": "How far the AI will go out of its way to hold or contest objectives.",
    },
    {
        "name": "positioning",
        "label": "Positioning",
        "type": "float",
        "min": 0.0,
        "max": 3.0,
        "step": 0.05,
        "default": AIParams.positioning,
        "help": "Weight on movement quality: closing, kiting and denying good ground.",
    },
    {
        "name": "approach",
        "label": "Closing",
        "type": "float",
        "min": 0.0,
        "max": 3.0,
        "step": 0.05,
        "default": AIParams.approach,
        "help": "Drive to close the gap and bring committed weapons into range; 0 makes it passive.",
    },
    {
        "name": "elevation",
        "label": "Elevation play",
        "type": "float",
        "min": 0.0,
        "max": 3.0,
        "step": 0.05,
        "default": AIParams.elevation,
        "help": "Effort spent taking high ground to shift melee attacks past enemy blocks.",
    },
    {
        "name": "los_caution",
        "label": "Cover discipline",
        "type": "float",
        "min": 0.0,
        "max": 3.0,
        "step": 0.05,
        "default": AIParams.los_caution,
        "help": "How much the AI avoids standing in the line of sight of ranged enemies.",
    },
    {
        "name": "standoff",
        "label": "Standoff",
        "type": "float",
        "min": 0.0,
        "max": 3.0,
        "step": 0.05,
        "default": AIParams.standoff,
        "help": "How firmly ranged frames keep their distance instead of closing.",
    },
    {
        "name": "pool",
        "label": "Cards considered",
        "type": "int",
        "min": 2,
        "max": 7,
        "step": 1,
        "default": AIParams.pool,
        "help": "How many cards of the drawn hand are considered when committing actions.",
    },
    {
        "name": "temperature",
        "label": "Randomness",
        "type": "float",
        "min": 0.0,
        "max": 6.0,
        "step": 0.1,
        "default": AIParams.temperature,
        "help": "Softmax spread on action choice; 0 always plays the top-scoring pair.",
    },
    {
        "name": "search_width",
        "label": "Search width",
        "type": "int",
        "min": 12,
        "max": 120,
        "step": 4,
        "default": AIParams.search_width,
        "help": "Candidate destinations scored per move; lower thinks faster on a phone.",
    },
    {
        "name": "think_ms",
        "label": "Think time (ms)",
        "type": "int",
        "min": 0,
        "max": 5000,
        "step": 50,
        "default": AIParams.think_ms,
        "help": "Wall-clock ceiling per decision; the AI narrows its search rather than stalling. 0 = no limit.",
    },
    {
        "name": "blunder_rate",
        "label": "Blunder rate",
        "type": "float",
        "min": 0.0,
        "max": 1.0,
        "step": 0.05,
        "default": AIParams.blunder_rate,
        "help": "Chance of throwing a decision away at random, used to make easy modes easy.",
    },
]

#: Difficulty presets. Values are partial -- anything absent keeps its default.
PRESETS: dict[str, dict[str, Any]] = {
    "beginner": {
        # Weakness lives in blunder_rate, temperature and the narrow search --
        # not in miscalibrated weights, which would just make it play a
        # different (and confusingly plausible) style.
        "objective_weight": 0.4,
        "positioning": 0.4,
        "approach": 0.5,
        "elevation": 0.2,
        "los_caution": 0.2,
        "focus_fire": 0.2,
        "pool": 4,
        "search_width": 20,
        "think_ms": 250,
        "temperature": 4.0,
        "blunder_rate": 0.30,
    },
    "standard": {},
    "veteran": {
        "defense": 1.15,
        "concentration": 0.7,
        "aggression": 1.1,
        "survival": 9.0,
        "focus_fire": 0.8,
        "objective_weight": 1.3,
        "positioning": 1.2,
        "approach": 1.2,
        "elevation": 2.0,
        "los_caution": 0.35,
        "standoff": 1.8,
        "pool": 7,
        "search_width": 96,
        "think_ms": 1500,
        "temperature": 0.25,
        "blunder_rate": 0.0,
    },
}

DEFAULT_PRESET = "standard"


def preset(name: str) -> AIParams:
    """`AIParams` for a difficulty preset name (unknown names -> defaults)."""
    return AIParams().replace(**PRESETS.get(name, {}))


def params_from_dict(payload: Mapping[str, Any] | None) -> AIParams:
    """Build `AIParams` from a loose dict (an HTTP body, say).

    Unknown keys are ignored, values are coerced to the field's type, and
    a `"preset"` key is applied first so a client can send
    `{"preset": "veteran", "aggression": 2.0}`.
    """
    payload = dict(payload or {})
    params = preset(str(payload.pop("preset", DEFAULT_PRESET)))
    types = {f.name: f.type for f in fields(AIParams)}
    changes: dict[str, Any] = {}
    for key, value in payload.items():
        if key not in types:
            continue
        try:
            changes[key] = int(value) if types[key] == "int" else float(value)
        except (TypeError, ValueError):
            continue
    return params.replace(**changes)


def params_schema() -> dict[str, Any]:
    """The whole payload for `GET /api/ai/params`, JSON-ready."""
    return {
        "params": [dict(entry) for entry in PARAM_SCHEMA],
        "presets": {name: dict(values) for name, values in PRESETS.items()},
        "defaultPreset": DEFAULT_PRESET,
        "defaults": AIParams().to_dict(),
    }
