from fastapi import FastAPI

from telereason.api import health
from telereason.settings import Settings, load_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="TeleReason")
    app.state.settings = settings or load_settings()
    app.include_router(health.router)
    return app


app = create_app()
