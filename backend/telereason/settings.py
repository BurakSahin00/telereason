"""Settings read from environment variables only (see .env.example)."""

import os
from dataclasses import dataclass
from typing import Literal, cast

EngineKind = Literal["fake", "vllm"]

ENGINE_KINDS: tuple[EngineKind, ...] = ("fake", "vllm")
DEFAULT_MODEL = "openai/gpt-oss-120b"


@dataclass(frozen=True)
class Settings:
    engine: EngineKind
    model: str
    data_dir: str = "./data"
    hf_dataset: str = "FactoryBench/FactoryBench"


def load_settings() -> Settings:
    engine = os.environ.get("ENGINE", "fake").strip().lower()
    if engine not in ENGINE_KINDS:
        raise ValueError(f"ENGINE must be one of {ENGINE_KINDS}, got {engine!r}")
    model = os.environ.get("MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    data_dir = os.environ.get("DATA_DIR", "./data").strip() or "./data"
    hf_dataset = os.environ.get("HF_DATASET", "FactoryBench/FactoryBench").strip()
    return Settings(
        engine=cast(EngineKind, engine),
        model=model,
        data_dir=data_dir,
        hf_dataset=hf_dataset or "FactoryBench/FactoryBench",
    )
