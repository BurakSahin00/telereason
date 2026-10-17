"""Settings read from environment variables only (see .env.example)."""

import os
from dataclasses import dataclass
from typing import Literal, cast

EngineKind = Literal["fake", "vllm"]
# Where real mode gets its night: "db" is the latest finished run in the SQLite store at
# DB_PATH (#48); "stored" is the 11 Oct night in fixtures/, kept as the fallback.
NightSource = Literal["stored", "db"]

# "auto": a small night reuses the latest stored gate; "full": every night runs the gate.
GateMode = Literal["auto", "full"]

ENGINE_KINDS: tuple[EngineKind, ...] = ("fake", "vllm")
GATE_MODES: tuple[GateMode, ...] = ("auto", "full")
NIGHT_SOURCES: tuple[NightSource, ...] = ("stored", "db")
DEFAULT_MODEL = "openai/gpt-oss-120b"
DEFAULT_GPU_MEMORY_UTILIZATION = 0.85
DEFAULT_DB_PATH = "./telereason.sqlite"


@dataclass(frozen=True)
class Settings:
    engine: EngineKind
    model: str
    data_dir: str = "./data"
    hf_dataset: str = "FactoryBench/FactoryBench"
    gpu_memory_utilization: float = DEFAULT_GPU_MEMORY_UTILIZATION
    night_source: NightSource = "stored"
    # SQLite store of night runs, windows, notes and reports.
    db_path: str = DEFAULT_DB_PATH
    gate_mode: GateMode = "auto"
    # Built frontend served from the same origin; empty means the API only.
    frontend_dist: str = ""
    # Requests per client per minute; 0 turns the limit off.
    rate_limit_ask_per_min: int = 10
    rate_limit_night_per_min: int = 2
    # Behind the Cloudflare tunnel every request comes from 127.0.0.1, so the client is
    # read from CF-Connecting-IP or X-Forwarded-For instead.
    trust_proxy_headers: bool = False
    # Comma-separated browser origins allowed to call the API (for a separate Vercel frontend).
    cors_origins: tuple[str, ...] = ()
    # Fake mode only: a live real-mode backend that answers /api while it is ready
    # (telereason.api.remote), and the key it expects.
    remote_api_url: str = ""
    remote_api_key: str = ""
    # Real mode behind a public tunnel: every /api request must carry this key.
    api_key: str = ""


def load_settings() -> Settings:
    engine = os.environ.get("ENGINE", "fake").strip().lower()
    if engine not in ENGINE_KINDS:
        raise ValueError(f"ENGINE must be one of {ENGINE_KINDS}, got {engine!r}")
    model = os.environ.get("MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    data_dir = os.environ.get("DATA_DIR", "./data").strip() or "./data"
    hf_dataset = os.environ.get("HF_DATASET", "FactoryBench/FactoryBench").strip()
    night_source = os.environ.get("NIGHT_SOURCE", "stored").strip().lower() or "stored"
    if night_source not in NIGHT_SOURCES:
        raise ValueError(f"NIGHT_SOURCE must be one of {NIGHT_SOURCES}, got {night_source!r}")
    return Settings(
        engine=cast(EngineKind, engine),
        model=model,
        data_dir=data_dir,
        hf_dataset=hf_dataset or "FactoryBench/FactoryBench",
        gpu_memory_utilization=_gpu_memory_utilization(),
        night_source=cast(NightSource, night_source),
        db_path=os.environ.get("DB_PATH", "").strip() or DEFAULT_DB_PATH,
        gate_mode=_gate_mode(),
        frontend_dist=os.environ.get("FRONTEND_DIST", "").strip(),
        rate_limit_ask_per_min=_per_min("RATE_LIMIT_ASK_PER_MIN", 10),
        rate_limit_night_per_min=_per_min("RATE_LIMIT_NIGHT_PER_MIN", 2),
        trust_proxy_headers=os.environ.get("TRUST_PROXY_HEADERS", "").strip() == "1",
        cors_origins=_cors_origins(),
        remote_api_url=os.environ.get("REMOTE_API_URL", "").strip().rstrip("/"),
        remote_api_key=os.environ.get("REMOTE_API_KEY", "").strip(),
        api_key=os.environ.get("API_KEY", "").strip(),
    )


def _cors_origins() -> tuple[str, ...]:
    raw = os.environ.get("CORS_ORIGINS", "")
    origins = (origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip())
    return tuple(dict.fromkeys(origins))


def _gate_mode() -> GateMode:
    mode = os.environ.get("GATE_MODE", "").strip().lower() or "auto"
    if mode not in GATE_MODES:
        raise ValueError(f"GATE_MODE must be one of {GATE_MODES}, got {mode!r}")
    return cast(GateMode, mode)


def _per_min(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip() or str(default)
    if not (raw.isascii() and raw.isdigit()):
        raise ValueError(f"{name} must be a whole number >= 0, got {raw!r}")
    return int(raw)


def _gpu_memory_utilization() -> float:
    raw = os.environ.get("GPU_MEMORY_UTILIZATION", "").strip()
    if not raw:
        return DEFAULT_GPU_MEMORY_UTILIZATION
    try:
        value = float(raw)
    except ValueError:
        value = float("nan")
    if not 0 < value <= 1:
        raise ValueError(f"GPU_MEMORY_UTILIZATION must be in (0, 1], got {raw!r}")
    return value
