from fastapi import APIRouter

from telereason.api.errors import not_implemented
from telereason.api.schemas import NightRunProgress, NightRunRequest, NightRunStarted

router = APIRouter()


@router.post("/api/night-runs", response_model=NightRunStarted, status_code=202)
def start_night_run(body: NightRunRequest) -> NightRunStarted:
    raise not_implemented("POST /api/night-runs")


@router.get("/api/night-runs/{run_id}", response_model=NightRunProgress)
def get_night_run(run_id: str) -> NightRunProgress:
    raise not_implemented("GET /api/night-runs/{run_id}")
