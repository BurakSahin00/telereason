from fastapi import APIRouter
from fastapi.responses import JSONResponse

from telereason.api.errors import not_implemented
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
async def ask(body: AskRequest) -> None:
    raise not_implemented("POST /api/ask")
