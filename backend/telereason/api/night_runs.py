from fastapi import APIRouter, Request

from telereason.api.fake import fixture_response
from telereason.api.schemas import NightRunProgress, NightRunRequest, NightRunStarted

router = APIRouter()


@router.post("/api/night-runs", response_model=NightRunStarted, status_code=202)
def start_night_run(request: Request, body: NightRunRequest) -> NightRunStarted:
    return fixture_response(
        request, NightRunStarted, "night_run_started.json", "POST /api/night-runs"
    )


@router.get("/api/night-runs/{run_id}", response_model=NightRunProgress)
def get_night_run(request: Request, run_id: str) -> NightRunProgress:
    # Fake mode has one stored night, so every run id gets its progress.
    return fixture_response(
        request, NightRunProgress, "night_run.json", "GET /api/night-runs/{run_id}"
    )
