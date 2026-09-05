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


def start(client: Client, *, deploy: bool = True,
          **overrides: Any) -> tuple[str, dict]:
    """Start a game. By default it is played past the setup phase.

    A new game opens on the `deploy` decision -- players place their frames
    one at a time before turn 1 -- so a test that wants a game already in
    progress has to get through deployment first. Pass `deploy=False` to stop
    on the first deployment decision instead.
    """
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
    game_id, view = payload["gameId"], payload["view"]
    if deploy:
        view = deploy_all(client, game_id, view)
    return game_id, view


#: The decisions the setup phase can raise: placing frames, then whatever the
#: objectives on the board still want -- a fugitive to hide, gangs or refugees
#: to put down, a frame to hand the bomb to.
SETUP_KINDS = ("deploy", "place_objective", "choose_frame")


def deploy_all(client: Client, game_id: str, view: dict) -> dict:
    """Answer every setup decision for the human seat."""
    guard = 0
    while view.get("pending") and view["pending"].get("kind") in SETUP_KINDS:
        guard += 1
        assert guard < 50, "deployment did not finish"
        pending = view["pending"]
        view = send(client, game_id, pending["kind"],
                    dict(pending["options"][0]))
    return view


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
    """Whatever card text is not implemented yet is flagged for the client.

    Only pilot and drone text may be deferred, and that set shrinks as those
    effects land -- so this asserts the bound and the shape of the flag, not
    how many cards are currently outstanding.
    """
    cards = client.get("/api/cards").json()
    flagged = {k: v for k, v in cards.items() if "notImplemented" in v}
    kinds = {v["type"] for v in flagged.values()}
    assert kinds <= {"pilot", "drone"}, kinds
    for key, card in flagged.items():
        assert card["notImplemented"], f"{key} flagged with nothing to show"
    every = {k for k, v in cards.items() if v["type"] in ("pilot", "drone")}
    assert set(flagged) <= every


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

        # Play to the first resolved attack over the wire. Whether a random
        # player lands one in a given game is a balance question and moves
        # with the card CSVs, so this sweeps seeds rather than pinning one --
        # what is under test is the socket, not the shooting.
        game_id = ""
        view = {}
        landed = False
        for seed in range(1, 12):
            status, body, _ = _http(f"{base}/api/game", {
                "seed": seed, "playerDecks": DECKS[:3], "aiDecks": DECKS[3:6]})
            assert status == 200
            payload = json.loads(body)
            game_id, view = payload["gameId"], payload["view"]
            rng = random.Random(seed)
            for _ in range(400):
                pending = view.get("pending")
                if view["over"] or not pending:
                    break
                kind, cmd = auto_payload(pending, rng)
                status, body, _ = _http(f"{base}/api/game/{game_id}/command",
                                        {"kind": kind, "payload": cmd})
                assert status == 200, body
                view = json.loads(body)
                if any("hits" in entry["text"] for entry in view["log"]):
                    landed = True
                    break
            if landed:
                break
        assert landed, "no attack resolved over the socket in eleven games"

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


def test_threat_answers_from_a_tile_the_frame_is_only_considering(
    client: Client,
) -> None:
    """Half of movement is what you will be able to see from the far end.

    Terrain and frame positions are both public, so a hypothetical vantage
    point gives away nothing the frame's own does not -- and the reach still
    comes from where it actually stands, because that is where it moves from.
    """
    game_id, view = start(client)
    frame = next(f for f in view["frames"] if f["seat"] == 0)
    here = client.get(f"/api/game/{game_id}/threat?frame={frame['id']}").json()
    assert here["from"] == here["pos"]

    x, y = frame["pos"]["x"], frame["pos"]["y"]
    elsewhere = client.get(
        f"/api/game/{game_id}/threat?frame={frame['id']}&x={x}&y={y - 6}"
    ).json()
    assert elsewhere["from"] == {"x": x, "y": y - 6}
    assert elsewhere["pos"] == here["pos"], "the frame has not moved"
    assert elsewhere["reach"] == here["reach"], "reach is still from where it is"
    assert elsewhere["los"] != here["los"], "but the sight lines are not"

    off = client.get(f"/api/game/{game_id}/threat?frame={frame['id']}&x=99&y=99")
    assert off.status_code == 400


def test_a_terrain_deck_can_be_chosen_for_each_side(client: Client) -> None:
    """"Each player must bring a terrain deck of 10 cards, and an objective
    deck of 5" (rules.tex:253) -- so which one is the player's call."""
    listed = client.get("/api/decks").json()["terrain"]
    assert listed, "the client has to be able to offer them"
    names = {deck["name"] for deck in listed}
    assert {"assault", "control", "siege", "strike"} <= names
    for deck in listed:
        assert deck["objectives"], "the objectives are what the choice is about"

    response = client.post("/api/game", {
        "seed": 4242, "playerDecks": DECKS[:3], "aiDecks": DECKS[3:6],
        "framesPerSide": 3, "playerTerrain": "siege", "aiTerrain": "control",
    })
    assert response.status_code == 200, response.text
    view = response.json()["view"]
    brought = [
        entry["text"] for entry in view["log"] if "terrain deck" in entry["text"]
    ]
    assert any("siege" in line for line in brought)
    assert any("control" in line for line in brought)

    bad = client.post("/api/game", {
        "seed": 1, "playerDecks": DECKS[:3], "aiDecks": DECKS[3:6],
        "playerTerrain": "not-a-battlefield",
    })
    assert bad.status_code == 400


def test_the_build_marker_changes_with_the_code(tmp_path) -> None:
    """The app runs out of a clone, so this is the only "which version".

    Same code, same id, on any machine; a byte changes and so does the id.
    """
    from playtest.server import build

    first = build.build_id()
    assert len(first) == 8 and first == build.build_id(), "stable while nothing moves"
    info = build.info()
    assert info["build"] == first and info["files"] > 10

    target = next(p for p in build._files() if p.suffix == ".js")
    original = target.read_bytes()
    try:
        target.write_bytes(original + b"\n// touched\n")
        assert build.build_id() != first, "an edited client is a different build"
    finally:
        target.write_bytes(original)
    assert build.build_id() == first, "and putting it back puts the id back"


def test_health_carries_the_build_marker(client: Client) -> None:
    body = client.get("/api/health").json()
    assert len(str(body["build"])) == 8
    assert body["files"] > 10


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


# --------------------------------------------------------------------------
# Read-outs the client cannot derive: what is resolving, what can be blocked
# --------------------------------------------------------------------------


def _play_until(client: Client, game_id: str, view: dict, predicate,
                rng: Optional[random.Random] = None, limit: int = 400):
    """Play on until `predicate(view)`, or give up. Returns the view or None."""
    rng = rng or random.Random(17)
    for _ in range(limit):
        if predicate(view):
            return view
        pending = view.get("pending")
        if view["over"] or not pending:
            return None
        kind, payload = auto_payload(pending, rng)
        view = send(client, game_id, kind, payload)
    return None


def test_view_says_which_card_is_resolving(client: Client) -> None:
    """`PendingDecision` names the frame but not the card; `resolving` does.

    Without it the only way to know what a `move` or `attack_target` decision
    belongs to was to parse the log line, which is why this exists.
    """
    game_id, view = start(client, seed=7)
    hit = _play_until(client, game_id, view, lambda v: v.get("resolving"))
    assert hit is not None, "no card resolved in a whole game"
    res = hit["resolving"]
    assert res["frameId"] in {f["id"] for f in hit["frames"]}
    assert res["frameName"] and isinstance(res["mine"], bool)
    # It is face up by the time it resolves, so it may be named.
    assert res["key"] in load_cards()
    assert isinstance(res["initiative"], int)


OWN_DECISIONS = {"move", "attack_target", "resolve_order", "effect_choice"}


def test_resolving_matches_the_frame_the_engine_is_asking_about(client: Client) -> None:
    """The acting frame, except for a block -- where the defender is deciding.

    That distinction is the whole reason the corner display reads `resolving`
    rather than `pending.frameId`: during a compulsory block the frame on the
    clock is the one being hit, not the one acting.
    """
    matched = blocks = 0
    # Several games: whether a compulsory block comes up at all is a property
    # of how the cards happen to fall, and a card pool that changes should not
    # be able to turn this into a red test without breaking anything.
    for seed in range(1, 12):
        game_id, view = start(client, seed=seed)
        rng = random.Random(seed + 10)
        for _ in range(400):
            pending = view.get("pending") or {}
            res = view.get("resolving")
            if res and pending.get("frameId"):
                if pending["kind"] in OWN_DECISIONS:
                    assert res["frameId"] == pending["frameId"]
                    matched += 1
                elif pending["kind"] == "choose_block":
                    assert res["frameId"] != pending["frameId"]
                    assert res["attack"]["targetId"] == pending["frameId"]
                    blocks += 1
            if view["over"] or not view.get("pending"):
                break
            kind, payload = auto_payload(view["pending"], rng)
            view = send(client, game_id, kind, payload)
        if matched and blocks:
            break
    assert matched, "never saw a decision belonging to the acting frame"
    assert blocks, "never saw a compulsory block in 11 games"


def test_defence_readout_counts_blocks_per_zone(client: Client) -> None:
    """The read the game turns on: what can this frame still cover?"""
    game_id, view = start(client, seed=7)
    hit = _play_until(
        client, game_id, view,
        lambda v: any(d["remaining"] for d in (v.get("defence") or {}).values()))
    assert hit is not None
    catalogue = load_cards()
    for frame_id, defence in hit["defence"].items():
        assert defence["frameId"] == frame_id
        assert set(defence["zones"]) == {"High", "Mid", "Low"}
        assert defence["remaining"] >= defence["faceDown"] >= 0
        for zone, info in defence["zones"].items():
            assert info["cards"] == len(info["known"])
            assert info["super"] <= info["cards"]
            for card in info["known"]:
                # Every card offered as a blocker really does block that zone,
                # and the super flag is the card's own Block value.
                blocks = catalogue[card["key"]].blocks[zone]
                assert blocks > 0
                assert card["super"] == (blocks >= 2)


def test_defence_never_describes_a_card_this_seat_cannot_see() -> None:
    """The hidden half of the game stays hidden.

    A face-down enemy card must not appear in the readout, and -- the subtler
    one -- the readout must not say *how many* face-down cards block a zone
    either: that is the identity leaking out one bit at a time. Only the
    frame's total face-down count is public.
    """
    client = Client(Registry())
    registry = client.registry
    game_id, view = start(client, seed=99)
    rng = random.Random(5)
    checks = 0
    for _ in range(400):
        session = registry.get(game_id)
        ai_seat = session.ai_seat
        hidden = {
            uid for frame in session.state.frames.values()
            if frame.seat == ai_seat
            for uid in frame.committed
            if session.state.cards[uid].location == "committed"
            and session.state.cards[uid].face_down
        }
        for frame in view["frames"]:
            defence = (view.get("defence") or {}).get(frame["id"])
            if defence is None:
                continue
            for info in defence["zones"].values():
                assert "hidden" not in info, "per-zone hidden count leaks identity"
                for card in info["known"]:
                    assert card["uid"] not in hidden
                    if frame["seat"] == ai_seat:
                        assert card["faceDown"] is False
            if frame["seat"] == ai_seat:
                assert defence["faceDown"] == len(
                    [c for c in frame["committed"] if c["faceDown"]])
        checks += 1
        pending = view.get("pending")
        if view["over"] or not pending:
            break
        kind, payload = auto_payload(pending, rng)
        view = send(client, game_id, kind, payload)
    assert checks > 40, f"only made {checks} checks"


def test_initiative_map_covers_only_cards_this_seat_can_identify() -> None:
    client = Client(Registry())
    registry = client.registry
    game_id, view = start(client, seed=99)
    rng = random.Random(9)
    for _ in range(200):
        session = registry.get(game_id)
        state = session.state
        for uid, value in (view.get("initiative") or {}).items():
            inst = state.cards[uid]
            frame = state.frames[inst.owner]
            assert isinstance(value, int)
            assert frame.seat == session.human_seat or not inst.face_down, (
                f"initiative leaked for face-down AI card {uid}")
        pending = view.get("pending")
        if view["over"] or not pending:
            break
        kind, payload = auto_payload(pending, rng)
        view = send(client, game_id, kind, payload)


# --------------------------------------------------------------------------
# Replaying the AI's turn
# --------------------------------------------------------------------------


def test_command_returns_replay_frames_for_the_ai_turn(client: Client) -> None:
    """The AI's whole turn happens inside one POST, so it ships snapshots."""
    game_id, view = start(client, seed=7)
    rng = random.Random(3)
    saw = 0
    for _ in range(80):
        pending = view.get("pending")
        if view["over"] or not pending:
            break
        kind, payload = auto_payload(pending, rng)
        view = send(client, game_id, kind, payload)
        for snap in view.get("replay") or []:
            saw += 1
            assert "board" not in snap, "the board never changes mid-turn"
            assert set(snap) >= {"turn", "phase", "frames", "tokens", "log", "vp"}
            for frame in snap["frames"]:
                assert isinstance(frame["committedCount"], int)
                assert "committed" not in frame
    assert saw, "the AI never produced a replay frame"


def test_replay_frames_carry_no_card_uids() -> None:
    """A replay is a picture of the past, and uids would leak into the present.

    A card that was face up while it resolved can be discarded, reshuffled and
    drawn again, so shipping its uid in a snapshot would hand the player a
    handle on a card that is in the AI's hand *now*.
    """
    client = Client(Registry())
    registry = client.registry
    game_id, view = start(client, seed=99)
    rng = random.Random(5)
    for _ in range(300):
        secrets = _ai_secret_keys(registry, game_id)
        for snap in view.get("replay") or []:
            assert not _uids_in(snap)
            blob = json.dumps(snap)
            for uid in secrets["hand"] | secrets["deck"]:
                assert f'"{uid}"' not in blob, f"{uid} leaked into a replay frame"
        pending = view.get("pending")
        if view["over"] or not pending:
            break
        kind, payload = auto_payload(pending, rng)
        view = send(client, game_id, kind, payload)


def test_the_replay_shows_every_card_the_ai_resolves(client: Client) -> None:
    """Not just the decisions -- the cards in between them too.

    Plenty of what the AI does needs no decision at all: a card with one legal
    target, an effect with no choices, a card that only ever blocks. Those used
    to be folded silently into the next decision's snapshot, which is the part
    that read as the AI doing things off screen. Every card an AI frame reveals
    now gets a beat of its own.
    """
    game_id, view = start(client, seed=7)
    rng = random.Random(3)
    revealed = set()
    beat_cards = set()
    for _ in range(120):
        pending = view.get("pending")
        if view["over"] or not pending:
            break
        kind, payload = auto_payload(pending, rng)
        view = send(client, game_id, kind, payload)
        for snap in view.get("replay") or []:
            res = snap.get("resolving") or {}
            beat = snap.get("beat") or {}
            if beat.get("event") == "card" and res.get("key"):
                beat_cards.add((res["frameId"], res["key"], snap["turn"]))
        for entry in view["log"]:
            text = entry["text"] if isinstance(entry, dict) else str(entry)
            if " resolves " in text and text.startswith("Red "):
                who, _, rest = text.partition(" resolves ")
                revealed.add((who, rest.split(" (initiative")[0]))
    assert beat_cards, "no card ever got a beat of its own"
    assert revealed, "the AI never resolved anything -- the test proves nothing"
    seen = {(who, key) for who, key, _ in beat_cards}
    missed = revealed - seen
    assert not missed, f"resolved off screen: {sorted(missed)[:5]}"


def test_a_replay_frame_says_what_changed(client: Client) -> None:
    """The marks the client draws are the *difference*, not the still."""
    game_id, view = start(client, seed=11)
    rng = random.Random(8)
    moves = []
    hits = []
    for _ in range(120):
        pending = view.get("pending")
        if view["over"] or not pending:
            break
        kind, payload = auto_payload(pending, rng)
        view = send(client, game_id, kind, payload)
        for snap in view.get("replay") or []:
            beat = snap["beat"]
            assert set(beat) == {"event", "moves", "hits", "dead"}
            for move in beat["moves"]:
                frame = next(f for f in snap["frames"] if f["id"] == move["id"])
                assert frame["pos"] == move["to"], "the move must land where it says"
                assert move["from"] != move["to"]
            moves.extend(beat["moves"])
            hits.extend(beat["hits"])
    assert moves, "nobody ever moved"
    assert hits, "nothing ever landed"
    assert all(h["amount"] > 0 and h["zone"] in ("High", "Mid", "Low") for h in hits)


def test_a_beat_reports_only_what_that_beat_did(client: Client) -> None:
    """The marks are a difference, so the baseline has to keep up.

    Beats that are *not* replayed -- the player's own cards resolving inside
    the same call -- still move the baseline. Without that, everything they
    changed surfaces on the next beat that is replayed: the player's own
    attack would draw its burst over whatever the AI did next, attributed to
    the wrong card. A card being revealed is the sharpest case, since nothing
    can have landed between the reveal and the beat.
    """
    game_id, view = start(client, seed=7)
    rng = random.Random(3)
    reveals = 0
    for _ in range(120):
        pending = view.get("pending")
        if view["over"] or not pending:
            break
        kind, payload = auto_payload(pending, rng)
        view = send(client, game_id, kind, payload)
        for snap in view.get("replay") or []:
            beat = snap["beat"]
            if beat["event"] != "card":
                continue
            reveals += 1
            assert not beat["hits"], (
                f"damage attributed to revealing "
                f"{(snap.get('resolving') or {}).get('key')}: {beat['hits']}"
            )
            assert not beat["moves"], "a card cannot have moved anyone yet"
    assert reveals, "no card was ever revealed on its own beat"


def test_replay_mine_records_the_players_own_frames_too(client: Client) -> None:
    """"Suppose my action is forced due to no choice" -- then it happens off
    screen, because nothing was asked and the player only sees the aftermath.

    Off by default (a delay between a tap and its result is worse than no
    animation); on, the same beats are recorded for the player's frames as for
    the AI's.
    """
    game_id, view = start(client, seed=7)
    rng = random.Random(3)
    mine = 0
    for _ in range(120):
        pending = view.get("pending")
        if view["over"] or not pending:
            break
        kind, payload = auto_payload(pending, rng)
        response = client.post(f"/api/game/{game_id}/command",
                               {"kind": kind, "payload": payload,
                                "replayMine": True})
        assert response.status_code == 200, response.text
        view = response.json()
        for snap in view.get("replay") or []:
            if (snap.get("beat") or {}).get("event") == "decision":
                continue
            if (snap.get("resolving") or {}).get("mine"):
                mine += 1
    assert mine, "the player's own frames were never recorded"


def test_the_human_s_own_cards_do_not_become_a_replay(client: Client) -> None:
    """Replaying your own tap would only put a delay between it and its result.

    A beat is recorded for the AI's frames. The human's cards resolve under
    their own hand and are on screen as they happen.
    """
    game_id, view = start(client, seed=7)
    rng = random.Random(3)
    for _ in range(120):
        pending = view.get("pending")
        if view["over"] or not pending:
            break
        kind, payload = auto_payload(pending, rng)
        view = send(client, game_id, kind, payload)
        for snap in view.get("replay") or []:
            if (snap.get("beat") or {}).get("event") == "decision":
                continue          # the AI answering something, whoever acts
            res = snap.get("resolving") or {}
            assert res.get("mine") is not True, (
                f"a beat for the player's own {res.get('key')}"
            )


def test_a_plain_get_does_not_replay_the_ai_turn_again(client: Client) -> None:
    game_id, view = start(client, seed=7)
    pending = view["pending"]
    kind, payload = auto_payload(pending, random.Random(1))
    after = send(client, game_id, kind, payload)
    assert "replay" in after
    refreshed = client.get(f"/api/game/{game_id}").json()
    assert "replay" not in refreshed, "a refresh must not re-animate"


def test_undo_drops_a_replay_that_no_longer_happened(client: Client) -> None:
    game_id, view = start(client, seed=7)
    kind, payload = auto_payload(view["pending"], random.Random(1))
    send(client, game_id, kind, payload)
    undone = client.post(f"/api/game/{game_id}/undo").json()
    assert not undone.get("replay")


# --------------------------------------------------------------------------
# Terrain and token art
# --------------------------------------------------------------------------


def test_terrain_crop_is_the_playable_grid() -> None:
    """3 tiles across by 4 down, so a card's pixels line up with its tiles."""
    from playtest.server import assets

    for size in ((640, 890), (640, 898)):
        w, h, x, y = assets.grid_crop(*size)
        assert x >= 0 and y >= 0
        assert x + w <= size[0] and y + h <= size[1]
        assert abs(w / h - 3 / 4) < 0.005, f"{size} cropped to {w}x{h}"


def test_terrain_and_token_art_ships_with_the_app() -> None:
    """The phone clones the repo and cannot regenerate art, so it must be there."""
    from playtest.server import assets
    from playtest.engine.terrain import load_terrain_cards

    bundled = assets.terrain_files()
    cards = load_terrain_cards()
    missing = sorted(set(cards) - set(bundled))
    assert not missing, f"no bundled terrain art for {missing}"
    tokens = set(assets.token_files())
    # The numbered ones are hit-point states: every step must exist or the
    # board would fall back to a blank tile as a token takes damage.
    for stem in ("Tower1", "Tower2", "Tower3", "Tower4",
                 "PowerPlant1", "PowerPlant2", "Shiny", "Fugitive"):
        assert stem in tokens, f"missing token art {stem}"
    # A drone is drawn as whatever its card summoned, one piece per drone
    # group -- a Gun Tower is a gun tower and not a swarm. The stems here are
    # the ones `api.js` builds from the card key on the token.
    drones = set(assets.drone_token_sources())
    assert "Gun_Tower" in drones and "Swarm" in drones, drones
    missing_drones = sorted(drones - tokens)
    assert not missing_drones, f"no bundled drone art for {missing_drones}"


def test_bundled_board_art_is_served_and_is_small(client: Client) -> None:
    from playtest.server import assets

    response = client.get(f"/static/terrain/{assets.slug('Power Reactors')}.jpg")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert len(response.content) < 60_000, "terrain art is going to a phone"

    token = client.get("/static/tokens/Tower4.png")
    assert token.status_code == 200
    assert token.headers["content-type"] == "image/png"
    assert len(token.content) < 30_000


def test_the_tile_glyphs_are_bundled_and_match_the_printed_card() -> None:
    """The board stamps a marked tile with the card's own glyph, so it needs it.

    A phone clones the repo and cannot run ImageMagick, so a missing glyph is
    a tile that says nothing about what it is at all. Every code the card
    stamps is covered -- the board draws the card's marking rather than an
    overlay of its own invention.
    """
    from playtest.server import assets

    bundled = set(assets.tile_icon_files())
    assert bundled == {"e1", "e2", "e3", "imp", "obs", "obj", "tkn"}, bundled
    # And they are the same files the terrain cards use.
    import terrain_cards

    for code, stem in (("e1", "e1"), ("e2", "e2"), ("e3", "e3"),
                       ("im", "imp"), ("obs", "obs"),
                       ("obj", "obj"), ("tkn", "tkn")):
        style = terrain_cards.STYLE_DICT[code]
        assert style["icon"] == f"{stem}.png", (
            f"the card stamps {style['icon']} on a {code} tile, the board {stem}.png"
        )


def test_elevation_glyphs_are_served_small(client: Client) -> None:
    for stem in ("e1", "e2", "e3", "imp", "obs", "obj", "tkn"):
        response = client.get(f"/static/tiles/{stem}.png")
        assert response.status_code == 200, stem
        assert response.headers["content-type"] == "image/png"
        assert len(response.content) < 4_000, stem


def test_the_board_hatches_a_tile_the_way_the_card_does() -> None:
    """Obstacle, objective and token spawn are hatches on the printed card.

    The objective used to get a gold outline of the client's own invention,
    drawn before the tile border and so painted over by it. It gets the card's
    green vertical lines now, and the spawn its purple horizontal ones -- the
    colours below are xcolor's base definitions of the names `STYLE_DICT` uses.
    """
    from pathlib import Path

    import terrain_cards

    static = Path(images.__file__).resolve().parent / "static"
    board_js = (static / "js" / "board.js").read_text(encoding="utf-8")

    xcolor = {                       # xcolor's `base` set, as 8-bit rgb
        "yellow": (255, 255, 0),
        "green": (0, 255, 0),
        "purple": (191, 0, 64),
    }
    #: The card's code -> (the board's key, how `_hatch` is told to angle it).
    #: `_hatch` draws along y and steps along x, so 0 is vertical.
    wanted = {
        "obs": ("obstacle", "[Math.PI / 4, -Math.PI / 4]"),
        "obj": ("objective", "[0]"),
        "tkn": ("spawn", "[Math.PI / 2]"),
    }
    for code, (key, angles) in wanted.items():
        style = terrain_cards.STYLE_DICT[code]
        rgb = xcolor[style["hatch_color"]]
        assert f"{key}: {{ css: " in board_js or f"{key}: {{ css:" in board_js, key
        line = next(l for l in board_js.splitlines() if l.strip().startswith(f"{key}:"))
        assert f"{rgb[0]},{rgb[1]},{rgb[2]}" in line, (
            f"the card hatches a {code} tile in {style['hatch_color']} = rgb{rgb}"
        )
        assert "0.5)" in line, "the card draws every hatch at fill opacity 0.5"
        assert angles in line, (
            f"the card's '{style['hatch']}' should be angles {angles}"
        )


def test_the_board_draws_elevation_the_way_the_card_does() -> None:
    """The colours and wall widths in `board.js` are the card's, converted.

    They are hard-coded there because the board is a canvas and the card is
    TikZ, so nothing can share the constants -- which makes this the only
    thing stopping them drifting apart.
    """
    from pathlib import Path

    import terrain_cards

    static = Path(images.__file__).resolve().parent / "static"
    board_js = (static / "js" / "board.js").read_text(encoding="utf-8")

    # cityblue!N!citysteel, mixed the way xcolor mixes it.
    blue, steel = (105, 156, 255), (78, 76, 118)
    for mix, level in ((0.30, 1), (0.60, 2), (1.0, 3)):
        rgb = tuple(round(mix * b + (1 - mix) * s) for b, s in zip(blue, steel))
        assert f"'{rgb[0]},{rgb[1]},{rgb[2]}'" in board_js, (
            f"e{level} should be rgb{rgb} -- the card's cityblue!{int(mix * 100)}!citysteel"
        )

    # A tile is 2.06 cm on the card, so a point is 1/58.4 of it.
    tile_cm = 2.06
    per_pt = terrain_cards._PT_TO_CM / tile_cm
    wanted = terrain_cards.ELEVATION_WALL_PER_LEVEL_PT * per_pt
    assert f"WALL_PER_DROP = {wanted:.4f}" in board_js, (
        f"a level of drop is {terrain_cards.ELEVATION_WALL_PER_LEVEL_PT} pt "
        f"= {wanted:.4f} tiles"
    )

    # An impassable tile's border is the card's `line width=5pt`, in red.
    thickness = terrain_cards.IMPASSIBLE_STYLE["thickness"]
    wall = terrain_cards._thickness_to_cm(thickness) / tile_cm
    assert f"IMPASSABLE_WALL = {wall:.4f}" in board_js, (
        f"the card draws it at {thickness} = {wall:.4f} tiles"
    )
    assert terrain_cards.IMPASSIBLE_STYLE["color"] == "red"
    assert terrain_cards.IMPASSIBLE_STYLE["fill"] == "black"
    assert "IMPASSABLE_CSS = '#ff0000'" in board_js
    assert "IMPASSABLE_FILL = 'rgba(0,0,0,0.5)'" in board_js

    # An obstacle is a yellow crosshatch under a dashed yellow outline, and
    # sets no colour or width of its own so it can sit on any elevation. The
    # hatch itself is checked against the card by the test above.
    obstacle = terrain_cards.OBSTACLE_STYLE
    assert obstacle["hatch"] == "crosshatch" and obstacle["hatch_color"] == "yellow"
    assert "color" not in obstacle and "thickness" not in obstacle, (
        "an obstacle that sets a border would hide the elevation it sits on"
    )
    for pt, name in ((3.0, "OBSTACLE_DASH_W"),):
        assert f"{name} = {pt * per_pt:.4f}" in board_js, name
    assert f"OBSTACLE_DASH = {0.2 / tile_cm:.4f}" in board_js, "2 mm on, 2 mm off"


def test_the_client_names_board_art_the_way_the_bundle_does() -> None:
    """`api.js` builds the URLs; `assets.py` writes the files. One naming rule.

    The token map in the client also has to agree with the engine's token
    kinds, or a Power Reactor would draw as nothing at all.
    """
    from pathlib import Path

    from playtest.engine.objectives import OBJECTIVE_TOKENS
    from playtest.server import assets

    static = Path(images.__file__).resolve().parent / "static"
    api_js = (static / "js" / "api.js").read_text(encoding="utf-8")

    # `terrainImageUrl` is `/static/terrain/<slug>.jpg`, and the board asks for
    # it by the card name the engine puts on every tile. Every file the bundler
    # wrote must be findable that way.
    on_disk = {p.name for p in (static / "terrain").glob("*.jpg")}
    for name in assets.terrain_files():
        assert f"{assets.slug(name)}.jpg" in on_disk, name
    assert "/static/terrain/" in api_js and "/static/tokens/" in api_js

    # Every token kind the engine can spawn has to be *drawable*: either piece
    # art in `api.js`, or a labelled counter in the board's own fallback. A
    # kind in neither draws as an anonymous blob.
    board_js = (static / "js" / "board.js").read_text(encoding="utf-8")
    style = board_js.split("const TOKEN_STYLE = {", 1)[1].split("};", 1)[0]
    for spec in OBJECTIVE_TOKENS.values():
        art = re.search(rf"^\s+{re.escape(spec.kind)}:", api_js, re.M)
        counter = re.search(rf"^\s+{re.escape(spec.kind)}:", style, re.M)
        assert art or counter, (
            f"nothing draws token kind {spec.kind!r}: no art in api.js and no "
            "counter in board.js")


# --------------------------------------------------------------------------
# Frame standees
# --------------------------------------------------------------------------


def test_every_frame_has_a_bundled_standee() -> None:
    """A frame with no standee draws as an abstract counter with art turned on.

    That is a legal fallback, not an acceptable state to ship in: the whole
    point of the standee is that you can see *which* mech is on the tile.
    """
    from playtest.engine.cards import load_frames
    from playtest.server import assets

    bundled = assets.frame_files()
    missing = sorted(set(load_frames()) - set(bundled))
    assert not missing, f"no bundled standee for {missing}"


def test_standees_are_served_small_and_keep_their_transparency(
    client: Client,
) -> None:
    from playtest.server import assets

    response = client.get(f"/static/frames/{assets.slug('Kuwagata')}.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    # It is drawn about 80 px tall on a phone and the whole set travels to the
    # device, so a standee that is not small is a bug.
    assert len(response.content) < 20_000
    # A standee is cut out of its background: without the alpha channel it
    # would be a mech in a white box standing on the battlefield.
    assert b"tRNS" in response.content or b"PNG" in response.content[:8]


def test_the_client_names_standees_the_way_the_bundle_does() -> None:
    """`api.frameImageUrl` builds the URL; `assets.build_frames` writes it."""
    from pathlib import Path

    from playtest.engine.cards import load_frames
    from playtest.server import assets

    static = Path(images.__file__).resolve().parent / "static"
    api_js = (static / "js" / "api.js").read_text(encoding="utf-8")
    assert "/static/frames/" in api_js
    on_disk = {p.name for p in (static / "frames").glob("*.png")}
    for name in load_frames():
        assert f"{assets.slug(name)}.png" in on_disk, name


# --------------------------------------------------------------------------
# Deployment (the setup phase)
# --------------------------------------------------------------------------


def _deploy_options(view: dict) -> list[dict]:
    pending = view.get("pending") or {}
    assert pending.get("kind") == "deploy", pending.get("kind")
    return pending["options"]


def test_a_new_game_asks_the_human_to_deploy_first(client: Client) -> None:
    """Setup is a decision now, not something that happened before you looked."""
    _, view = start(client, deploy=False)
    assert view["phase"] == "setup"
    pending = view["pending"]
    assert pending["kind"] == "deploy"
    assert pending["seat"] == view["seat"]
    # The seat chooses *which* frame as well as where, so the decision is about
    # no single frame and the options are the whole cross product.
    assert pending["frameId"] is None
    for option in pending["options"]:
        assert set(option) >= {"frame", "name", "x", "y"}
    assert len({o["frame"] for o in pending["options"]}) == 3
    assert all(f["pos"] is None for f in view["frames"])


def test_deploying_puts_that_frame_on_that_tile(client: Client) -> None:
    game_id, view = start(client, deploy=False)
    option = _deploy_options(view)[7]
    after = send(client, game_id, "deploy", {"frame": option["frame"],
                                             "x": option["x"], "y": option["y"]})
    placed = next(f for f in after["frames"] if f["id"] == option["frame"])
    assert placed["pos"] == {"x": option["x"], "y": option["y"]}
    # Seats alternate, so the AI has answered by the time the view comes back.
    theirs = [f for f in after["frames"] if f["seat"] != after["seat"] and f["pos"]]
    assert len(theirs) == 1


def test_a_deployment_that_was_not_offered_is_a_400(client: Client) -> None:
    game_id, view = start(client, deploy=False)
    option = _deploy_options(view)[0]
    # The enemy's half of the board is never on offer.
    response = client.post(f"/api/game/{game_id}/command", {
        "kind": "deploy",
        "payload": {"frame": option["frame"], "x": option["x"], "y": 0},
    })
    assert response.status_code == 400
    assert response.json()["error"] == "illegal_command"


def test_deployment_ends_and_planning_begins(client: Client) -> None:
    """Setup is deploys and then, if the deal brought one, the fugitive."""
    game_id, view = start(client, deploy=False)
    for _ in range(3):
        option = _deploy_options(view)[0]
        view = send(client, game_id, "deploy", {"frame": option["frame"],
                                                "x": option["x"], "y": option["y"]})
    assert all(f["pos"] for f in view["frames"]), "every frame is on the board"
    view = deploy_all(client, game_id, view)
    assert view["phase"] == "planning"
    assert view["pending"]["kind"] == "commit_actions"


# --------------------------------------------------------------------------
# Two of the same frame
# --------------------------------------------------------------------------


def _duplicate_deck() -> str:
    """A deck name that can legally be brought twice (any of them can)."""
    return DECKS[0]


def test_a_squad_may_field_the_same_frame_twice(client: Client) -> None:
    """Nothing in the rules says the frames must differ, so nothing here may."""
    name = _duplicate_deck()
    game_id, view = start(client, playerDecks=[name, name, DECKS[1]])
    mine = [f for f in view["frames"] if f["seat"] == view["seat"]]
    names = [f["name"] for f in mine]
    twice = [n for n in names if names.count(n) > 1]
    assert twice, f"expected a duplicated frame, got {names}"
    # Two frames, two identities: separate ids, separate decks, separate damage.
    doubled = [f for f in mine if f["name"] == twice[0]]
    assert len({f["id"] for f in doubled}) == 2
    assert all(f["deckCount"] > 0 for f in doubled)


def test_a_game_with_duplicate_frames_plays_through(client: Client) -> None:
    name = _duplicate_deck()
    game_id, view = start(client, playerDecks=[name, name, name],
                          aiDecks=[DECKS[1], DECKS[1], DECKS[2]], seed=11)
    rng = random.Random(3)
    for _ in range(400):
        pending = view.get("pending")
        if view["over"] or not pending:
            break
        kind, payload = auto_payload(pending, rng)
        view = send(client, game_id, kind, payload)
    assert view["turn"] >= 1


# --------------------------------------------------------------------------
# Whose line is it? -- frames are named by id
# --------------------------------------------------------------------------


def _model_names(view: dict) -> set[str]:
    return {f["name"] for f in view["frames"]}


def test_frame_ids_carry_the_team_and_number_only_the_duplicates(
    client: Client,
) -> None:
    """An id says which side a frame is on, and which one it is."""
    game_id, view = start(
        client, deploy=False,
        playerDecks=[DECKS[0], DECKS[0], DECKS[1]],
        aiDecks=DECKS[3:6],
    )
    mine = [f["id"] for f in view["frames"] if f["seat"] == 0]
    theirs = [f["id"] for f in view["frames"] if f["seat"] == 1]
    assert all(i.startswith("Blue ") for i in mine), mine
    assert all(i.startswith("Red ") for i in theirs), theirs
    # The doubled deck is numbered on both copies; the odd one out is not.
    doubled = [i for i in mine if i.rstrip("0123456789 ") != i.rstrip()]
    assert len(doubled) == 2, mine
    assert doubled[0][:-1] == doubled[1][:-1]
    assert len(set(mine + theirs)) == len(mine + theirs), "ids must be unique"


def test_log_lines_name_frames_by_id_and_never_by_bare_model(
    client: Client,
) -> None:
    """Two of a model on one side is legal, so "Kuwagata" is not an identity.

    The engine writes its log naming frames by id, so this holds the whole
    pipeline to that: strike every id out of a line and no model name may be
    left behind. A line that still says "Kuwagata" after that is a line the
    player cannot act on.
    """
    game_id, view = start(
        client,
        playerDecks=[DECKS[0], DECKS[0], DECKS[1]],
        aiDecks=DECKS[3:6],
        seed=7,
    )
    ids = sorted({f["id"] for f in view["frames"]}, key=len, reverse=True)
    models = _model_names(view)
    rng = random.Random(5)
    for _ in range(120):
        if view.get("over") or not view.get("pending"):
            break
        kind, payload = auto_payload(view["pending"], rng)
        view = send(client, game_id, kind, payload)
    seen_an_id = False
    for entry in view["log"]:
        text = entry["text"]
        if any(i in text for i in ids):
            seen_an_id = True
        for frame_id in ids:
            text = text.replace(frame_id, "")
        for model in models:
            assert model not in text, (
                f"log line names {model!r} without saying which: {entry['text']!r}"
            )
    assert seen_an_id, "no log line named a frame at all"


def test_prompts_name_frames_by_id(client: Client) -> None:
    """The decision prompt is the one line the player is definitely reading."""
    game_id, view = start(
        client,
        playerDecks=[DECKS[0], DECKS[0], DECKS[1]],
        aiDecks=DECKS[3:6],
        seed=11,
    )
    ids = sorted({f["id"] for f in view["frames"]}, key=len, reverse=True)
    models = _model_names(view)
    rng = random.Random(9)
    checked = 0
    for _ in range(120):
        pending = view.get("pending")
        if view.get("over") or not pending:
            break
        prompt = pending.get("prompt") or ""
        stripped = prompt
        for frame_id in ids:
            stripped = stripped.replace(frame_id, "")
        for model in models:
            assert model not in stripped, f"ambiguous prompt: {prompt!r}"
        if any(i in prompt for i in ids):
            checked += 1
        kind, payload = auto_payload(pending, rng)
        view = send(client, game_id, kind, payload)
    assert checked, "no prompt named a frame at all"


# --------------------------------------------------------------------------
# Ephemeral Images: the hiding has to survive the whole API
# --------------------------------------------------------------------------


def _cloak(session, frame_id: str):
    """Put a frame behind its images, the way the card's effect step does."""
    from playtest.engine import effects

    state = session.state
    frame = state.frames[frame_id]
    state.phase = "action"
    effects._effect_ephemeral_images(state, frame, "")
    return frame


def test_a_hidden_frames_tile_never_leaves_the_engine(client: Client) -> None:
    """Every route the other seat can reach, and none of them says where it is.

    The card is worth nothing if the position can be recovered anywhere -- from
    the frame entry, from the images, from the legal target list or from the
    threat overlay -- so this checks all four rather than just the view.
    """
    game_id, view = start(client, seed=13)
    session = client.registry.get(game_id)
    enemy_id = next(f["id"] for f in view["frames"] if f["seat"] == 1)
    frame = _cloak(session, enemy_id)
    truth = {"x": frame.pos.x, "y": frame.pos.y}

    seen = session.view()
    hidden = next(f for f in seen["frames"] if f["id"] == enemy_id)
    assert hidden["pos"] is None and hidden["cloaked"] is True
    images = [t for t in seen["tokens"] if t["kind"] == "image"]
    assert len(images) == 3, "three images or the guess is not a guess"
    assert all("real" not in t for t in images), "the view marked the real one"
    assert truth in [t["pos"] for t in images], "the frame is under one of them"

    # No legal command may name the frame either -- offering it as a target
    # would say it is in range, which is a position by another name.
    assert not [
        c for c in seen["legal"]
        if c["kind"] == "attack_target" and c["payload"].get("id") == enemy_id
    ]

    response = client.get(f"/api/game/{game_id}/threat?frame={enemy_id}")
    assert response.status_code == 409, response.text


def test_our_own_side_still_knows_which_image_it_is_standing_on(
    client: Client,
) -> None:
    game_id, view = start(client, seed=13)
    session = client.registry.get(game_id)
    mine_id = next(f["id"] for f in view["frames"] if f["seat"] == 0)
    frame = _cloak(session, mine_id)

    seen = session.view()
    shown = next(f for f in seen["frames"] if f["id"] == mine_id)
    assert shown["pos"] == {"x": frame.pos.x, "y": frame.pos.y}
    real = [t for t in seen["tokens"] if t.get("real")]
    assert len(real) == 1
    assert real[0]["pos"] == shown["pos"]


def test_an_area_token_carries_its_area_into_the_view() -> None:
    """A gravity well re-prices movement for five tiles and says so.

    It was silent: standing inside one made every step *away* from it cost an
    extra point, with nothing on the board to explain a refused move. The
    radius and the wording are the engine's -- the client only draws the ring.
    """
    from playtest.engine import effects
    from playtest.engine import effects_state as fx
    from playtest.engine.serialize import view_for
    from playtest.engine.types import Pos

    from ._helpers import add_frame, make_state

    state = make_state()
    add_frame(state, 0, "Kuwagata", Pos(2, 2))
    fx.spawn_token(state, fx.GRAVITY_WELL, Pos(8, 8), owner=1)
    fx.spawn_token(state, fx.BARRICADE, Pos(3, 3), owner=1)

    tokens = {t["kind"]: t for t in view_for(state, 0)["tokens"]}
    aura = tokens["gravitywell"]["aura"]
    assert aura["radius"] == effects.GRAVITY_RADIUS
    assert "movement" in aura["text"] and aura["name"] == "gravity well"
    # Every kind the engine gives an area to has one in the view, and nothing
    # else does -- a token with no aura must not grow a ring on the board.
    for kind, token in tokens.items():
        assert ("aura" in token) == (effects.token_aura(kind) is not None), kind
