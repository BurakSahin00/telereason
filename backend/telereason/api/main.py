import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from telereason.ai.agent import warm_up_prompt
from telereason.ai.engine import Engine, create_engine, warm_up
from telereason.api import ask, examples, gate, health, night_runs, report, system, windows
from telereason.api.errors import ERROR_RESPONSES, install_error_handlers
from telereason.api.frontend import mount_frontend
from telereason.api.rate_limit import RateLimiter
from telereason.data.loader import DataStore, WindowSource
from telereason.night.run import NightRunner
from telereason.settings import Settings, load_settings
from telereason.store.db import NightDb
from telereason.store.night import DbNight

ROUTERS = (health, system, gate, report, windows, ask, examples, night_runs)
WARMUP_PREVIEW_CHARS = 200

logger = logging.getLogger(__name__)


def log_to_stderr() -> None:
    """uvicorn configures only its own loggers; show ours at INFO next to them."""
    package = logging.getLogger("telereason")
    if not package.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        package.addHandler(handler)
        package.setLevel(logging.INFO)


async def open_engine(engine: Engine) -> None:
    """Start the engine and send one agent-sized warm-up request. A failure is logged, not
    raised: the app keeps serving and /api/health keeps reporting ready=false."""
    started = time.perf_counter()
    try:
        await engine.start()
        warm = await warm_up(engine, warm_up_prompt())
    except Exception:
        logger.exception("%s engine failed to start; /api/health stays ready=false", engine.name)
        await engine.close()
        return
    logger.info(
        "%s engine ready in %.1f s (model=%s); warm-up: %d ms, %d prompt + %d completion "
        "tokens\n%s",
        engine.name,
        time.perf_counter() - started,
        engine.model,
        warm.elapsed_ms,
        warm.prompt_tokens,
        warm.completion_tokens,
        warm.text[:WARMUP_PREVIEW_CHARS],
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the one engine of this process at startup and close it at shutdown.

    A night run left unfinished by an earlier process is marked failed at startup; an active
    one is cancelled (and marked failed) at shutdown, before the engine closes.
    """
    log_to_stderr()
    engine: Engine = app.state.engine
    runner: NightRunner | None = app.state.night_runner
    if runner is not None:
        runner.recover()
    # Open in the background so the app answers requests while the model loads.
    opening = asyncio.create_task(open_engine(engine))
    try:
        yield
    finally:
        if runner is not None:
            await runner.close()
        opening.cancel()
        with suppress(asyncio.CancelledError):
            await opening
        await engine.close()


def db_night(settings: Settings) -> DbNight | None:
    """The stored night runs the API serves: real mode with NIGHT_SOURCE=db only."""
    if settings.engine == "fake" or settings.night_source != "db":
        return None
    return DbNight(NightDb(settings.db_path))


def night_store(
    settings: Settings, data_store: DataStore, night: DbNight | None = None
) -> WindowSource:
    """Windows and details of the night the API serves.

    Fake mode serves `data_store`. Real mode with NIGHT_SOURCE=db serves the latest finished
    run in the SQLite store. With NIGHT_SOURCE=stored it serves the stored 11 Oct night in
    `fixtures/`, even when the dataset is downloaded, so every report window opens. Signals
    always come from `data_store` (#28).
    """
    if settings.engine == "fake":
        return data_store
    if night is not None:
        return night
    return DataStore(settings.data_dir, fixtures_first=settings.night_source == "stored")


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    app = FastAPI(title="TeleReason", lifespan=lifespan)
    app.state.settings = settings or load_settings()
    # Building the engine imports no GPU code; vllm is imported only when it starts.
    app.state.engine = engine or create_engine(app.state.settings)
    app.state.data_store = DataStore(
        app.state.settings.data_dir, use_fixtures=app.state.settings.engine == "fake"
    )
    app.state.db_night = db_night(app.state.settings)
    app.state.night_store = night_store(
        app.state.settings, app.state.data_store, app.state.db_night
    )
    # Night runs on request are recorded in the store at DB_PATH (real mode only).
    app.state.night_runner = (
        None
        if app.state.settings.engine == "fake"
        else NightRunner(NightDb(app.state.settings.db_path), app.state.engine)
    )
    # Read once: the installed versions do not change while the process runs.
    app.state.versions = system.read_versions()
    app.state.rate_limiters = {
        "ask": RateLimiter(app.state.settings.rate_limit_ask_per_min),
        "night_runs": RateLimiter(app.state.settings.rate_limit_night_per_min),
    }
    install_error_handlers(app)
    for module in ROUTERS:
        app.include_router(module.router, responses=ERROR_RESPONSES)
    if app.state.settings.frontend_dist:
        mount_frontend(app, app.state.settings.frontend_dist)
    return app


app = create_app()
