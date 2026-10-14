"""Fixture-backed responses for ENGINE=fake, so the frontend runs without GPU or real data.

Fixtures are found through the window data store (`DataStore.fixture_root`), the same path
the window endpoints use. With ENGINE=vllm these helpers raise `not_implemented` until the
real endpoints land in their own issues; endpoints of the stored night are the exception
while NIGHT_SOURCE=stored (#26, #27 replace them).
"""

import asyncio
import random
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import Request
from pydantic import BaseModel

from telereason.api.errors import not_implemented
from telereason.settings import Settings

# Pause between replayed SSE events, in seconds, so the stream looks like a live answer.
EVENT_DELAY_S = (0.3, 0.8)


def is_fake(request: Request) -> bool:
    settings: Settings = request.app.state.settings
    return settings.engine == "fake"


def fixture_path(request: Request, name: str) -> Path:
    return request.app.state.data_store.fixture_root / name


def serves_stored_night(request: Request) -> bool:
    settings: Settings = request.app.state.settings
    return settings.night_source == "stored"


def fixture_response[M: BaseModel](
    request: Request, model: type[M], name: str, endpoint: str, *, stored_night: bool = False
) -> M:
    """The fixture `name` in fake mode; with `stored_night`, also in real mode while
    NIGHT_SOURCE=stored."""
    if not (is_fake(request) or (stored_night and serves_stored_night(request))):
        raise not_implemented(endpoint)
    return model.model_validate_json(fixture_path(request, name).read_text(encoding="utf-8"))


def event_delay_s() -> float:
    low, high = EVENT_DELAY_S
    return random.uniform(low, high)


async def replay_sse(path: Path) -> AsyncIterator[str]:
    """Yield the messages of a recorded SSE stream, pausing between them."""
    messages = path.read_text(encoding="utf-8").strip().split("\n\n")
    for index, message in enumerate(messages):
        if index:
            await asyncio.sleep(event_delay_s())
        yield message + "\n\n"
