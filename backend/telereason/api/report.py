from fastapi import APIRouter, Request

from telereason.api.fake import fixture_response
from telereason.api.schemas import ReportResponse

router = APIRouter()


@router.get("/api/report/latest", response_model=ReportResponse)
def latest_report(request: Request) -> ReportResponse:
    return fixture_response(
        request, ReportResponse, "report_latest.json", "GET /api/report/latest", stored_night=True
    )
