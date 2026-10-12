from typing import Annotated, Literal

from fastapi import APIRouter, Query

from telereason.api.errors import not_implemented
from telereason.api.schemas import Line, SignalsResponse, WindowDetail, WindowListResponse

router = APIRouter()


@router.get("/api/windows", response_model=WindowListResponse)
def list_windows(
    line: Line | None = None,
    flagged: bool | None = None,
    sort: Literal["score", "deviation"] | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> WindowListResponse:
    raise not_implemented("GET /api/windows")


@router.get("/api/windows/{window_id}", response_model=WindowDetail)
def get_window(window_id: str) -> WindowDetail:
    raise not_implemented("GET /api/windows/{window_id}")


@router.get("/api/windows/{window_id}/signals", response_model=SignalsResponse)
def get_signals(
    window_id: str,
    channels: Annotated[
        str | None,
        Query(description="Comma-separated channel names; default: look_first plus two more."),
    ] = None,
) -> SignalsResponse:
    raise not_implemented("GET /api/windows/{window_id}/signals")
