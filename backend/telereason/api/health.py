from fastapi import APIRouter, Request

from telereason.ai.engine import Engine
from telereason.api.schemas import HealthResponse
from telereason.settings import Settings

router = APIRouter()


@router.get("/api/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    settings: Settings = request.app.state.settings
    engine: Engine = request.app.state.engine
    # Fake mode loads nothing, so it is ready even before the lifespan starts its engine.
    return HealthResponse(
        status="ok",
        engine=settings.engine,
        model=settings.model,
        ready=settings.engine == "fake" or engine.ready,
    )
