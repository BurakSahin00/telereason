from fastapi import FastAPI

from telereason.api import ask, examples, gate, health, night_runs, report, system, windows
from telereason.api.errors import ERROR_RESPONSES, install_error_handlers
from telereason.settings import Settings, load_settings

ROUTERS = (health, system, gate, report, windows, ask, examples, night_runs)


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="TeleReason")
    app.state.settings = settings or load_settings()
    install_error_handlers(app)
    for module in ROUTERS:
        app.include_router(module.router, responses=ERROR_RESPONSES)
    return app


app = create_app()
