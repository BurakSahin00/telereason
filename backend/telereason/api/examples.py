from fastapi import APIRouter

from telereason.api.errors import not_implemented
from telereason.api.schemas import ExamplesResponse

router = APIRouter()


@router.get("/api/examples", response_model=ExamplesResponse)
def examples() -> ExamplesResponse:
    raise not_implemented("GET /api/examples")
