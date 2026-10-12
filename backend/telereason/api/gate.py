from fastapi import APIRouter

from telereason.api.errors import not_implemented
from telereason.api.schemas import GateResponse

router = APIRouter()


@router.get("/api/gate", response_model=GateResponse)
def gate() -> GateResponse:
    raise not_implemented("GET /api/gate")
