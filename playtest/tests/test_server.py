"""Workstream C: the HTTP API and the client it serves.

The shipped server is standard library only (`http.server` + `json`), because
the app runs on the phone itself under Termux where FastAPI's pydantic
dependency has no wheel. So these tests drive that server, not FastAPI: most
of them go through `Router.dispatch`, which is the exact code path a socket
takes, and `test_stdlib_server_over_a_real_socket` proves the socket layer on
top of it works too.

The test that matters most is `test_human_view_never_leaks_ai_hidden_state`:
planning is simultaneous and hidden, so if the human's view ever carries the
AI's hand, deck order or a face-down card's identity, the whole playtest is
worthless. It is checked at every single decision of a complete game rather
than once at the start.
"""

from __future__ import annotations

import json
import random
import re
import urllib.error
import urllib.request
from typing import Any, Optional
from urllib.parse import urlencode, urlsplit, parse_qs

import pytest

from playtest.engine import available_decks, load_cards, legal_commands
from playtest.engine.serialize import hidden_id
from playtest.server import ai_bridge, images
from playtest.server.games import Registry
from playtest.server.httpd import BackgroundServer
from playtest.server.routes import Router

DECKS = available_decks()


# --------------------------------------------------------------------------
# A tiny test client over the router
# --------------------------------------------------------------------------


class Result:
    """Just enough of a response object for the tests to read."""

    def __init__(self, status: int, body: bytes, content_type: str,
                 headers: dict[str, str]) -> None:
        self.status_code = status
        self.content = body
        self.headers = {"content-type": content_type, **headers}

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")

    def json(self) -> Any:
        return json.loads(self.text)


class Client:
    """Drives `Router.dispatch` directly -- no socket, same code path."""

    def __init__(self, registry: Optional[Registry] = None) -> None:
        self.registry = registry if registry is not None else Registry()
        self.router = Router(self.registry)

    def request(self, method: str, path: str, json_body: Any = None) -> Result:
        parts = urlsplit(path)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        body = b"" if json_body is None else json.dumps(json_body).encode()
        response = self.router.dispatch(method, parts.path, query, body)
        return Result(response.status, response.body, response.content_type,
                      response.headers)

    def get(self, path: str) -> Result:
        return self.request("GET", path)

    def post(self, path: str, json: Any = None) -> Result:     # noqa: A002
        return self.request("POST", path, json)

    def delete(self, path: str) -> Result:
        return self.request("DELETE", path)


@pytest.fixture()
def client() -> Client:
    """A fresh router with its own registry, so tests cannot see each other."""
    return Client()


def start(client: Client, **overrides: Any) -> tuple[str, dict]:
    body = {
        "seed": 4242,
        "playerDecks": DECKS[:3],
        "aiDecks": DECKS[3:6],
        "framesPerSide": 3,
    }
    body.update(overrides)
    response = client.post("/api/game", body)
    assert response.status_code == 200, response.text
    payload = response.json()
    return payload["gameId"], payload["view"]


def send(client: Client, game_id: str, kind: str, payload: dict) -> dict:
    response = client.post(f"/api/game/{game_id}/command",
                           {"kind": kind, "payload": payload})
    assert response.status_code == 200, response.text
    return response.json()


def auto_payload(pending: dict, rng: random.Random) -> tuple[str, dict]:
    """A legal command for whatever is pending, chosen at random."""
    kind = pending["kind"]
    options = pending["options"]
    if kind == "commit_actions":
        uids = [o["uid"] for o in options]
        return kind, {"uids": rng.sample(uids, min(2, len(uids)))}
    return kind, dict(rng.choice(options))


# --------------------------------------------------------------------------
# No third-party imports on the shipped path
# --------------------------------------------------------------------------


def test_the_shipped_server_needs_nothing_but_the_standard_library() -> None:
    """Termux gets `pkg install python` and nothing else -- keep it that way."""
    import ast
    from pathlib import Path

    stdlib_only = ["httpd.py", "routes.py", "games.py", "ai_bridge.py", "images.py",
                   "__init__.py", "__main__.py"]
    server_dir = Path(ai_bridge.__file__).resolve().parent
    third_party = {"fastapi", "starlette", "pydantic", "uvicorn", "httpx", "anyio",
                   "numpy", "requests", "flask", "django"}
    for name in stdlib_only:
        tree = ast.parse((server_dir / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            roots = []
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                roots = [node.module.split(".")[0]]
            offenders = third_party.intersection(roots)
            assert not offenders, f"{name} imports {offenders}"


def test_importing_the_server_package_does_not_pull_in_fastapi() -> None:
    import subprocess
    import sys

    code = (
        "import playtest.server, sys; "
        "bad=[m for m in ('fastapi','pydantic','uvicorn') if m in sys.modules]; "
        "print(bad)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, check=True)
    assert out.stdout.strip() == "[]", out.stdout


# --------------------------------------------------------------------------
# Static endpoints
# --------------------------------------------------------------------------


def test_health(client: Client) -> None:
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["server"] == "stdlib"
    assert body["cards"] > 100
    assert body["frames"] == 12


def test_cards_catalogue_is_keyed_by_group_name(client: Client) -> None:
    cards = client.get("/api/cards").json()
    assert "Sword_Feint" in cards
    entry = cards["Sword_Feint"]
    assert entry["key"] == "Sword_Feint"
    assert set(entry["attacks"]) == {"High", "Mid", "Low"}
    # The key doubles as the card image filename stem (workstream E).
    assert entry["image"] == "Sword_Feint.png"


def test_catalogue_flags_the_unimplemented_card_text(client: Client) -> None:
    """All 24 pilot cards and both drone cards load but their text is a no-op."""
    cards = client.get("/api/cards").json()
    flagged = {k: v for k, v in cards.items() if "notImplemented" in v}
    kinds = {v["type"] for v in flagged.values()}
    assert kinds <= {"pilot", "drone"}, kinds
    pilots = {k for k, v in cards.items() if v["type"] == "pilot"}
    drones = {k for k, v in cards.items() if v["type"] == "drone"}
    assert pilots <= set(flagged), "every pilot card must be flagged"
    assert drones <= set(flagged), "every drone card must be flagged"
    assert len(flagged) == 26, f"expected 26 deferred cards, got {len(flagged)}"


def test_frames_and_decks(client: Client) -> None:
    frames = client.get("/api/frames").json()
    assert "Adam" in frames
    assert set(frames["Adam"]["armour"]) == {"High", "Mid", "Low"}
    decks = client.get("/api/decks").json()["decks"]
    assert len(decks) == len(DECKS)
    assert all("legal" in d and "frame" in d for d in decks)


def test_ai_params_schema_shape(client: Client) -> None:
    """The client builds its controls from this, so the shape is the contract."""
    body = client.get("/api/ai/params").json()
    assert isinstance(body["params"], list) and body["params"]
    for entry in body["params"]:
        assert entry["name"] and isinstance(entry["name"], str)
        assert "label" in entry and "help" in entry and "default" in entry
    assert isinstance(body.get("presets", {}), dict)
    json.dumps(body)                       # JSON-serialisable end to end


def test_index_and_static_client_are_served(client: Client) -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "board-canvas" in page.text
    for asset, kind in (
        ("/static/app.css", "text/css"),
        ("/static/js/app.js", "text/javascript"),
        ("/static/js/board.js", "text/javascript"),
        ("/static/js/cards.js", "text/javascript"),
        ("/static/js/params.js", "text/javascript"),
        ("/static/js/decisions.js", "text/javascript"),
        ("/static/js/api.js", "text/javascript"),
        ("/manifest.webmanifest", "application/manifest+json"),
    ):
        response = client.get(asset)
        assert response.status_code == 200, asset
        assert response.headers["content-type"].startswith(kind), asset


def test_the_client_never_references_an_external_host() -> None:
    """It has to work on a train with the radio off."""
    from pathlib import Path

    static = Path(images.__file__).resolve().parent / "static"
    for path in list(static.rglob("*.html")) + list(static.rglob("*.js")) \
            + list(static.rglob("*.css")) + list(static.rglob("*.webmanifest")):
        text = path.read_text(encoding="utf-8")
        for needle in ("http://", "https://", "//cdn", "fonts.googleapis"):
            assert needle not in text, f"{path.name} references {needle}"


def test_only_api_js_talks_to_the_transport() -> None:
    """One module is the seam, so an in-browser engine is a one-file swap."""
    from pathlib import Path

    js = Path(images.__file__).resolve().parent / "static" / "js"
    for path in js.glob("*.js"):
        if path.name == "api.js":
            continue
        text = path.read_text(encoding="utf-8")
        for needle in ("fetch(", "XMLHttpRequest", "WebSocket", "EventSource"):
            assert needle not in text, f"{path.name} calls {needle} directly"


def test_static_serving_refuses_to_escape_its_directory(client: Client) -> None:
    for attempt in ("/static/../games.py", "/static/../../engine/state.py",
                    "/static/%2e%2e/games.py"):
        assert client.get(attempt).status_code == 404, attempt


def test_unknown_route_is_404_json(client: Client) -> None:
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


# --------------------------------------------------------------------------
# Card art
# --------------------------------------------------------------------------


def test_card_image_is_phone_sized_and_needs_url_encoding(client: Client) -> None:
    """`AllCardImages` filenames contain spaces, so the key must be encoded."""
    key = "Assault Rifle_Aimed Fire"
    if images.find(key) is None:
        pytest.skip("no card art in this checkout")
    encoded = client.get(f"/api/card-image/{key.replace(' ', '%20')}?w=240")
    assert encoded.status_code == 200
    assert encoded.headers["content-type"].startswith("image/")
    # 378x537 print PNGs are ~90 kB; a phone must never be sent those.
    assert len(encoded.content) < 60_000
    assert client.get("/api/card-image/No%20Such%20Card").status_code == 404


def test_bundled_art_is_served_untouched(client: Client) -> None:
    """The phone has no ImageMagick, so the bundle must need no resizing."""
    from pathlib import Path

    if not images.BUNDLE_DIR.is_dir() or not any(images.BUNDLE_DIR.iterdir()):
        pytest.skip("card bundle not built (python -m playtest.server.images)")
    sample = next(p for p in sorted(images.BUNDLE_DIR.iterdir())
                  if p.suffix.lower() in images.IMAGE_SUFFIXES)
    found = images.find(sample.stem)
    assert found is not None
    assert images.is_bundled(found), "the bundle must win over the originals"
    body, content_type = images.thumbnail(found)
    assert body == sample.read_bytes(), "a bundled file is already the right size"
    assert content_type == "image/jpeg"
    # And the whole bundle has to be small enough to carry on a phone.
    total = sum(p.stat().st_size for p in images.BUNDLE_DIR.iterdir() if p.is_file())
    assert total < 8_000_000, f"bundle is {total / 1e6:.1f} MB"


def test_card_image_key_matches_the_catalogue_key() -> None:
    """The engine key, the image filename and the client's lookup are one string."""
    catalogue = load_cards()
    have = set(images.available())
    if not have:
        pytest.skip("no card art in this checkout")
    missing = [key for key in catalogue if key not in have]
    assert len(missing) < len(catalogue), "no card art matched any catalogue key"
    for key in list(catalogue)[:20]:
        if key in have:
            assert images.find(key) is not None


# --------------------------------------------------------------------------
# Games
# --------------------------------------------------------------------------


def test_new_game_returns_a_playable_view(client: Client) -> None:
    game_id, view = start(client)
    assert view["gameId"] == game_id
    assert view["board"]["width"] == 15 and view["board"]["height"] == 16
    assert len(view["board"]["tiles"]) == 15 * 16
    assert len(view["frames"]) == 6
    assert view["pending"]["seat"] == 0
    assert view["pending"]["kind"] == "commit_actions"
    assert view["turn"] == 1


def test_get_game_and_log(client: Client) -> None:
    game_id, _ = start(client)
    again = client.get(f"/api/game/{game_id}").json()
    assert again["gameId"] == game_id
    log = client.get(f"/api/game/{game_id}/log").json()
    assert log["gameId"] == game_id
    assert any("begins" in entry["text"] for entry in log["log"])
    listing = client.get("/api/games").json()["games"]
    assert any(g["gameId"] == game_id for g in listing)
    assert client.delete(f"/api/game/{game_id}").status_code == 200
    assert client.get(f"/api/game/{game_id}").status_code == 404


def test_unknown_game_is_404(client: Client) -> None:
    assert client.get("/api/game/nope").status_code == 404
    assert client.post("/api/game/nope/command",
                       {"kind": "commit_actions", "payload": {}}).status_code == 404


def test_illegal_command_is_400_not_500(client: Client) -> None:
    game_id, view = start(client)
    uids = [o["uid"] for o in view["pending"]["options"]]

    # A card that is not in the frame's hand.
    bad = client.post(f"/api/game/{game_id}/command",
                      {"kind": "commit_actions", "payload": {"uids": ["c999", "c998"]}})
    assert bad.status_code == 400
    assert bad.json()["error"] == "illegal_command"
    assert bad.json()["detail"]

    # The right cards but the wrong count.
    assert client.post(f"/api/game/{game_id}/command",
                       {"kind": "commit_actions",
                        "payload": {"uids": uids[:1]}}).status_code == 400

    # A command the engine is not waiting for.
    assert client.post(f"/api/game/{game_id}/command",
                       {"kind": "move", "payload": {"x": 0, "y": 0}}).status_code == 400

    # A command with no kind at all, and a body that is not JSON.
    assert client.post(f"/api/game/{game_id}/command", {}).status_code == 400
    raw = client.router.dispatch("POST", f"/api/game/{game_id}/command",
                                 {}, b"{not json")
    assert raw.status == 400

    # And the game is still playable afterwards.
    assert send(client, game_id, "commit_actions", {"uids": uids[:2]})["turn"] == 1


def test_undo_steps_back_one_human_decision(client: Client) -> None:
    game_id, view = start(client)
    before = len(view["log"])
    uids = [o["uid"] for o in view["pending"]["options"]]
    after_commit = send(client, game_id, "commit_actions", {"uids": uids[:2]})
    assert len(after_commit["log"]) > before
    undone = client.post(f"/api/game/{game_id}/undo").json()
    assert len(undone["log"]) == before
    assert undone["pending"]["kind"] == "commit_actions"
    for _ in range(5):
        response = client.post(f"/api/game/{game_id}/undo")
        if response.status_code == 400:
            break
    assert response.status_code == 400


def test_attack_target_payload_needs_the_nested_form(client: Client) -> None:
    """`attack_target`'s payload has its own `kind`, so the flat form collides."""
    for seed in range(1, 12):
        game_id, view = start(client, seed=seed)
        rng = random.Random(seed)
        for _ in range(400):
            pending = view.get("pending")
            if view["over"] or not pending:
                break
            if pending["kind"] == "attack_target":
                option = pending["options"][0]
                flat = client.post(
                    f"/api/game/{game_id}/command",
                    {"kind": "attack_target", "id": option["id"],
                     "targetKind": option["kind"]})
                assert flat.status_code == 200, flat.text
                return
            kind, payload = auto_payload(pending, rng)
            view = send(client, game_id, kind, payload)
    pytest.skip("no multi-target attack came up in 11 games")


def test_a_whole_game_plays_through_the_api(client: Client) -> None:
    game_id, view = start(client, seed=11)
    rng = random.Random(3)
    seen: set[str] = set()
    for _ in range(600):
        if view["over"]:
            break
        pending = view["pending"]
        assert pending is not None, "the server must never hand back a dead end"
        assert pending["seat"] == view["seat"], "the AI seat must be advanced server-side"
        seen.add(pending["kind"])
        kind, payload = auto_payload(pending, rng)
        view = send(client, game_id, kind, payload)
    assert view["over"] is True
    assert view["phase"] == "finished"
    assert {"commit_actions", "resolve_order", "move"} <= seen
    assert set(view["vp"]) == {"0", "1"}
    # Damage actually happened, so this was a real game and not an empty loop.
    assert any("hits" in entry["text"] for entry in view["log"])


def test_ai_seat_is_advanced_before_the_view_comes_back(client: Client) -> None:
    game_id, view = start(client)
    for _ in range(30):
        pending = view.get("pending")
        if not pending or view["over"]:
            break
        assert not pending.get("waiting"), "the human should never be shown a wait state"
        assert pending["seat"] == 0
        kind, payload = auto_payload(pending, random.Random(7))
        view = send(client, game_id, kind, payload)


def test_retuning_the_ai_mid_game(client: Client) -> None:
    game_id, _ = start(client)
    schema = client.get("/api/ai/params").json()
    params = {p["name"]: p["default"] for p in schema["params"]}
    assert client.post(f"/api/game/{game_id}/ai-params",
                       {"aiParams": params}).status_code == 200
    assert client.post(f"/api/game/{game_id}/ai-params",
                       {"aiParams": "nope"}).status_code == 400


# --------------------------------------------------------------------------
# The socket layer
# --------------------------------------------------------------------------


def _http(url: str, payload: Any = None, method: Optional[str] = None
          ) -> tuple[int, Any, str]:
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url, data=data, method=method or ("POST" if data else "GET"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read()
            return response.status, body, response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("Content-Type", "")


def test_stdlib_server_over_a_real_socket() -> None:
    """The shipped path, end to end: a real socket, real HTTP, a real game."""
    with BackgroundServer(Registry()) as server:
        base = server.base_url

        status, body, content_type = _http(f"{base}/api/health")
        assert status == 200 and json.loads(body)["server"] == "stdlib"
        assert content_type.startswith("application/json")

        status, body, content_type = _http(f"{base}/")
        assert status == 200 and content_type.startswith("text/html")
        assert b"board-canvas" in body

        status, body, _ = _http(f"{base}/api/game", {
            "seed": 5, "playerDecks": DECKS[:3], "aiDecks": DECKS[3:6]})
        assert status == 200
        payload = json.loads(body)
        game_id, view = payload["gameId"], payload["view"]

        # Play to the first resolved attack over the wire.
        rng = random.Random(5)
        for _ in range(200):
            pending = view.get("pending")
            if view["over"] or not pending:
                break
            kind, cmd = auto_payload(pending, rng)
            status, body, _ = _http(f"{base}/api/game/{game_id}/command",
                                    {"kind": kind, "payload": cmd})
            assert status == 200, body
            view = json.loads(body)
            if any("hits" in entry["text"] for entry in view["log"]):
                break
        assert any("hits" in entry["text"] for entry in view["log"]), \
            "no attack resolved over the socket"

        # Errors keep their status codes through the socket layer too.
        status, body, _ = _http(f"{base}/api/game/{game_id}/command",
                                {"kind": "move", "payload": {"x": 0, "y": 0}})
        assert status == 400 and json.loads(body)["error"] == "illegal_command"
        assert _http(f"{base}/api/game/nope")[0] == 404

        # Card art travels intact.
        key = urlencode({"": "Sword_Feint"})[1:]
        status, body, content_type = _http(f"{base}/api/card-image/{key}")
        if status == 200:
            assert content_type.startswith("image/")
            assert len(body) < 60_000


def test_the_server_binds_loopback_by_default() -> None:
    from playtest.server import httpd

    assert httpd.DEFAULT_HOST == "127.0.0.1", "never expose this by default"
    with BackgroundServer(Registry()) as server:
        assert server.server.server_address[0] == "127.0.0.1"


# --------------------------------------------------------------------------
# The one that matters: hidden information
# --------------------------------------------------------------------------


def _ai_secret_keys(registry: Registry, game_id: str) -> dict[str, set[str]]:
    """Everything the human is not entitled to know, straight off the state."""
    session = registry.get(game_id)
    state = session.state
    hand_uids: set[str] = set()
    deck_uids: set[str] = set()
    facedown_uids: set[str] = set()
    for frame in state.frames.values():
        if frame.seat != session.ai_seat:
            continue
        hand_uids |= set(frame.hand)
        deck_uids |= set(frame.deck)
        for uid in frame.committed:
            inst = state.cards[uid]
            if inst.location == "committed" and inst.face_down:
                facedown_uids.add(uid)
    return {"hand": hand_uids, "deck": deck_uids, "facedown": facedown_uids}


def _uids_in(blob: Any) -> set[str]:
    """Every `uid` value anywhere in the view."""
    found: set[str] = set()
    if isinstance(blob, dict):
        for key, value in blob.items():
            if key == "uid" and isinstance(value, str):
                found.add(value)
            found |= _uids_in(value)
    elif isinstance(blob, list):
        for item in blob:
            found |= _uids_in(item)
    return found


def test_human_view_never_leaks_ai_hidden_state() -> None:
    """The regression test. Checked at every decision of a complete game.

    The human view may name an AI card only once the engine has turned it face
    up (it is resolving, has resolved, or is set aside). It must never carry
    the AI's hand, its deck order, or the identity of a face-down commitment.
    """
    client = Client(Registry())
    registry = client.registry
    game_id, view = start(client, seed=99)
    rng = random.Random(5)
    checks = 0

    for _ in range(600):
        secrets = _ai_secret_keys(registry, game_id)
        ai_seat = registry.get(game_id).ai_seat

        # 1. No AI frame exposes a hand, only a count.
        for frame in view["frames"]:
            if frame["seat"] == ai_seat:
                assert "hand" not in frame, f"AI hand leaked on {frame['id']}"
                assert isinstance(frame.get("handCount"), int)
            else:
                assert "hand" in frame

        # 2. Nothing anywhere in the view references a hand or deck card's uid.
        present = _uids_in(view)
        assert not (present & secrets["hand"]), "an AI hand card appears in the view"
        assert not (present & secrets["deck"]), "an AI deck card appears in the view"

        # 3. Face-down AI commitments are anonymous.
        for frame in view["frames"]:
            if frame["seat"] != ai_seat:
                continue
            for card in frame["committed"]:
                if card["uid"] in secrets["facedown"]:
                    assert card["faceDown"] is True
                    assert "key" not in card, (
                        f"face-down AI card {card['uid']} revealed as {card.get('key')}"
                    )

        # 4. The AI's deck order is never sent -- only counts.
        for frame in view["frames"]:
            assert isinstance(frame["deckCount"], int)
            assert "deck" not in frame and "discard" not in frame

        # 5. A pending decision belonging to the AI never carries its options.
        pending = view.get("pending")
        if pending and pending.get("seat") != view["seat"]:
            assert "options" not in pending

        # 6. Not one uid of a card in the AI's hand or deck appears anywhere in
        #    the serialised view -- not as a value, not inside a log line.
        #    (Face-down *commitments* do carry an opaque uid, deliberately, so
        #    the client can draw the right number of card backs; they carry no
        #    `key`, which is what rule 3 above checks.)
        blob = json.dumps(view)
        for uid in secrets["hand"] | secrets["deck"]:
            assert f'"{uid}"' not in blob, f"uid {uid} leaked into the view"

        checks += 1
        if view["over"] or not pending:
            break
        kind, payload = auto_payload(pending, rng)
        view = send(client, game_id, kind, payload)

    assert checks > 40, f"only made {checks} checks -- the game did not really run"
    assert view["over"] is True


def test_facedown_uids_do_not_decode_to_card_identities() -> None:
    """A face-down commitment must not be identifiable from its uid alone."""
    client = Client(Registry())
    registry = client.registry
    game_id, view = start(client, seed=17)
    session = registry.get(game_id)
    rng = random.Random(0)
    for _ in range(40):
        facedown = [
            card for frame in view["frames"] if frame["seat"] == session.ai_seat
            for card in frame["committed"] if card["faceDown"]
        ]
        if facedown or view["over"] or not view.get("pending"):
            break
        kind, payload = auto_payload(view["pending"], rng)
        view = send(client, game_id, kind, payload)
    state = session.state
    assert any(card["faceDown"] for frame in view["frames"]
               if frame["seat"] == session.ai_seat
               for card in frame["committed"]), "no face-down AI card to test"

    # What an attacker knows: the deck lists, which ship in the repo.
    order: dict[str, list[str]] = {}
    for frame in state.frames.values():
        uids = sorted((u for u, i in state.cards.items() if i.owner == frame.id),
                      key=lambda u: int(u[1:]))
        order[frame.id] = [state.cards[u].key for u in uids]

    checked = 0
    for frame in view["frames"]:
        if frame["seat"] != session.ai_seat:
            continue
        owned = [u for u, i in state.cards.items() if i.owner == frame["id"]]
        base = min(owned, key=lambda u: int(u[1:]))
        for card in frame["committed"]:
            if not card["faceDown"]:
                continue
            checked += 1
            shown = card["uid"]
            assert "key" not in card, "a face-down card must not ship its key"

            # The threat is real: given the engine's own uid, the deck order
            # above decodes it straight to the card.
            real = next(
                u for u in owned
                if hidden_id(state, session.human_seat, u) == shown
            )
            assert order[frame["id"]][int(real[1:]) - int(base[1:])] == \
                state.cards[real].key

            # So the view must not ship that uid, nor anything positional.
            assert shown not in state.cards, f"view ships the real uid {shown}"
            assert not re.fullmatch(r"c\d+", shown), f"{shown} is positional"
    assert checked, "no face-down card was actually examined"


def test_legal_commands_for_the_ai_seat_are_not_offered_to_the_human() -> None:
    client = Client(Registry())
    game_id, view = start(client)
    session = client.registry.get(game_id)
    # It is always the human's turn in a view we hand back, so the AI seat has
    # nothing legal -- there is no window in which the human could act for it.
    assert legal_commands(session.state, session.ai_seat) == []
    assert view["legal"], "the human's own legal commands are published"


# --------------------------------------------------------------------------
# The AI bridge
# --------------------------------------------------------------------------


def test_bridge_normalises_a_dataclass_style_schema() -> None:
    """Workstream D may hand us a list, a mapping or a whole payload dict."""
    as_list = ai_bridge._normalise_schema([
        {"name": "x", "min": 0, "max": 1, "default": 0.5, "help": "h"},
    ])
    assert as_list[0]["label"] == "X" and as_list[0]["type"] == "float"

    wrapped = ai_bridge._normalise_schema({"params": [
        {"key": "focus_fire", "lo": 0, "hi": 2, "default": 1, "description": "d"},
    ]})
    assert wrapped[0]["name"] == "focus_fire"
    assert wrapped[0]["label"] == "Focus Fire"
    assert wrapped[0]["help"] == "d"
    assert wrapped[0]["min"] == 0 and wrapped[0]["max"] == 2

    as_map = ai_bridge._normalise_schema({"aggression": 1.0})
    assert as_map[0]["name"] == "aggression"

    assert ai_bridge._normalise_schema(None) == []


def test_bridge_falls_back_when_the_ai_package_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(ai_bridge, "_candidates", lambda: [])
    schema = ai_bridge.param_schema()
    assert schema["source"] == "fallback"
    assert schema["params"], "the UI must still have controls to draw"
    agent = ai_bridge.make_agent({}, seat=1)
    assert isinstance(agent, ai_bridge.HeuristicAgent)


def test_fallback_agent_plays_a_legal_game(monkeypatch) -> None:
    """A game must be playable end to end without workstream D."""
    monkeypatch.setattr(ai_bridge, "_candidates", lambda: [])
    client = Client(Registry())
    game_id, view = start(client, seed=8)
    assert view["aiSource"] == "fallback"
    rng = random.Random(2)
    for _ in range(600):
        if view["over"] or not view.get("pending"):
            break
        kind, payload = auto_payload(view["pending"], rng)
        view = send(client, game_id, kind, payload)
    assert view["over"] is True


def test_a_broken_agent_does_not_500(client: Client) -> None:
    """If D's agent throws, the server plays a legal move rather than crashing."""
    class Exploding:
        source = "broken"

        def choose(self, state, seat):
            raise RuntimeError("boom")

    game_id, view = start(client)
    client.registry.get(game_id).agent = Exploding()
    uids = [o["uid"] for o in view["pending"]["options"]]
    view = send(client, game_id, "commit_actions", {"uids": uids[:2]})
    assert view["pending"] is not None or view["over"]


# --------------------------------------------------------------------------
# Board overlays
# --------------------------------------------------------------------------


def test_threat_overlay_uses_public_information_only(client: Client) -> None:
    game_id, view = start(client)
    frame = next(f for f in view["frames"] if f["seat"] == 1)
    body = client.get(f"/api/game/{game_id}/threat?frame={frame['id']}").json()
    assert body["frame"] == frame["id"]
    assert body["movement"] == frame["movement"], "base movement only"
    assert body["reach"] and all(len(entry) == 3 for entry in body["reach"])
    assert isinstance(body["los"], list)
    assert client.get(f"/api/game/{game_id}/threat?frame=zz").status_code == 404
    assert client.get(f"/api/game/{game_id}/threat").status_code == 400


# --------------------------------------------------------------------------
# The optional FastAPI adapter (desktop development only)
# --------------------------------------------------------------------------


def test_fastapi_adapter_uses_the_same_router() -> None:
    pytest.importorskip("fastapi")
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from playtest.server.app import create_app

    with fastapi_testclient.TestClient(create_app(Registry())) as http:
        assert http.get("/api/health").json()["server"] == "stdlib"
        created = http.post("/api/game", json={
            "seed": 3, "playerDecks": DECKS[:3], "aiDecks": DECKS[3:6]})
        assert created.status_code == 200
        game_id = created.json()["gameId"]
        bad = http.post(f"/api/game/{game_id}/command",
                        json={"kind": "move", "payload": {"x": 0, "y": 0}})
        assert bad.status_code == 400
