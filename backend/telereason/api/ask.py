"""POST /api/ask: the live assistant as a Server-Sent Events stream.

With ENGINE=fake the recorded stream in `fixtures/ask.sse` is replayed, except for a request
to act on equipment, which gets the same refusal as in real mode. Otherwise the agent
in `ai/agent.py` runs against the engine and each of its messages becomes one SSE message.
A `: ping` comment goes out every `PING_INTERVAL_S`. A question that runs longer than
`ASK_TIMEOUT_S`, or fails, ends with an `error` message. When the client disconnects,
Starlette cancels the stream; the agent task is cancelled with it, which aborts the engine
request.
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from telereason.ai.agent import REFUSAL_TEXT, AgentMessage, is_action_request, strip_harmony_markers
from telereason.ai.agent import ask as run_agent
from telereason.ai.engine import Engine, EngineNotReadyError
from telereason.ai.tools import AgentData, ProtocolIndex
from telereason.api.fake import fixture_path, is_fake, not_available, replay_sse
from telereason.api.rate_limit import limit
from telereason.api.schemas import (
    AnswerEvent,
    AskRequest,
    AskStreamMessage,
    DoneEvent,
    ErrorCode,
    ErrorDetail,
    ErrorResponse,
    GateResponse,
    ReportResponse,
)
from telereason.data.loader import DataNotAvailableError
from telereason.store.night import DbNight, RunWindows

router = APIRouter()
logger = logging.getLogger(__name__)

PING_INTERVAL_S = 15.0
ASK_TIMEOUT_S = 60.0
PING = ": ping\n\n"
TIMEOUT_MESSAGE = f"The answer took longer than {ASK_TIMEOUT_S:.0f} s; try a narrower question."
FAILED_MESSAGE = "The assistant failed while answering; try again."
SSE_HEADERS = {"Cache-Control": "no-cache"}


class EventStreamSchema(JSONResponse):
    """Documents the SSE messages under text/event-stream in /openapi.json.

    The real handler returns its own StreamingResponse, so this class only shapes the docs.
    """

    media_type = "text/event-stream"


def sse(event: str, data: dict[str, Any]) -> str:
    """One SSE message, written like the messages in `fixtures/ask.sse`."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_error(code: ErrorCode, message: str) -> str:
    error = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return sse("error", error.model_dump())


async def refusal_stream() -> AsyncIterator[str]:
    """The refusal the agent gives in real mode, for fake mode: no model runs, so 0 tokens."""
    started = time.perf_counter()
    refusal = AnswerEvent(answer="ACTION_REFUSED", answer_source="tool", text=REFUSAL_TEXT)
    yield sse("answer", refusal.model_dump(mode="json"))
    total_ms = round((time.perf_counter() - started) * 1000)
    yield sse("done", DoneEvent(total_ms=total_ms, tokens=0).model_dump(mode="json"))


class _End:
    """Queue marker: the agent finished."""


async def stream_agent(
    messages: AsyncIterator[AgentMessage],
    *,
    ping_interval_s: float,
    timeout_s: float,
) -> AsyncIterator[str]:
    """Turn agent messages into SSE text, with pings, a time limit and error messages.

    The agent runs in its own task, so pings go out while it waits for the engine. Closing
    or cancelling this generator cancels that task.
    """
    queue: asyncio.Queue[AgentMessage | Exception | _End] = asyncio.Queue()

    async def produce() -> None:
        try:
            async for message in messages:
                await queue.put(message)
        except Exception as exc:
            await queue.put(exc)
        else:
            await queue.put(_End())

    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline = started + timeout_s
    next_ping = started + ping_interval_s
    producer = asyncio.create_task(produce())
    try:
        while True:
            now = loop.time()
            if now >= deadline:
                logger.warning("ask: no answer after %.0f s; request cancelled", timeout_s)
                yield sse_error("timeout", TIMEOUT_MESSAGE)
                return
            if now >= next_ping:
                next_ping += ping_interval_s
                yield PING
                continue
            try:
                item = await asyncio.wait_for(queue.get(), min(deadline, next_ping) - now)
            except TimeoutError:
                continue
            if isinstance(item, _End):
                return
            if isinstance(item, EngineNotReadyError):
                yield sse_error("engine_not_ready", str(item))
                return
            if isinstance(item, Exception):
                logger.error("ask: the agent failed", exc_info=item)
                yield sse_error("internal_error", FAILED_MESSAGE)
                return
            yield sse(item.event, item.data.model_dump(mode="json"))
    finally:
        # Runs on normal end, timeout, error and client disconnect alike.
        producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)


def agent_data(request: Request) -> AgentData:
    """Data the agent's tools read.

    With NIGHT_SOURCE=db the gate verdicts, the morning report and the windows are those of
    the run that finished last, loaded again when a newer run finishes. Otherwise they are the
    stored 11 Oct night in `fixtures/`, read through the same store as `/api/windows` and
    loaded once per process. Protocols come from the downloaded knowledge graph, loaded once.
    """
    state = request.app.state
    data: AgentData | None = getattr(state, "agent_data", None)
    night: DbNight | None = state.db_night
    if night is not None:
        try:
            run = night.latest()
            if data is not None and getattr(state, "agent_data_run", None) == run.run_id:
                return data
            data = AgentData(
                store=RunWindows(night.db, run),
                gate=night.gate(run),
                protocols=protocols(request),
                report=night.report(run),
            )
        except DataNotAvailableError as exc:
            raise not_available(exc) from exc
        state.agent_data, state.agent_data_run = data, run.run_id
        return data
    if data is None:
        root = state.night_store.fixture_root
        data = AgentData(
            store=state.night_store,
            gate=GateResponse.model_validate_json((root / "gate.json").read_text("utf-8")),
            protocols=protocols(request),
            report=ReportResponse.model_validate_json(
                (root / "report_latest.json").read_text("utf-8")
            ),
        )
        state.agent_data = data
    return data


def protocols(request: Request) -> ProtocolIndex:
    state = request.app.state
    index: ProtocolIndex | None = getattr(state, "protocols", None)
    if index is None:
        index = state.protocols = ProtocolIndex.load(state.settings.data_dir)
    return index


@router.post(
    "/api/ask",
    dependencies=[Depends(limit("ask"))],
    response_model=AskStreamMessage,
    response_class=EventStreamSchema,
    responses={
        200: {"description": "SSE stream; one schema item is one `event:` plus `data:` message."},
        # Errors before the stream starts are plain JSON, not SSE.
        "default": {
            "description": "Error",
            "content": {
                "application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}},
            },
        },
    },
)
async def ask(request: Request, body: AskRequest) -> StreamingResponse:
    if is_fake(request):
        # The recorded answer is about one window; a request to act on equipment must still
        # be refused, as the agent does before the model runs.
        if is_action_request(strip_harmony_markers(body.question)):
            stream = refusal_stream()
        else:
            stream = replay_sse(fixture_path(request, "ask.sse"))
    else:
        engine: Engine = request.app.state.engine
        if not engine.ready:
            raise EngineNotReadyError("engine is not ready")
        messages = run_agent(engine, agent_data(request), body.question, body.window_id)
        stream = stream_agent(messages, ping_interval_s=PING_INTERVAL_S, timeout_s=ASK_TIMEOUT_S)
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)
