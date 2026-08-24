"""NetFrame playtest server (workstream C) -- standard library only.

    python -m playtest.server        # http://127.0.0.1:8000/, this device only

The whole HTTP layer is `http.server` plus `json`: `playtest/engine/` and
`playtest/ai/` are pure standard library, and keeping this layer stdlib too
means installing the app on an Android phone is `pkg install python` and
nothing else -- no pip, no wheels, no compiling pydantic-core from Rust.

`Router` (in `routes.py`) is the routing table; `httpd.py` is the plumbing.
`playtest.server.app` is an *optional* FastAPI adapter over the same `Router`,
for desktop development only.

Everything is imported lazily, so importing this package costs nothing and
FastAPI never becomes a requirement.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "Router", "serve", "make_server", "BackgroundServer",
    "Registry", "REGISTRY", "create_app", "app",
]

_LAZY = {
    "Router": ("routes", "Router"),
    "serve": ("httpd", "serve"),
    "make_server": ("httpd", "make_server"),
    "BackgroundServer": ("httpd", "BackgroundServer"),
    "Registry": ("games", "Registry"),
    "REGISTRY": ("games", "REGISTRY"),
    "create_app": ("app", "create_app"),      # needs FastAPI
    "app": ("app", "app"),                    # needs FastAPI
}


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute = target
    from importlib import import_module

    module = import_module(f".{module_name}", __name__)
    return getattr(module, attribute)


def __dir__() -> list[str]:
    return sorted(__all__)
