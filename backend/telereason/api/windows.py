from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request

from telereason.api.errors import ApiError
from telereason.api.schemas import Line, SignalsResponse, WindowDetail, WindowListResponse
from telereason.data.loader import DataNotAvailableError, WindowNotFoundError

router = APIRouter()


@router.get("/api/windows", response_model=WindowListResponse)
def list_windows(
    request: Request,
    line: Line | None = None,
    flagged: bool | None = None,
    sort: Literal["score", "deviation"] | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> WindowListResponse:
    try:
        return request.app.state.night_store.list_windows(
            line=line, flagged=flagged, sort=sort, page=page, page_size=page_size
        )
    except DataNotAvailableError as exc:
        raise ApiError(503, "engine_not_ready", str(exc)) from exc


@router.get("/api/windows/{window_id}", response_model=WindowDetail)
def get_window(request: Request, window_id: str) -> WindowDetail:
    try:
        return request.app.state.night_store.get_window(window_id)
    except WindowNotFoundError as exc:
        raise ApiError(404, "not_found", f"Window {window_id} does not exist.") from exc
    except DataNotAvailableError as exc:
        raise ApiError(503, "engine_not_ready", str(exc)) from exc


@router.get("/api/windows/{window_id}/signals", response_model=SignalsResponse)
def get_signals(
    request: Request,
    window_id: str,
    channels: Annotated[
        str | None,
        Query(description="Comma-separated channel names; default: look_first plus two more."),
    ] = None,
) -> SignalsResponse:
    try:
        return request.app.state.data_store.get_signals(window_id, channels)
    except WindowNotFoundError as exc:
        raise ApiError(404, "not_found", f"Window {window_id} does not exist.") from exc
    except DataNotAvailableError as exc:
        raise ApiError(503, "engine_not_ready", str(exc)) from exc
