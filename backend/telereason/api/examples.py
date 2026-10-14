from fastapi import APIRouter, Request

from telereason.api.fake import fixture_response
from telereason.api.schemas import ExamplesResponse

router = APIRouter()


@router.get("/api/examples", response_model=ExamplesResponse)
def examples(request: Request) -> ExamplesResponse:
    return fixture_response(
        request, ExamplesResponse, "examples.json", "GET /api/examples", stored_night=True
    )
