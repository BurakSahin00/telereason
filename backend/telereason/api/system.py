from fastapi import APIRouter, Request

from telereason.api.fake import fixture_response
from telereason.api.schemas import SystemResponse

router = APIRouter()


@router.get("/api/system", response_model=SystemResponse)
def system(request: Request) -> SystemResponse:
    return fixture_response(request, SystemResponse, "system.json", "GET /api/system")
