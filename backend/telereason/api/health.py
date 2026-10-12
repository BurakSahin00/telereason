from fastapi import APIRouter, Request

from telereason.api.schemas import HealthResponse
from telereason.settings import Settings

router = APIRouter()


@router.get("/api/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    settings: Settings = request.app.state.settings
    # Fake mode is ready at once; vLLM readiness comes with the engine (ai/engine.py).
    return HealthResponse(
        status="ok",
        engine=settings.engine,
        model=settings.model,
        ready=settings.engine == "fake",
    )
