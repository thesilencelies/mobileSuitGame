"""Optional FastAPI adapter -- **development convenience only**.

The shipped server is `httpd.py`, which needs nothing but the standard library,
because the app runs on an Android phone under Termux where FastAPI's pydantic
dependency has no wheel and would have to be compiled from Rust source.

This module exists so a desktop can use FastAPI's reload and its docs page. It
delegates every request to the same `Router` the stdlib server uses, so the two
cannot drift: there is exactly one routing table.

    uvicorn playtest.server.app:app --reload

Importing this module requires FastAPI. Nothing else in `playtest/` imports it.
"""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, Request, Response

from .games import Registry
from .routes import Router


def create_app(registry: Optional[Registry] = None) -> FastAPI:
    app = FastAPI(title="NetFrame playtest", version="1.0")
    router = Router(registry)
    app.state.registry = router.registry
    app.state.router = router

    @app.api_route(
        "/{path:path}",
        methods=["GET", "HEAD", "POST", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    async def _everything(path: str, request: Request) -> Response:
        body = await request.body()
        result = router.dispatch(
            request.method,
            "/" + path,
            dict(request.query_params),
            body,
        )
        return Response(
            content=result.body,
            status_code=result.status,
            media_type=result.content_type,
            headers=result.headers,
        )

    return app


app = create_app()


def serve_dev(host: str = "127.0.0.1", port: int = 8000, *,
              quiet: bool = False) -> int:
    """Run the FastAPI adapter under uvicorn. Desktop development only.

    `__main__.py` calls this behind `--dev-server` and is not allowed to name
    uvicorn itself, so the import lives here where FastAPI already does.
    """
    import uvicorn

    uvicorn.run("playtest.server.app:app", host=host, port=port,
                log_level="warning" if quiet else "info")
    return 0
