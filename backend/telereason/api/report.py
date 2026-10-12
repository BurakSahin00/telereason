from fastapi import APIRouter

from telereason.api.errors import not_implemented
from telereason.api.schemas import ReportResponse

router = APIRouter()


@router.get("/api/report/latest", response_model=ReportResponse)
def latest_report() -> ReportResponse:
    raise not_implemented("GET /api/report/latest")
