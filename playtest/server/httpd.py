"""The shipped HTTP server: `http.server` and nothing else.

`playtest/engine/` and `playtest/ai/` import only the standard library. Keeping
this layer stdlib too means the whole install on an Android phone is
`pkg install python` -- no pip, no wheels, no Rust toolchain to build
pydantic-core with. That is the difference between an app that starts on a
train and one that does not.

`ThreadingHTTPServer` is used rather than the single-threaded one so a slow
card-image resize cannot block the page that is asking for it.
"""

from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, urlsplit

from .games import Registry
from .routes import Router

#: Loopback by default. The app runs on the same device as the browser, and
#: nothing about this server should ever be reachable from a network.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

#: Refuse absurd request bodies rather than reading them into memory.
MAX_BODY = 4 * 1024 * 1024


class RequestHandler(BaseHTTPRequestHandler):
    """Reads a request, hands it to the router, writes the response."""

    server_version = "NetFramePlaytest/1.0"
    protocol_version = "HTTP/1.1"
    router: Router                            # set on the server instance

    # -- verbs -----------------------------------------------------------

    def do_GET(self) -> None:
        self._run("GET")

    def do_HEAD(self) -> None:
        self._run("HEAD", body_out=False)

    def do_POST(self) -> None:
        self._run("POST")

    def do_DELETE(self) -> None:
        self._run("DELETE")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Allow", "GET, HEAD, POST, DELETE, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # -- plumbing --------------------------------------------------------

    def _run(self, method: str, body_out: bool = True) -> None:
        parts = urlsplit(self.path)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY:
            self.send_error(413, "request body too large")
            return
        body = self.rfile.read(length) if length > 0 else b""

        router: Router = self.server.router          # type: ignore[attr-defined]
        try:
            response = router.dispatch(method, parts.path, query, body)
        except Exception as exc:                     # pragma: no cover - last resort
            self.log_error("unhandled error on %s %s: %r", method, self.path, exc)
            self.send_error(500, "internal error")
            return

        payload = response.body if body_out else b""
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        for name, value in response.headers.items():
            self.send_header(name, value)
        self.end_headers()
        if payload:
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):    # pragma: no cover
                pass                                  # the phone navigated away

    def log_message(self, fmt: str, *args: object) -> None:
        # One tidy line per request; the default writes to stderr unbuffered
        # with a date stamp that is useless on a phone.
        if self.server.quiet:                        # type: ignore[attr-defined]
            return
        print(f"  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}",
              flush=True)


class PlaytestServer(ThreadingHTTPServer):
    """A `ThreadingHTTPServer` that carries the router and the registry."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], router: Router,
                 quiet: bool = False) -> None:
        super().__init__(address, RequestHandler)
        self.router = router
        self.quiet = quiet


def make_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    registry: Optional[Registry] = None,
    quiet: bool = False,
) -> PlaytestServer:
    """Build (but do not start) a server. `port=0` picks a free port."""
    return PlaytestServer((host, port), Router(registry), quiet=quiet)


def serve(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    registry: Optional[Registry] = None,
    quiet: bool = False,
) -> None:
    """Run until interrupted."""
    server = make_server(host, port, registry=registry, quiet=quiet)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


class BackgroundServer:
    """A running server on an ephemeral port -- what the tests drive.

    Used as a context manager so a test gets a real socket, a real HTTP
    round-trip and a guaranteed shutdown.
    """

    def __init__(self, registry: Optional[Registry] = None,
                 host: str = "127.0.0.1") -> None:
        self.server = make_server(host, 0, registry=registry, quiet=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    @property
    def base_url(self) -> str:
        return f"http://{self.server.server_address[0]}:{self.port}"

    def __enter__(self) -> "BackgroundServer":
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def lan_addresses() -> list[str]:
    """This machine's non-loopback IPv4 addresses (only used by `--host`)."""
    found: list[str] = []
    for probe in ("8.8.8.8", "192.168.1.1"):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((probe, 80))               # no packet is actually sent
            addr = sock.getsockname()[0]
            if addr and not addr.startswith("127.") and addr not in found:
                found.append(addr)
        except OSError:
            pass
        finally:
            sock.close()
    return found
