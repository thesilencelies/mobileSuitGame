"""`python -m playtest.server` -- run the playtest app on this device.

The default is loopback: the browser and the server are on the same phone, and
nothing should be reachable from a network. `--host` exists for desktop
development and prints a warning when it is used.

Nothing in this file may name a third-party package, not even inside a guarded
function -- `test_the_shipped_server_needs_nothing_but_the_standard_library`
asserts that statically, because a stray `import uvicorn` here is the
difference between an app that starts under Termux and one that does not. The
optional FastAPI development path lives in `app.py`, which is the only module
allowed to know that FastAPI exists.
"""

from __future__ import annotations

import argparse
import sys

from .httpd import DEFAULT_HOST, DEFAULT_PORT, lan_addresses, serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m playtest.server",
        description="NetFrame playtest app (offline, runs on this device)",
    )
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help="bind address (default 127.0.0.1: this device only)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--quiet", action="store_true", help="no request logging")
    parser.add_argument("--dev-server", action="store_true",
                        help="desktop only: use the optional dev server in app.py")
    args = parser.parse_args(argv)

    print(f"NetFrame playtest — http://127.0.0.1:{args.port}/", flush=True)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"  WARNING: bound to {args.host}, so this is reachable from the "
              f"network. The supported setup is loopback only.", flush=True)
        for addr in lan_addresses():
            print(f"    also at  http://{addr}:{args.port}/", flush=True)
    print("  Ctrl-C to stop.", flush=True)

    if args.dev_server:
        try:
            from .app import serve_dev
        except Exception as exc:                 # the dev extras are not installed
            print(f"dev server unavailable ({exc}); using the stdlib server.",
                  file=sys.stderr, flush=True)
        else:
            return serve_dev(args.host, args.port, quiet=args.quiet)

    serve(args.host, args.port, quiet=args.quiet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
