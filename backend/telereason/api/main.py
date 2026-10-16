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
from telereason.data.loader import DataStore
from telereason.settings import Settings, load_settings

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
    """Open the one engine of this process at startup and close it at shutdown."""
    log_to_stderr()
    engine: Engine = app.state.engine
    # Open in the background so the app answers requests while the model loads.
    opening = asyncio.create_task(open_engine(engine))
    try:
        yield
    finally:
        opening.cancel()
        with suppress(asyncio.CancelledError):
            await opening
        await engine.close()


def night_store(settings: Settings, data_store: DataStore) -> DataStore:
    """Windows and details of the night the API serves.

    Fake mode serves `data_store`. Real mode with NIGHT_SOURCE=stored serves the stored
    11 Oct night in `fixtures/`, even when the dataset is downloaded, so every report window
    opens; signals still come from `data_store` (#28). The real night replaces this (#26, #27).
    """
    if settings.engine == "fake":
        return data_store
    return DataStore(settings.data_dir, fixtures_first=settings.night_source == "stored")


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    app = FastAPI(title="TeleReason", lifespan=lifespan)
    app.state.settings = settings or load_settings()
    # Building the engine imports no GPU code; vllm is imported only when it starts.
    app.state.engine = engine or create_engine(app.state.settings)
    app.state.data_store = DataStore(
        app.state.settings.data_dir, use_fixtures=app.state.settings.engine == "fake"
    )
    app.state.night_store = night_store(app.state.settings, app.state.data_store)
    # Read once: the installed versions do not change while the process runs.
    app.state.versions = system.read_versions()
    install_error_handlers(app)
    for module in ROUTERS:
        app.include_router(module.router, responses=ERROR_RESPONSES)
    return app


app = create_app()
