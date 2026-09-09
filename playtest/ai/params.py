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
    # How much a hit is worth for *finishing* a zone rather than for the marks
    # it puts on it. Only kills and objectives are victory points -- damage
    # that never converts scores nothing -- so this ramps an attack's value
    # with how much of the zone's remaining armour it takes off. 0 restores
    # the flat "a mark is a mark" reading the scorer started with.
    lethality: float = 0.0
    # What an attack is still worth when the frame cannot reach anything to
    # point it at this turn. Not zero: the card can still block, and there is
    # a next turn. This is the term that decides whether the AI commits a
    # melee weapon with the enemy nine tiles away.
    reach: float = 0.15
    # Value that arrives on a *later* turn: a summoned drone that attacks for
    # free every turn, an extra action, a second hand. The scorer prices what
    # a card does now and nothing else, and two games a human won say that is
    # the largest thing it is missing on offence -- 7 such cards to the AI's 0
    # in the 8-0, and the AI rating a permanent free attacker as the worst card
    # in its hand. See the tempo term in `scoring.score_hand`.
    #
    # Shipped at 1.0 on a small but twice-replicated margin (62.2% -> 64.7%
    # over 720 panel games, and 63.6% -> 64.5% over 504 on another seed; the
    # VP gain, +0.06, is inside the error bars). The margin is small partly
    # because the panel can barely measure it: the four squads hold between
    # one and five of these cards in sixty, and two of them hold no
    # action-economy card at all. Three human wins in a row committed 7, 5 and
    # 7 such cards against the AI's 0, 5 and 1 -- and the one game where the
    # AI matched the human on them (5-5) is the one it nearly won.
    tempo: float = 1.0
    # How small a chance of a frame being destroyed *outright*, by one card
    # from full health on a zone, is still worth covering. See
    # `scoring.threat_profile`: read against two games a human won 8-0 and
    # 4-2, every one of the four frames that died died exactly that way, and
    # on the zone that killed them the old test did not fire at all.
    #
    # Tuned to the top of its range, which is the finding and not a shrug: the
    # sweep is monotone (0.3 and 0.55 never fire at all, 0.9 is worth +0.07 VP,
    # 1.0 is worth +0.32) and 1.0 means "guard a zone if anything in their pool
    # can take it out in one hit". Given that is how every frame died, the
    # model was simply not paranoid enough. Worth +5.3 points of score rate and
    # +0.45 VP a game over 840 panel games. Kept as a range because the
    # difficulty presets want to turn it off, not because 0.5 is useful.
    caution: float = 1.0
    # What the compulsory block is expected to cost this hand. Blocking is not
    # optional, so committing a big slow attack alongside nothing cheaper to
    # cover the same zone is committing it to be eaten before it swings. 0 is
    # the old behaviour, which only ever rewarded covering a zone and never
    # asked with what.
    bait: float = 0.0

    # -- the board ----------------------------------------------------------
    # Objectives are about half the victory points on offer and a kill is one,
    # yet a kill was priced at `scoring.KILL_VALUE` (7 damage marks) while
    # standing on a 3-point objective came out at 4.2 -- the two halves of the
    # scoreboard were never in the same units. Rather than restate every
    # objective in kill-units, the weight carries the correction: arena sweeps
    # walk up monotonically from 0.5 (45.1%) through 1.0 (55.5%) to 2.5, and
    # flatten past 3. Worth +0.86 VP a game on its own -- the single largest
    # effect of anything in this file.
    objective_weight: float = 2.5
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
    # Standing where a committed attack can actually be *delivered* this turn,
    # as a step rather than as a gradient. The approach term already slopes
    # toward the enemy, but a slope is a small number next to the exposure and
    # standoff terms, and the arena found frames stopping one tile short of
    # contact with the tile they wanted in the list. This pays for the last
    # step in.
    contact: float = 0.0
    # Per-tile discount on an attack the frame cannot make yet: three tiles
    # short is worth `approach_falloff ** 3` of the same attack landed. Lower
    # is more short-sighted, higher walks further for a future hit.
    # 0.72 walked frames a long way toward a hit they could not make this turn,
    # and the arena is clear that this is a mistake: `approach=2.0` is the worst
    # single setting measured anywhere (36.5%, -1.03 VP). Five turns is not
    # long enough to spend one walking, and the ground is worth more than the
    # swing.
    # How far the squad commits to an objective *plan* rather than being
    # pulled toward every objective at once: one objective per frame, best
    # first, and objectives nobody can reach or that an enemy is already
    # standing on dropped rather than chased. 0 restores the smooth gradient,
    # which cannot say no and cannot divide the work. See
    # `scoring.ObjectivePlan`.
    #
    # Off by default, which is the measurement and not a lack of nerve. Over
    # two seeds and three objective mixes the plan is **arena-neutral**: the
    # only effect that replicated is that full commitment *hurts* on
    # `control` (-0.17 VP, -0.045 at 0.75), where objectives are contested
    # ground and a frame hovering between two of them takes whichever is
    # still free at the end. It looked strongly positive on `siege` on one
    # seed (+0.19) and that did not survive the second, and the hope that it
    # would at least improve deployment did not survive measurement either.
    #
    # It ships because it does what it is supposed to do -- one objective per
    # frame, unreachable and occupied ground dropped, all covered by tests --
    # and because it is the structure any better answer needs. It does not
    # ship *on*, because nothing yet shows it winning. `planning=1` turns it
    # fully on.
    planning: float = 0.0
    # How much a *damaged* frame stops trading and goes to stand on ground.
    # A frame near death loses the fight it is in and hands over a victory
    # point for losing it, while an objective it is standing on is one it is
    # still holding when the game is counted. Scales both what ground is worth
    # to that frame and its claim on an objective in the plan.
    retreat: float = 0.0
    # How much a drone or an objective token is worth shooting, against a
    # frame. Tokens cannot be killed for a victory point, and a frame that
    # walks across the board to swat one arrives alone.
    token_greed: float = 1.0
    approach_falloff: float = 0.55
    # How sharply objective value ramps toward the last turn. The end-of-game
    # objectives are scored once, after turn 5, so what standing on one is
    # worth is really "will I still be here then"; >1 discounts the early
    # turns harder, <1 chases them from the start.
    endgame: float = 1.0

    # -- policy -------------------------------------------------------------
    pool: int = 7
    temperature: float = 0.4
    # Movement is far less forgiving than card choice -- a tile one step wrong
    # is an attack that does not happen -- so it has always been sampled far
    # colder than the headline temperature. It is its own number now so it can
    # be tuned on its own; 0 always takes the top-scoring tile.
    move_temperature: float = 0.1

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
        "name": "lethality",
        "label": "Killer instinct",
        "type": "float",
        "min": 0.0,
        "max": 4.0,
        "step": 0.05,
        "default": AIParams.lethality,
        "help": "How much more a hit is worth for finishing a zone than for merely marking it.",
    },
    {
        "name": "reach",
        "label": "Wasted actions",
        "type": "float",
        "min": 0.0,
        "max": 1.0,
        "step": 0.05,
        "default": AIParams.reach,
        "help": "What an attack is still worth with nothing in range; 0 refuses to commit one.",
    },
    {
        "name": "tempo",
        "label": "Playing for later",
        "type": "float",
        "min": 0.0,
        "max": 3.0,
        "step": 0.05,
        "default": AIParams.tempo,
        "help": "Worth of drones, extra actions and other cards that pay off on a later turn.",
    },
    {
        "name": "caution",
        "label": "Fear of the one-shot",
        "type": "float",
        "min": 0.0,
        "max": 1.0,
        "step": 0.05,
        "default": AIParams.caution,
        "help": "How small a chance of losing a frame to a single hit is still worth guarding.",
    },
    {
        "name": "bait",
        "label": "Block discipline",
        "type": "float",
        "min": 0.0,
        "max": 4.0,
        "step": 0.05,
        "default": AIParams.bait,
        "help": "How wary the AI is of committing a big attack that a forced block will eat first.",
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
        "name": "contact",
        "label": "Close the gap",
        "type": "float",
        "min": 0.0,
        "max": 6.0,
        "step": 0.1,
        "default": AIParams.contact,
        "help": "Reward for ending a move somewhere a committed attack can actually be delivered.",
    },
    {
        "name": "planning",
        "label": "Objective planning",
        "type": "float",
        "min": 0.0,
        "max": 1.0,
        "step": 0.05,
        "default": AIParams.planning,
        "help": "How firmly each frame commits to one objective instead of drifting toward all of them.",
    },
    {
        "name": "retreat",
        "label": "Break off when hurt",
        "type": "float",
        "min": 0.0,
        "max": 3.0,
        "step": 0.05,
        "default": AIParams.retreat,
        "help": "How readily a damaged frame stops trading hits and goes to stand on an objective.",
    },
    {
        "name": "token_greed",
        "label": "Token hunting",
        "type": "float",
        "min": 0.0,
        "max": 2.0,
        "step": 0.05,
        "default": AIParams.token_greed,
        "help": "How much a drone or objective token is worth chasing, against attacking a frame.",
    },
    {
        "name": "approach_falloff",
        "label": "Patience",
        "type": "float",
        "min": 0.3,
        "max": 0.95,
        "step": 0.01,
        "default": AIParams.approach_falloff,
        "help": "How much of an attack's worth survives each tile it is still short by.",
    },
    {
        "name": "endgame",
        "label": "Endgame timing",
        "type": "float",
        "min": 0.2,
        "max": 4.0,
        "step": 0.1,
        "default": AIParams.endgame,
        "help": "How hard objectives are discounted early; high means grab them on the last turns.",
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
        "name": "move_temperature",
        "label": "Movement randomness",
        "type": "float",
        "min": 0.0,
        "max": 3.0,
        "step": 0.02,
        "default": AIParams.move_temperature,
        "help": "Softmax spread when choosing a tile; 0 always walks to the best one.",
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
]

#: Difficulty presets. Values are partial -- anything absent keeps its default.
PRESETS: dict[str, dict[str, Any]] = {
    "beginner": {
        # Weakness lives in temperature and the narrow search -- not in
        # miscalibrated weights, which would just make it play a different
        # (and confusingly plausible) style.
        #
        # It used to live partly in a `blunder_rate` that threw decisions away
        # at random. That is gone: as a slider in a scrolling drawer it was
        # too easy to nudge by accident, and a game against an AI silently set
        # to discard 85% of its decisions is not a game -- it is dice, and it
        # looks exactly like an AI that has forgotten how to play. One did,
        # and the evidence took a while to unpick.
        #
        # `move_temperature` is spelled out because it used to be derived
        # (`temperature * 0.35`), so this preset got its wandering movement
        # for free from `temperature: 4.0`. Splitting the two would otherwise
        # have quietly handed the beginner the tuned agent's movement, which
        # is most of what movement is worth.
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
        "move_temperature": 1.4,
        # A beginner does not see the one-shot coming, and does not play for
        # next turn either. These two are the largest things separating it
        # from `standard`, so they belong here rather than in more shaved
        # weights.
        "caution": 0.0,
        "tempo": 0.0,
    },
    "standard": {},
    "veteran": {
        # Much shorter than it used to be, and deliberately so. The old
        # veteran nudged eleven weights up and beat `standard` 41% of the
        # time -- it was a different style, not a stronger player, and the
        # sweeps that produced it were reading noise. Retuning `standard`
        # made that worse: it now sits at the top of what the weights can
        # do, and every candidate veteran tried against it landed inside the
        # error bars (57.7% to 62.4% over 384 games, on a 59.6% control).
        #
        # So this is only the three things that are not weights -- a colder
        # policy, a wider search and longer to run it -- plus `lethality`,
        # the one term that trended up on its own. It beats the retuned
        # standard 53.8% over 144 games, which is a real step but a small
        # one, and it is honest about that: the remaining headroom is in
        # what the evaluator can see, not in what its terms are multiplied
        # by. `pool` is already at the whole hand and `objective_weight`
        # already at its plateau, so neither appears here.
        "defense": 1.2,
        "focus_fire": 0.7,
        "lethality": 1.0,
        "temperature": 0.3,
        "move_temperature": 0.08,
        "search_width": 96,
        "think_ms": 1500,
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
