"""Answer the always-on demo from the live MI300X backend while that backend is up.

The demo runs in fake mode. With REMOTE_API_URL set, every /api request is passed on to that
URL while its /api/health says ready with a real engine, and the answer is streamed back as it
arrives. Otherwise the request is answered here from the recorded night, exactly as without this
module. The health of the live backend is checked at most once per CHECK_INTERVAL_S, and a
request never waits more than CHECK_TIMEOUT_S for that check. A request that cannot reach the
live backend is answered here, and the next check comes after CHECK_INTERVAL_S.

The live backend runs with API_KEY set to the demo's REMOTE_API_KEY, so only the demo can use
it: every /api request to it must carry the key in KEY_HEADER (`RequireKey`).
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import time
from collections.abc import Callable

import httpx
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from telereason.api.errors import error_response
from telereason.api.rate_limit import client_key

KEY_HEADER = "x-telereason-key"
CHECK_INTERVAL_S = 15.0
CHECK_TIMEOUT_S = 3.0
# Between two events of a streamed answer; a night note or a tool call takes far less.
READ_TIMEOUT_S = 120.0
# Request headers passed on. Cookies, Origin and the client's own proxy headers stay here.
FORWARDED_REQUEST_HEADERS = ("accept", "content-type")
# Response headers not passed back: hop-by-hop ones, the ones this server sets itself, and
# CORS, which this app adds for its own allowed origins.
DROPPED_RESPONSE_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "transfer-encoding",
        "content-length",
        "content-encoding",
        "date",
        "server",
    }
)
# The same per-client limits as the local routes (telereason.api.rate_limit).
RATE_LIMITED = {("POST", "/api/ask"): "ask", ("POST", "/api/night-runs"): "night_runs"}

logger = logging.getLogger(__name__)


class RemoteBackend:
    """The live backend and whether it was ready at the last check."""

    def __init__(
        self,
        url: str,
        key: str,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.url = url
        self.clock = clock
        self.client = httpx.AsyncClient(
            base_url=url,
            transport=transport,
            headers={KEY_HEADER: key, "accept-encoding": "identity"},
            timeout=httpx.Timeout(10.0, connect=CHECK_TIMEOUT_S, read=READ_TIMEOUT_S),
        )
        self.ready = False
        self.checked_at: float | None = None
        self._lock = asyncio.Lock()

    async def is_ready(self) -> bool:
        now = self.clock()
        if self.checked_at is not None and now - self.checked_at < CHECK_INTERVAL_S:
            return self.ready
        if self._lock.locked():
            # Another request is checking right now; use the last answer instead of waiting.
            return self.ready
        async with self._lock:
            ready = await self._check()
            if ready != self.ready:
                logger.info("live backend %s is %s", self.url, "ready" if ready else "not ready")
            self.ready = ready
            self.checked_at = self.clock()
        return self.ready

    async def _check(self) -> bool:
        try:
            response = await self.client.get("/api/health", timeout=CHECK_TIMEOUT_S)
            body = response.json()
        except (httpx.HTTPError, ValueError):
            return False
        return (
            response.status_code == 200
            and isinstance(body, dict)
            and body.get("ready") is True
            and body.get("engine") != "fake"
        )

    def mark_down(self) -> None:
        """Answer locally until the next check."""
        if self.ready:
            logger.warning("live backend %s stopped answering", self.url)
        self.ready = False
        self.checked_at = self.clock()

    async def close(self) -> None:
        await self.client.aclose()


class RemoteProxy:
    """ASGI middleware: /api requests go to the live backend while it is ready."""

    def __init__(self, app: ASGIApp, remote: RemoteBackend) -> None:
        self.app = app
        self.remote = remote

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        if not await self.remote.is_ready():
            await self.app(scope, receive, send)
            return
        body = await read_body(receive)
        request = Request(scope)
        if limited := rate_limited(request):
            await limited(scope, receive, send)
            return
        query = scope.get("query_string", b"").decode("latin-1")
        url = scope["path"] + (f"?{query}" if query else "")
        headers = {
            name: value
            for name, value in request.headers.items()
            if name in FORWARDED_REQUEST_HEADERS
        }
        client = self.remote.client
        try:
            upstream = await client.send(
                client.build_request(request.method, url, headers=headers, content=body),
                stream=True,
            )
        except httpx.HTTPError as exc:
            logger.warning("live backend unreachable (%s); answering locally", exc)
            self.remote.mark_down()
            await self.app(scope, replay(body, receive), send)
            return
        if upstream.status_code == 401:
            # The key no longer matches: answer locally rather than pass the error on.
            await upstream.aclose()
            self.remote.mark_down()
            await self.app(scope, replay(body, receive), send)
            return
        try:
            await send(
                {
                    "type": "http.response.start",
                    "status": upstream.status_code,
                    "headers": response_headers(upstream),
                }
            )
            async for chunk in upstream.aiter_bytes():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
        except httpx.HTTPError as exc:
            logger.warning("live backend stream broke off (%s)", exc)
            self.remote.mark_down()
        finally:
            await upstream.aclose()
        await send({"type": "http.response.body", "body": b"", "more_body": False})


class RequireKey:
    """ASGI middleware for the live backend: /api requests must carry the demo's key."""

    def __init__(self, app: ASGIApp, key: str) -> None:
        self.app = app
        self.key = key.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith("/api/"):
            given = dict(scope.get("headers", [])).get(KEY_HEADER.encode(), b"")
            if not hmac.compare_digest(given, self.key):
                response = error_response(401, "bad_request", "This API is used by the demo only.")
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def rate_limited(request: Request) -> Response | None:
    """A 429 response when the client has used up its limit on this route, else None."""
    name = RATE_LIMITED.get((request.method, request.url.path))
    if name is None:
        return None
    state = request.app.state
    key = client_key(request, state.settings.trust_proxy_headers)
    if wait_s := state.rate_limiters[name].acquire(key):
        return error_response(
            429,
            "rate_limited",
            f"Too many requests; try again in {wait_s} s.",
            {"Retry-After": str(wait_s)},
        )
    return None


def response_headers(upstream: httpx.Response) -> list[tuple[bytes, bytes]]:
    return [
        (name.encode("latin-1"), value.encode("latin-1"))
        for name, value in upstream.headers.multi_items()
        if name.lower() not in DROPPED_RESPONSE_HEADERS
        and not name.lower().startswith("access-control-")
    ]


async def read_body(receive: Receive) -> bytes:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] != "http.request":
            break
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


def replay(body: bytes, receive: Receive) -> Receive:
    """A `receive` that gives the body read already, then waits on the client as before."""
    sent = False

    async def receive_again() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return receive_again
