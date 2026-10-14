from fastapi import APIRouter, Request

from telereason.api.fake import fixture_response
from telereason.api.schemas import GateResponse

router = APIRouter()


@router.get("/api/gate", response_model=GateResponse)
def gate(request: Request) -> GateResponse:
    return fixture_response(request, GateResponse, "gate.json", "GET /api/gate", stored_night=True)
