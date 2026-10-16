from fastapi import APIRouter, Request

from telereason.api.fake import db_night, fixture_response, not_available
from telereason.api.schemas import GateResponse
from telereason.data.loader import DataNotAvailableError

router = APIRouter()


@router.get("/api/gate", response_model=GateResponse)
def gate(request: Request) -> GateResponse:
    # The run that finished last with NIGHT_SOURCE=db, else the stored night. A comment, not
    # a docstring: FastAPI would put a docstring into /openapi.json.
    if (night := db_night(request)) is not None:
        try:
            return night.gate()
        except DataNotAvailableError as exc:
            raise not_available(exc) from exc
    return fixture_response(request, GateResponse, "gate.json", "GET /api/gate", stored_night=True)
