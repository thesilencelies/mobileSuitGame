"""The whole HTTP API, with no web framework underneath it.

The app has to run on the phone itself, under Termux, with `pkg install python`
and nothing else. FastAPI would drag in pydantic, whose core is a compiled Rust
extension with no Android wheel -- so the shipped path is `http.server` plus
`json`, and this module is the routing table both that and the optional FastAPI
adapter share.

`Router.dispatch()` is a pure function of (method, path, query, body) -> the
bytes to send back. That makes it the natural place for the tests to drive the
API without opening a socket, and it keeps the plumbing in `httpd.py` down to
reading a request and writing a response.

Every game response goes through `Session.view()`, which goes through the
engine's `view_for(state, seat)`. Nothing here serialises a `GameState`, so
nothing here can leak the AI's face-down cards.
"""

from __future__ import annotations

import json
import mimetypes
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Pattern

from ..engine import (
    IllegalCommand,
    Pos,
    available_decks,
    catalogue_json,
    load_cards,
    load_frames,
    validate_all_decks,
)
from . import ai_bridge, assets, images
from .games import REGISTRY, GameNotFound, Registry, Session, default_decks

STATIC_DIR = Path(__file__).resolve().parent / "static"

#: Extensions we serve, and what we call them. `mimetypes` on Android has been
#: known to disagree about JavaScript, and a wrong type there is a blank page.
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
}

JSON_TYPE = "application/json; charset=utf-8"


class HttpError(Exception):
    """An error with a status code, turned into a JSON body by `dispatch`."""

    def __init__(self, status: int, detail: str, error: str = "error") -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.error = error


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    content_type: str = JSON_TYPE
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def json(cls, payload: Any, status: int = 200,
             headers: Optional[dict[str, str]] = None) -> "Response":
        return cls(
            status=status,
            body=json.dumps(payload).encode("utf-8"),
            content_type=JSON_TYPE,
            headers=dict(headers or {}),
        )

    # Convenience for tests and for anything that wants the parsed body back.
    def parsed(self) -> Any:
        if not self.body:
            return None
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None


Handler = Callable[..., Any]


class Router:
    """Method + path regex -> handler. One instance per game registry."""

    def __init__(
        self,
        registry: Optional[Registry] = None,
        static_dir: Optional[Path] = None,
    ) -> None:
        self.registry = registry if registry is not None else REGISTRY
        self.static_dir = Path(static_dir) if static_dir else STATIC_DIR
        self._routes: list[tuple[str, Pattern[str], Handler]] = []
        self._catalogue: Optional[dict[str, Any]] = None
        self._frames: Optional[dict[str, Any]] = None
        self._register()

    # -- registration ----------------------------------------------------

    def route(self, method: str, pattern: str) -> Callable[[Handler], Handler]:
        compiled = re.compile(f"^{pattern}$")

        def decorate(fn: Handler) -> Handler:
            self._routes.append((method.upper(), compiled, fn))
            return fn

        return decorate

    def _register(self) -> None:
        r = self.route
        r("GET", "/api/health")(self._health)
        r("GET", "/api/cards")(lambda **kw: self.catalogue())
        r("GET", "/api/frames")(lambda **kw: self.frames())
        r("GET", "/api/decks")(self._decks)
        r("GET", "/api/ai/params")(lambda **kw: ai_bridge.param_schema())
        r("GET", "/api/images")(lambda **kw: {"keys": images.available()})
        r("GET", "/api/card-image/(?P<key>.+)")(self._card_image)

        r("POST", "/api/game")(self._create_game)
        r("GET", "/api/games")(lambda **kw: {"games": self.registry.list()})
        r("GET", "/api/game/(?P<game_id>[^/]+)")(self._get_game)
        r("DELETE", "/api/game/(?P<game_id>[^/]+)")(self._delete_game)
        r("POST", "/api/game/(?P<game_id>[^/]+)/command")(self._command)
        r("POST", "/api/game/(?P<game_id>[^/]+)/undo")(self._undo)
        r("POST", "/api/game/(?P<game_id>[^/]+)/ai-params")(self._set_ai_params)
        r("GET", "/api/game/(?P<game_id>[^/]+)/log")(self._log)
        r("GET", "/api/game/(?P<game_id>[^/]+)/threat")(self._threat)

    # -- dispatch --------------------------------------------------------

    def dispatch(
        self,
        method: str,
        path: str,
        query: Optional[Mapping[str, str]] = None,
        body: bytes = b"",
    ) -> Response:
        method = method.upper()
        query = dict(query or {})
        try:
            for route_method, pattern, handler in self._routes:
                match = pattern.match(path)
                if not match:
                    continue
                if route_method != method:
                    if method == "HEAD" and route_method == "GET":
                        pass
                    else:
                        continue
                result = handler(query=query, body=body, **match.groupdict())
                if isinstance(result, Response):
                    return result
                return Response.json(result)
            if method in ("GET", "HEAD"):
                static = self._static(path)
                if static is not None:
                    return static
            raise HttpError(404, f"no route for {method} {path}", "not_found")
        except IllegalCommand as exc:
            # The client asked for something the engine did not offer. That is a
            # 400 with a message a player can read, never a 500.
            return Response.json(
                {"error": "illegal_command", "detail": str(exc)}, status=400)
        except GameNotFound as exc:
            return Response.json(
                {"error": "no_such_game", "detail": str(exc)}, status=404)
        except HttpError as exc:
            return Response.json(
                {"error": exc.error, "detail": exc.detail}, status=exc.status)

    # -- static files ----------------------------------------------------

    def _static(self, path: str) -> Optional[Response]:
        if not self.static_dir.is_dir():
            return None
        if path == "/":
            relative = "index.html"
        elif path == "/manifest.webmanifest":
            relative = "manifest.webmanifest"
        elif path.startswith("/static/"):
            relative = path[len("/static/"):]
        else:
            return None
        # posixpath.normpath collapses `..`; refuse anything that escapes.
        safe = posixpath.normpath("/" + relative).lstrip("/")
        if not safe or safe.startswith(".."):
            return None
        target = (self.static_dir / safe).resolve()
        try:
            target.relative_to(self.static_dir.resolve())
        except ValueError:
            return None
        if not target.is_file():
            return None
        suffix = target.suffix.lower()
        content_type = CONTENT_TYPES.get(suffix) or (
            mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        # The client is served from the same device it runs on, so a stale
        # cached module is pure downside; card art is content-addressed enough
        # to cache hard.
        return Response(
            status=200,
            body=target.read_bytes(),
            content_type=content_type,
            headers={"Cache-Control": "no-cache"},
        )

    # -- static data -----------------------------------------------------

    def catalogue(self) -> dict[str, Any]:
        """The card catalogue, loaded once. Marks unimplemented card text."""
        if self._catalogue is None:
            self._catalogue = catalogue_json(load_cards())
        return self._catalogue

    def frames(self) -> dict[str, Any]:
        if self._frames is None:
            self._frames = {
                name: {
                    "name": spec.name,
                    "faction": spec.faction,
                    "movement": spec.movement,
                    "weaponSlots": spec.weapon_slots,
                    "boosterSlots": spec.booster_slots,
                    "deckSize": spec.deck_size,
                    "armour": dict(spec.armour),
                    "ability": spec.ability_text,
                    "keywords": sorted(spec.keywords),
                    "shield": spec.shield,
                    "image": f"{spec.name}.png",
                }
                for name, spec in load_frames().items()
            }
        return self._frames

    def _health(self, **_: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "cards": len(self.catalogue()),
            "frames": len(self.frames()),
            "decks": len(available_decks()),
            "images": len(images.available()),
            "terrainArt": len(assets.terrain_files()),
            "tokenArt": len(assets.token_files()),
            "ai": ai_bridge.param_schema()["source"],
            "server": "stdlib",
        }

    def _decks(self, **_: Any) -> dict[str, Any]:
        specs = self.frames()
        reports = {report.deck: report for report in validate_all_decks()}
        out = []
        for name in available_decks():
            report = reports.get(name)
            frame_name = report.frame if report else None
            out.append({
                "name": name,
                "label": name.replace("deck_", "").replace("_", " ").title(),
                "frame": frame_name,
                "faction": (specs.get(frame_name) or {}).get("faction", ""),
                "size": report.size if report else 0,
                "legal": bool(report.legal) if report else True,
                "errors": list(report.errors) if report else [],
            })
        return {"decks": out}

    def _card_image(self, key: str, query: Mapping[str, str], **_: Any) -> Response:
        from urllib.parse import unquote

        path = images.find(unquote(key))
        if path is None:
            raise HttpError(404, f"no art for {key!r}", "no_such_image")
        try:
            width = int(query.get("w", images.DEFAULT_WIDTH))
        except (TypeError, ValueError):
            width = images.DEFAULT_WIDTH
        body, content_type = images.thumbnail(path, width)
        return Response(
            status=200,
            body=body,
            content_type=content_type,
            headers={"Cache-Control": "public, max-age=604800"},
        )

    # -- games -----------------------------------------------------------

    def _create_game(self, body: bytes, **_: Any) -> dict[str, Any]:
        payload = _json_body(body)
        frames_per_side = _int(payload.get("framesPerSide"), 3)
        player_decks = [str(d) for d in (payload.get("playerDecks") or [])]
        ai_decks = [str(d) for d in (payload.get("aiDecks") or [])]
        if not player_decks or not ai_decks:
            fallback_player, fallback_ai = default_decks(frames_per_side)
            player_decks = player_decks or fallback_player
            ai_decks = ai_decks or fallback_ai
        if len(player_decks) < frames_per_side or len(ai_decks) < frames_per_side:
            raise HttpError(400, f"need {frames_per_side} decks per side", "bad_request")
        seed = payload.get("seed")
        try:
            session = self.registry.create(
                player_decks=player_decks[:frames_per_side],
                ai_decks=ai_decks[:frames_per_side],
                seed=int(seed) if seed not in (None, "") else None,
                frames_per_side=frames_per_side,
                ai_params=dict(payload.get("aiParams") or {}),
                human_seat=_int(payload.get("humanSeat"), 0),
            )
        except (FileNotFoundError, ValueError) as exc:
            raise HttpError(400, str(exc), "bad_request") from exc
        return {"gameId": session.id, "view": session.view()}

    def _get_game(self, game_id: str, **_: Any) -> dict[str, Any]:
        # A plain GET is "what is on the table now" -- a refresh or a deep link.
        # The AI replay belongs to the command that caused it, so it is not
        # replayed again here.
        return self.registry.get(game_id).view(with_replay=False)

    def _delete_game(self, game_id: str, **_: Any) -> dict[str, Any]:
        self.registry.get(game_id)
        self.registry.drop(game_id)
        return {"ok": True}

    def _command(self, game_id: str, body: bytes, **_: Any) -> dict[str, Any]:
        payload = _json_body(body)
        kind = payload.get("kind")
        if not kind:
            raise HttpError(400, "command needs a 'kind'", "bad_request")
        # SPEC sends `{kind, ...}` flat; an explicit `payload` object also
        # works and is the only unambiguous form for `attack_target`, whose
        # own payload has a `kind` ("frame"/"token") that would collide with
        # the command kind. `targetKind` is accepted as a flat alias.
        command_payload = dict(payload.get("payload") or {})
        for key, value in payload.items():
            if key not in ("kind", "seat", "payload", "targetKind"):
                command_payload[key] = value
        if "targetKind" in payload:
            command_payload["kind"] = payload["targetKind"]
        return self.registry.command(game_id, str(kind), command_payload).view()

    def _undo(self, game_id: str, **_: Any) -> dict[str, Any]:
        return self.registry.undo(game_id).view()

    def _set_ai_params(self, game_id: str, body: bytes, **_: Any) -> dict[str, Any]:
        payload = _json_body(body)
        params = payload.get("aiParams")
        if not isinstance(params, Mapping):
            raise HttpError(400, "aiParams must be an object", "bad_request")
        return self.registry.retune(game_id, params).view()

    def _log(self, game_id: str, **_: Any) -> dict[str, Any]:
        session = self.registry.get(game_id)
        return {"gameId": session.id, "log": list(session.state.log)}

    def _threat(self, game_id: str, query: Mapping[str, str], **_: Any) -> dict[str, Any]:
        frame_id = query.get("frame")
        if not frame_id:
            raise HttpError(400, "threat needs ?frame=<id>", "bad_request")
        return _threat_overlay(self.registry.get(game_id), frame_id)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _json_body(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HttpError(400, f"invalid JSON: {exc}", "bad_request") from exc
    if not isinstance(parsed, Mapping):
        raise HttpError(400, "body must be a JSON object", "bad_request")
    return dict(parsed)


def _int(value: Any, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _threat_overlay(session: Session, frame_id: str) -> dict[str, Any]:
    """Board overlays for one frame: reach and line of sight.

    Public information only -- terrain, frame positions and the frame's *base*
    movement. It deliberately does not use the frame's committed cards, because
    for an enemy frame those are face down and using them would leak hidden
    information into the overlay.
    """
    state = session.state
    frame = state.frames.get(frame_id)
    if frame is None or frame.pos is None or state.board is None:
        raise HttpError(404, f"no such frame {frame_id!r}", "no_such_frame")
    board = state.board
    flying = "flying" in frame.spec.keywords
    occupied = state.occupied(exclude=frame.id)
    reach = board.reachable(
        frame.pos, frame.base_movement, occupied=occupied, flying=flying)
    los: list[list[int]] = []
    for y in range(board.height):
        for x in range(board.width):
            pos = Pos(x, y)
            if pos == frame.pos:
                continue
            try:
                clear = board.has_line_of_sight(
                    frame.pos, pos, occupied=occupied, flying_attacker=flying)
            except TypeError:                      # pragma: no cover
                clear = board.has_line_of_sight(frame.pos, pos)
            if clear:
                los.append([x, y])
    return {
        "frame": frame_id,
        "pos": {"x": frame.pos.x, "y": frame.pos.y},
        "movement": frame.base_movement,
        "reach": [[p.x, p.y, cost] for p, cost in reach.items()],
        "los": los,
        "note": "base movement and terrain only; face-down cards are not used",
    }
