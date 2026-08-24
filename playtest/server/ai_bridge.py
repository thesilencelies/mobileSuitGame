"""Adapter between the server and workstream D's AI package.

Workstream D owns `playtest/ai/`. It is being written concurrently, so this
module never imports it at module scope and never assumes a particular name:
it probes for a handful of plausible entry points, normalises whatever
parameter schema it finds into the shape `GET /api/ai/params` promises, and
falls back to a small built-in agent if the package is not importable yet.

The built-in agent is deliberately unambitious -- it exists so the game can be
played end to end before D lands, and it is reported to the client as
`"source": "fallback"` so nobody mistakes it for the real thing.

Both the probe and the fallback act only on what a seat is entitled to see:
`view_for(state, seat)` for the board and `legal_commands(state, seat)` for the
options. Nothing here reaches into the opponent's hand.
"""

from __future__ import annotations

import importlib
import random
from dataclasses import fields, is_dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

from ..engine import (
    Command,
    GameState,
    catalogue_json,
    legal_commands,
    load_cards,
    view_for,
)

AI_MODULE = "playtest.ai"

#: Attribute names we will accept for "the parameter schema".
_SCHEMA_ATTRS = (
    "param_schema", "parameter_schema", "params_schema", "schema",
    "PARAM_SCHEMA", "PARAMETER_SCHEMA", "PARAMS_SCHEMA", "SCHEMA", "PARAMS",
)
#: Attribute names we will accept for "difficulty presets".
_PRESET_ATTRS = ("presets", "PRESETS", "difficulty_presets", "DIFFICULTY_PRESETS")
#: Attribute names we will accept for "give me an agent".
_AGENT_ATTRS = (
    "make_agent", "build_agent", "create_agent", "agent_for", "get_agent",
    "Agent", "AIAgent", "NetFrameAI",
)
#: Attribute names we will accept for "pick a command" on a module or an agent.
_CHOOSE_ATTRS = (
    "choose_command", "choose", "decide", "act", "select_command", "command_for",
)
#: Submodules worth probing when the top-level package exports nothing useful.
_SUBMODULES = ("params", "parameters", "agent", "ai", "tuning")


# --------------------------------------------------------------------------
# Probing workstream D
# --------------------------------------------------------------------------


def _import(name: str) -> Optional[Any]:
    try:
        return importlib.import_module(name)
    except Exception:                       # ImportError, SyntaxError, anything
        return None


def _candidates() -> list[Any]:
    """The AI package and its plausible submodules, best first."""
    root = _import(AI_MODULE)
    if root is None:
        return []
    out = [root]
    for sub in _SUBMODULES:
        mod = _import(f"{AI_MODULE}.{sub}")
        if mod is not None:
            out.append(mod)
    return out


def _first_attr(objs: Sequence[Any], names: Sequence[str]) -> Optional[Any]:
    for obj in objs:
        for name in names:
            value = getattr(obj, name, None)
            if value is not None:
                return value
    return None


# --------------------------------------------------------------------------
# Parameter schema normalisation
# --------------------------------------------------------------------------

_NAME_KEYS = ("name", "key", "id", "param")
_LABEL_KEYS = ("label", "title", "display", "display_name")
_HELP_KEYS = ("help", "description", "doc", "docstring", "tooltip", "blurb")
_MIN_KEYS = ("min", "minimum", "lo", "low")
_MAX_KEYS = ("max", "maximum", "hi", "high")
_DEFAULT_KEYS = ("default", "value", "initial")
_CHOICE_KEYS = ("options", "choices", "values", "enum")


def _pick(entry: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in entry and entry[key] is not None:
            return entry[key]
    return None


def _titlecase(name: str) -> str:
    return name.replace("_", " ").replace("-", " ").strip().title()


def _infer_type(default: Any, lo: Any, hi: Any, choices: Any) -> str:
    if choices:
        return "choice"
    if isinstance(default, bool):
        return "bool"
    if isinstance(default, str):
        return "text"
    values = [v for v in (default, lo, hi) if v is not None]
    if values and all(isinstance(v, int) and not isinstance(v, bool) for v in values):
        return "int"
    return "float"


def _normalise_entry(name: str, entry: Any) -> Optional[dict[str, Any]]:
    """One parameter, in the shape the client renders."""
    if not isinstance(entry, Mapping):
        # A bare default value: `{"aggression": 1.0}`.
        entry = {"default": entry}
    name = str(_pick(entry, _NAME_KEYS) or name or "").strip()
    if not name:
        return None
    lo = _pick(entry, _MIN_KEYS)
    hi = _pick(entry, _MAX_KEYS)
    default = _pick(entry, _DEFAULT_KEYS)
    choices = _pick(entry, _CHOICE_KEYS)
    kind = str(entry.get("type") or "").strip() or _infer_type(default, lo, hi, choices)
    out: dict[str, Any] = {
        "name": name,
        "label": str(_pick(entry, _LABEL_KEYS) or _titlecase(name)),
        "type": kind,
        "help": str(_pick(entry, _HELP_KEYS) or ""),
        "default": default,
    }
    if lo is not None:
        out["min"] = lo
    if hi is not None:
        out["max"] = hi
    if choices:
        out["options"] = list(choices)
    step = entry.get("step")
    if step is None and kind == "float":
        span = None
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
            span = float(hi) - float(lo)
        step = round(span / 100, 4) if span else 0.05
    if step is None and kind == "int":
        step = 1
    if step is not None:
        out["step"] = step
    for extra in ("group", "advanced", "unit"):
        if extra in entry:
            out[extra] = entry[extra]
    return out


def _normalise_schema(raw: Any) -> list[dict[str, Any]]:
    """Accept a list, a mapping, a dataclass or a dataclass instance."""
    if raw is None:
        return []
    if callable(raw) and not isinstance(raw, type):
        try:
            raw = raw()
        except Exception:
            return []
    if is_dataclass(raw):
        instance = raw if not isinstance(raw, type) else None
        if instance is None:
            try:
                instance = raw()
            except Exception:
                instance = None
        entries = []
        for field in fields(raw):
            meta = dict(field.metadata or {})
            if instance is not None:
                meta.setdefault("default", getattr(instance, field.name, None))
            entries.append((field.name, meta))
        return [e for e in (_normalise_entry(n, m) for n, m in entries) if e]
    if isinstance(raw, Mapping):
        # A whole `GET /api/ai/params` payload -- `{"params": [...], ...}`.
        for wrapper in ("params", "parameters", "schema", "entries"):
            inner = raw.get(wrapper)
            if isinstance(inner, Sequence) and not isinstance(inner, (str, bytes)):
                return _normalise_schema(inner)
        return [
            e for e in
            (_normalise_entry(str(k), v) for k, v in raw.items())
            if e
        ]
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        out = []
        for item in raw:
            entry = _normalise_entry("", item) if isinstance(item, Mapping) else None
            if entry:
                out.append(entry)
        return out
    return []


#: What the app offers when workstream D has not landed. Names match the
#: minimum set SPEC.md asks D for, so the UI looks the same either way.
FALLBACK_SCHEMA: list[dict[str, Any]] = [
    {"name": "aggression", "label": "Aggression", "type": "float", "min": 0.0,
     "max": 2.0, "step": 0.05, "default": 1.0,
     "help": "How much the AI values dealing damage over staying safe."},
    {"name": "defense", "label": "Defence", "type": "float", "min": 0.0,
     "max": 2.0, "step": 0.05, "default": 1.0,
     "help": "How much the AI values keeping blocks in hand for the zones it expects."},
    {"name": "concentration", "label": "Concentration", "type": "float", "min": 0.0,
     "max": 2.0, "step": 0.05, "default": 0.5,
     "help": "Preference for stacking damage into one zone rather than spreading it."},
    {"name": "objective_weight", "label": "Objectives", "type": "float", "min": 0.0,
     "max": 3.0, "step": 0.05, "default": 1.0,
     "help": "How strongly objectives pull the AI's movement."},
    {"name": "focus_fire", "label": "Focus fire", "type": "float", "min": 0.0,
     "max": 2.0, "step": 0.05, "default": 1.0,
     "help": "Preference for finishing a damaged frame over spreading attacks."},
    {"name": "pool", "label": "Search pool", "type": "int", "min": 1, "max": 20,
     "step": 1, "default": 7,
     "help": "How many candidate plans the AI scores before choosing."},
    {"name": "temperature", "label": "Randomness", "type": "float", "min": 0.0,
     "max": 1.0, "step": 0.02, "default": 0.1,
     "help": "0 plays the top-scoring option every time; higher mixes it up."},
]


def param_schema() -> dict[str, Any]:
    """`GET /api/ai/params` -- always a usable schema, D landed or not."""
    mods = _candidates()
    raw = _first_attr(mods, _SCHEMA_ATTRS)
    params = _normalise_schema(raw)
    presets_raw = _first_attr(mods, _PRESET_ATTRS)
    if callable(presets_raw) and not isinstance(presets_raw, type):
        try:
            presets_raw = presets_raw()
        except Exception:
            presets_raw = None
    presets = presets_raw if isinstance(presets_raw, Mapping) else {}
    if params:
        return {
            "source": AI_MODULE,
            "available": True,
            "params": params,
            "presets": {str(k): dict(v) for k, v in presets.items()
                        if isinstance(v, Mapping)},
        }
    return {
        "source": "fallback",
        "available": bool(mods),
        "note": (
            "playtest.ai has not published a parameter schema yet; these are the "
            "server's placeholder parameters and drive the built-in stand-in AI."
        ),
        "params": [dict(p) for p in FALLBACK_SCHEMA],
        "presets": {
            "gentle": {"aggression": 0.6, "temperature": 0.35, "pool": 4},
            "standard": {"aggression": 1.0, "temperature": 0.1, "pool": 7},
            "ruthless": {"aggression": 1.4, "temperature": 0.0, "pool": 14},
        },
    }


def defaults() -> dict[str, Any]:
    return {
        p["name"]: p.get("default")
        for p in param_schema()["params"]
        if p.get("default") is not None
    }


# --------------------------------------------------------------------------
# Agents
# --------------------------------------------------------------------------


class Agent:
    """What the server needs from an AI: one command for one pending decision."""

    source = "fallback"

    def choose(self, state: GameState, seat: int) -> Optional[Command]:
        raise NotImplementedError


class _DelegatingAgent(Agent):
    """Wraps workstream D's agent behind `choose(state, seat)`.

    D's `Agent.act(view)` takes the *redacted view*, not the `GameState` --
    which is the stronger contract, so it is tried first. A `choose(state,
    seat)`-shaped agent also works, for whatever D does next.
    """

    def __init__(self, inner: Any, source: str) -> None:
        self._inner = inner
        self.source = source
        self._act: Optional[Callable[..., Any]] = None
        self._call: Optional[Callable[..., Any]] = None
        act = getattr(inner, "act", None)
        if callable(act):
            self._act = act
        for name in _CHOOSE_ATTRS:
            fn = getattr(inner, name, None)
            if callable(fn):
                self._call = fn
                break
        if self._act is None and self._call is None and callable(inner):
            self._call = inner

    @property
    def usable(self) -> bool:
        return self._act is not None or self._call is not None

    def choose(self, state: GameState, seat: int) -> Optional[Command]:
        if self._act is not None:
            # The view is all it gets: no hand, no deck order, no face-down keys.
            result = self._act(view_for(state, seat))
        elif self._call is not None:
            try:
                result = self._call(state, seat)
            except TypeError:
                result = self._call(state)
        else:
            return None
        return _as_command(result, state, seat)


def _as_command(result: Any, state: GameState, seat: int) -> Optional[Command]:
    if isinstance(result, Command):
        return result
    if isinstance(result, Mapping):              # a plain payload dict
        pending = state.pending
        kind = str(result.get("kind") or (pending.kind if pending else ""))
        payload = dict(result.get("payload") or
                       {k: v for k, v in result.items()
                        if k not in ("kind", "seat")})
        return Command(kind, seat, payload)
    return None


def _construct(factory: Any, *, seat: int, catalogue: Any, params: Mapping[str, Any],
               seed: int) -> Optional[Any]:
    """Call `factory` with whatever subset of our arguments it accepts."""
    import inspect

    pool = {"seat": seat, "catalogue": catalogue, "cards": catalogue,
            "params": params, "seed": seed}
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        signature = None
    attempts: list[tuple[tuple, dict]] = []
    if signature is not None:
        kwargs = {
            name: pool[name] for name in signature.parameters
            if name in pool
        }
        attempts.append(((), kwargs))
    attempts += [
        ((seat, catalogue, params), {}),
        ((seat, catalogue), {}),
        ((params,), {}),
        ((), {}),
    ]
    for args, kwargs in attempts:
        try:
            return factory(*args, **kwargs)
        except Exception:
            continue
    return None


def make_agent(
    params: Optional[Mapping[str, Any]] = None,
    *,
    seat: int = 1,
    catalogue: Optional[Mapping[str, Any]] = None,
    seed: int = 0,
) -> Agent:
    """Workstream D's agent if it exists, otherwise the built-in stand-in."""
    params = dict(params or {})
    mods = _candidates()
    if catalogue is None:
        catalogue = json_catalogue()
    for mod in mods:
        for name in _AGENT_ATTRS:
            factory = getattr(mod, name, None)
            if factory is None:
                continue
            inner = _construct(
                factory, seat=seat, catalogue=catalogue, params=params, seed=seed
            )
            if inner is None:
                continue
            agent = _DelegatingAgent(inner, AI_MODULE)
            if agent.usable:
                return agent
    # A module-level `choose_command(state, seat)` with no agent object.
    fn = _first_attr(mods, _CHOOSE_ATTRS)
    if callable(fn):
        agent = _DelegatingAgent(fn, AI_MODULE)
        if agent.usable:
            return agent
    return HeuristicAgent(params)


# --------------------------------------------------------------------------
# The built-in stand-in
# --------------------------------------------------------------------------

_CATALOGUE: Optional[Mapping[str, Any]] = None
_JSON_CATALOGUE: Optional[Mapping[str, Any]] = None


def _catalogue() -> Mapping[str, Any]:
    global _CATALOGUE
    if _CATALOGUE is None:
        _CATALOGUE = load_cards()
    return _CATALOGUE


def json_catalogue() -> Mapping[str, Any]:
    """The catalogue as plain dicts -- what D's `Agent` wants handed to it."""
    global _JSON_CATALOGUE
    if _JSON_CATALOGUE is None:
        _JSON_CATALOGUE = catalogue_json(_catalogue())
    return _JSON_CATALOGUE


class HeuristicAgent(Agent):
    """A small greedy agent, so a game is playable before workstream D lands.

    It reads the board through `view_for(state, seat)` and its options through
    `legal_commands(state, seat)`, which is exactly what a human client sees --
    so it structurally cannot peek at the opponent's hand.
    """

    source = "fallback"

    def __init__(self, params: Optional[Mapping[str, Any]] = None) -> None:
        merged = dict(defaults())
        merged.update(params or {})
        self.params = merged
        self.rng = random.Random(int(merged.get("seed") or 0) or None)

    # -- helpers ---------------------------------------------------------

    def _card(self, key: str) -> Any:
        return _catalogue().get(key)

    def _card_value(self, key: str) -> float:
        card = self._card(key)
        if card is None:
            return 0.0
        aggression = float(self.params.get("aggression", 1.0) or 1.0)
        defense = float(self.params.get("defense", 1.0) or 1.0)
        attack = sum(card.attacks.values()) * 1.6 * aggression
        block = sum(min(2, v) for v in card.blocks.values()) * 1.2 * defense
        init = (max(card.initiative) if card.initiative else 0) * 0.08
        move = card.movement * 0.15
        return attack + block + init + move

    @staticmethod
    def _distance(a: Mapping[str, int], b: Mapping[str, int]) -> int:
        return max(abs(a["x"] - b["x"]), abs(a["y"] - b["y"]))

    # -- the decision ----------------------------------------------------

    def choose(self, state: GameState, seat: int) -> Optional[Command]:
        options = legal_commands(state, seat)
        if not options:
            return None
        view = view_for(state, seat)
        pending = view.get("pending") or {}
        kind = pending.get("kind")
        scorer = {
            "commit_actions": self._score_commit,
            "resolve_order": self._score_order,
            "move": self._score_move,
            "attack_target": self._score_attack,
            "choose_block": self._score_block,
            "echo_card": self._score_echo,
        }.get(str(kind))
        if scorer is None:
            return options[0]
        scored = [(scorer(cmd, view, pending), i, cmd)
                  for i, cmd in enumerate(options)]
        temperature = float(self.params.get("temperature", 0.0) or 0.0)
        if temperature > 0:
            pool = max(1, int(self.params.get("pool", 7) or 7))
            best = sorted(scored, key=lambda t: -t[0])[:pool]
            weights = [max(1e-6, 1.0 + temperature * self.rng.random()) for _ in best]
            return self.rng.choices([c for _, _, c in best], weights=weights)[0]
        return max(scored, key=lambda t: t[0])[2]

    # -- per-decision scorers -------------------------------------------

    def _keys_for(self, pending: Mapping[str, Any]) -> dict[str, str]:
        return {
            str(o.get("uid")): str(o.get("key", ""))
            for o in pending.get("options", [])
            if o.get("uid")
        }

    def _score_commit(self, cmd, view, pending) -> float:
        keys = self._keys_for(pending)
        uids = [str(u) for u in cmd.payload.get("uids", [])]
        total = sum(self._card_value(keys.get(u, "")) for u in uids)
        cards = [self._card(keys.get(u, "")) for u in uids]
        cards = [c for c in cards if c is not None]
        # A pair that can both attack and block is worth more than two of either.
        if any(c.is_attack for c in cards) and any(c.block_zones for c in cards):
            total += 1.2 * float(self.params.get("defense", 1.0) or 1.0)
        inits = {i for c in cards for i in c.initiative}
        total += 0.2 * len(inits)              # spread across the turn
        return total

    def _score_order(self, cmd, view, pending) -> float:
        order = [str(s) for s in cmd.payload.get("order", [])]
        score = 0.0
        if order and order[0] == "movement":
            score += 2.0                       # close first, then swing
        if order and order[-1] == "attack":
            score += 1.5
        return score

    def _score_move(self, cmd, view, pending) -> float:
        me = next((f for f in view["frames"] if f["id"] == pending.get("frameId")), None)
        if me is None:
            return 0.0
        dest = {"x": int(cmd.payload["x"]), "y": int(cmd.payload["y"])}
        enemies = [f for f in view["frames"]
                   if f["seat"] != view["seat"] and f["alive"] and f["pos"]]
        score = -0.05 * float(cmd.payload.get("cost", 0) or 0)
        if enemies:
            nearest = min(self._distance(dest, f["pos"]) for f in enemies)
            score += 6.0 * float(self.params.get("aggression", 1.0) or 1.0) / (1 + nearest)
            if nearest == 1:
                score += 1.5
        weight = float(self.params.get("objective_weight", 1.0) or 1.0)
        for obj in view["board"].get("objectives", []):
            for tx, ty in obj.get("tiles", []):
                if tx == dest["x"] and ty == dest["y"]:
                    score += 2.0 * weight
        return score

    def _score_attack(self, cmd, view, pending) -> float:
        option = next(
            (o for o in pending.get("options", [])
             if o.get("kind") == cmd.payload.get("kind")
             and o.get("id") == cmd.payload.get("id")),
            {},
        )
        zones = option.get("zones") or {}
        score = float(sum(zones.values())) * 2.0
        if option.get("kind") == "frame":
            target = next((f for f in view["frames"] if f["id"] == option.get("id")), None)
            if target:
                focus = float(self.params.get("focus_fire", 1.0) or 1.0)
                hurt = sum(target["damage"].values())
                score += focus * hurt * 0.8
                for zone, marks in zones.items():
                    left = target["armour"].get(zone, 0) - target["damage"].get(zone, 0)
                    if marks >= left:
                        score += 6.0 * focus       # this kills it
        else:
            score += 1.0 * float(self.params.get("objective_weight", 1.0) or 1.0)
        return score

    def _score_block(self, cmd, view, pending) -> float:
        keys = self._keys_for(pending)
        card = self._card(keys.get(str(cmd.payload.get("uid")), ""))
        if card is None:
            return 0.0
        # Blocking is compulsory; spend the cheapest card, and prefer a super
        # block because it is not discarded.
        score = -self._card_value(card.key)
        if card.super_block_zones:
            score += 8.0
        return score

    def _score_echo(self, cmd, view, pending) -> float:
        return -1.0 if cmd.payload.get("decline") else 1.0
