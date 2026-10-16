"""In-memory token buckets for POST /api/ask and POST /api/night-runs.

The app runs in one process (`uvicorn --workers 1`), so a dict per endpoint is enough. A client
holds up to `per_min` tokens that refill evenly over a minute; a request without a token gets
429 `rate_limited` with a Retry-After header. A limit of 0 turns the bucket off.
"""

import math
import time
from collections.abc import Awaitable, Callable

from fastapi import Request
from starlette.exceptions import HTTPException


class RateLimiter:
    def __init__(self, per_min: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_min = per_min
        self.clock = clock
        self.buckets: dict[str, tuple[float, float]] = {}  # client -> (tokens, updated)

    def acquire(self, key: str) -> int:
        """Take one token for `key`: 0 when allowed, otherwise the seconds to wait."""
        if self.per_min <= 0:
            return 0
        now, rate = self.clock(), self.per_min / 60
        tokens, updated = self.buckets.get(key, (self.per_min, now))
        tokens = min(self.per_min, tokens + (now - updated) * rate)
        if tokens >= 1:
            self.buckets[key] = (tokens - 1, now)
            return 0
        self.buckets[key] = (tokens, now)
        return max(1, math.ceil((1 - tokens) / rate))


def client_key(request: Request, trust_proxy_headers: bool) -> str:
    """The client address; proxy headers count only with TRUST_PROXY_HEADERS=1."""
    if trust_proxy_headers:
        # The client can write the first values; the proxy next to us appends the last one.
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[-1]
        address = request.headers.get("cf-connecting-ip", "").strip() or forwarded.strip()
        if address:
            return address
    return request.client.host if request.client else "unknown"


def limit(name: str) -> Callable[[Request], Awaitable[None]]:
    """A route dependency that spends one token of `app.state.rate_limiters[name]`."""

    async def dependency(request: Request) -> None:
        state = request.app.state
        key = client_key(request, state.settings.trust_proxy_headers)
        if wait_s := state.rate_limiters[name].acquire(key):
            message = f"Too many requests; try again in {wait_s} s."
            raise HTTPException(429, message, headers={"Retry-After": str(wait_s)})

    return dependency
