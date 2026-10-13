from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from telereason.api.errors import not_implemented
from telereason.api.fake import fixture_path, is_fake, replay_sse
from telereason.api.schemas import AskRequest, AskStreamMessage

router = APIRouter()


class EventStreamSchema(JSONResponse):
    """Documents the SSE messages under text/event-stream in /openapi.json.

    The real handler returns its own StreamingResponse, so this class only shapes the docs.
    """

    media_type = "text/event-stream"


@router.post(
    "/api/ask",
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
    if not is_fake(request):
        raise not_implemented("POST /api/ask")
    return StreamingResponse(
        replay_sse(fixture_path(request, "ask.sse")),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
