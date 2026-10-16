from fastapi import APIRouter, Request

from telereason.api.fake import db_night, fixture_response, not_available
from telereason.api.schemas import ReportResponse
from telereason.data.loader import DataNotAvailableError

router = APIRouter()


@router.get("/api/report/latest", response_model=ReportResponse)
def latest_report(request: Request) -> ReportResponse:
    # The run that finished last with NIGHT_SOURCE=db, else the stored night. A comment, not
    # a docstring: FastAPI would put a docstring into /openapi.json.
    if (night := db_night(request)) is not None:
        try:
            return night.report()
        except DataNotAvailableError as exc:
            raise not_available(exc) from exc
    return fixture_response(
        request, ReportResponse, "report_latest.json", "GET /api/report/latest", stored_night=True
    )
