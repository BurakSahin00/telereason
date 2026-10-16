from fastapi import APIRouter, Depends, Request

from telereason.ai.engine import Engine, EngineNotReadyError
from telereason.api.errors import ApiError
from telereason.api.fake import fixture_response, is_fake
from telereason.api.rate_limit import limit
from telereason.api.schemas import NightRunProgress, NightRunRequest, NightRunStarted
from telereason.night.run import NightRunner, RunnerBusyError
from telereason.store.db import RunRecord

router = APIRouter()


def progress(run: RunRecord) -> NightRunProgress:
    return NightRunProgress(
        run_id=run.run_id,
        status=run.status,
        step=run.step,
        done=run.done,
        total=run.total,
        timings_s=run.step_timings,
    )


@router.post(
    "/api/night-runs",
    dependencies=[Depends(limit("night_runs"))],
    response_model=NightRunStarted,
    status_code=202,
)
async def start_night_run(request: Request, body: NightRunRequest) -> NightRunStarted:
    if is_fake(request):
        return fixture_response(
            request, NightRunStarted, "night_run_started.json", "POST /api/night-runs"
        )
    engine: Engine = request.app.state.engine
    if not engine.ready:
        raise EngineNotReadyError("engine is not ready")
    runner: NightRunner = request.app.state.night_runner
    try:
        run = runner.start(body.max_windows)
    except RunnerBusyError as exc:
        raise ApiError(409, "busy", str(exc)) from exc
    return NightRunStarted(run_id=run.run_id, status=run.status)


@router.get("/api/night-runs/{run_id}", response_model=NightRunProgress)
def get_night_run(request: Request, run_id: str) -> NightRunProgress:
    if is_fake(request):
        # Fake mode has one stored night, so every run id gets its progress.
        return fixture_response(
            request, NightRunProgress, "night_run.json", "GET /api/night-runs/{run_id}"
        )
    runner: NightRunner = request.app.state.night_runner
    run = runner.get(run_id)
    if run is None:
        raise ApiError(404, "not_found", f"Night run {run_id} does not exist.")
    return progress(run)
