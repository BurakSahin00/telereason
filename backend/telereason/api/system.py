from fastapi import APIRouter

from telereason.api.errors import not_implemented
from telereason.api.schemas import SystemResponse

router = APIRouter()


@router.get("/api/system", response_model=SystemResponse)
def system() -> SystemResponse:
    raise not_implemented("GET /api/system")
