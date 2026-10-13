"""Settings read from environment variables only (see .env.example)."""

import os
from dataclasses import dataclass
from typing import Literal, cast

EngineKind = Literal["fake", "vllm"]

ENGINE_KINDS: tuple[EngineKind, ...] = ("fake", "vllm")
DEFAULT_MODEL = "openai/gpt-oss-120b"
DEFAULT_GPU_MEMORY_UTILIZATION = 0.85


@dataclass(frozen=True)
class Settings:
    engine: EngineKind
    model: str
    gpu_memory_utilization: float = DEFAULT_GPU_MEMORY_UTILIZATION


def load_settings() -> Settings:
    engine = os.environ.get("ENGINE", "fake").strip().lower()
    if engine not in ENGINE_KINDS:
        raise ValueError(f"ENGINE must be one of {ENGINE_KINDS}, got {engine!r}")
    model = os.environ.get("MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    return Settings(
        engine=cast(EngineKind, engine),
        model=model,
        gpu_memory_utilization=_gpu_memory_utilization(),
    )


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
