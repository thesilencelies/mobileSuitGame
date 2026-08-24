"""The NetFrame playtest AI.

An agent plays a seat through exactly the command interface a human uses. It
is handed `view_for(state, seat)` -- the same redacted dict the web client
gets -- and returns one `Command`. It is never given a `GameState`, so it
cannot read the opponent's hand, deck order or face-down commitments: that
information is not in its inputs at all.

    from playtest.ai import Agent, params_from_dict
    from playtest.engine import catalogue_json, load_cards, view_for

    agent = Agent(seat=1, catalogue=catalogue_json(load_cards()), seed=7)
    command = agent.act(view_for(state, 1))

The server serves the parameter schema for `GET /api/ai/params` from
`playtest.ai.PARAM_SCHEMA` (a plain list of dicts) or, with presets and
defaults included, `playtest.ai.params_schema()`.

`python -m playtest.ai.arena` plays headless AI-vs-AI matches and compares
parameter sets; that is how the defaults were tuned.
"""

from .agent import Agent
from .baseline import GreedyAgent, RandomAgent
from .params import (
    DEFAULT_PRESET,
    PARAM_SCHEMA,
    PRESETS,
    AIParams,
    params_from_dict,
    params_schema,
    preset,
)

__all__ = [
    "Agent",
    "RandomAgent",
    "GreedyAgent",
    "AIParams",
    "PARAM_SCHEMA",
    "PRESETS",
    "DEFAULT_PRESET",
    "params_from_dict",
    "params_schema",
    "preset",
]
