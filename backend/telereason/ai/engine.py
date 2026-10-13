"""Text generation engines: the one place that opens and closes the LLM engine.

`FakeEngine` runs without GPU or network (laptops and CI). `VllmEngine` wraps the vLLM
AsyncLLM engine on the droplet. Everything else receives an `Engine` and never opens one.
"""

import logging
import time
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import IntEnum
from typing import Literal, Protocol, runtime_checkable

from telereason.settings import Settings

logger = logging.getLogger(__name__)

Prompt = str | Sequence[int]
GenerationKind = Literal["note", "agent"]


class Priority(IntEnum):
    """Scheduling priority. vLLM runs the smaller number first."""

    LIVE = 0
    NIGHT = 1


@dataclass(frozen=True)
class Generation:
    text: str
    token_ids: list[int]
    prompt_tokens: int
    completion_tokens: int
    elapsed_ms: int


class EngineNotReadyError(RuntimeError):
    """Raised when `generate` is called before `start` or after `close`."""


@runtime_checkable
class Engine(Protocol):
    name: Literal["fake", "vllm"]
    model: str

    @property
    def ready(self) -> bool: ...

    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def generate(
        self,
        prompt: Prompt,
        *,
        priority: Priority,
        max_tokens: int,
        temperature: float = 0.0,
        stop: Sequence[str] | None = None,
        kind: GenerationKind | None = None,
    ) -> Generation: ...


FAKE_NOTE = (
    "During this window the cell kept its usual cycle rhythm, but the motor current on one "
    "axis drifted away from the line's normal band in the second half of the window while "
    "the commanded motion stayed the same. The abnormal-behaviour score for the window is "
    "above the line's usual level. The data does not support naming a cause, so this note "
    "reports only the verified facts; a technician should compare the next cycles with the "
    "cell's maintenance protocol before the morning shift."
)
FAKE_AGENT_TURN = (
    "<|channel|>final<|message|>I can answer questions about last night's report, the "
    "scanned windows and the maintenance protocols. I cannot start, stop or change any "
    "equipment.<|return|>"
)
FAKE_DEFAULT = "Fake engine output."


@dataclass(frozen=True)
class EngineCall:
    prompt: Prompt
    priority: Priority
    kind: GenerationKind | None


class FakeEngine:
    """Deterministic engine without GPU or network.

    Tokens are UTF-8 bytes, so token counts are stable and need no tokenizer. Scripted
    replies are returned in order; once the queue is empty the canned text for `kind`
    is returned. Every call is recorded in `calls`.
    """

    name: Literal["fake", "vllm"] = "fake"

    def __init__(self, model: str = "fake", scripted: Iterable[str] = ()) -> None:
        self.model = model
        self.calls: list[EngineCall] = []
        self._scripted: deque[str] = deque(scripted)
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    def script(self, *texts: str) -> None:
        """Queue replies for the next `generate` calls."""
        self._scripted.extend(texts)

    async def start(self) -> None:
        self._ready = True
        logger.info("fake engine started (model=%s)", self.model)

    async def close(self) -> None:
        self._ready = False
        logger.info("fake engine closed")

    async def generate(
        self,
        prompt: Prompt,
        *,
        priority: Priority,
        max_tokens: int,
        temperature: float = 0.0,
        stop: Sequence[str] | None = None,
        kind: GenerationKind | None = None,
    ) -> Generation:
        if not self._ready:
            raise EngineNotReadyError("engine is not ready")
        if max_tokens < 1:
            raise ValueError("max_tokens must be at least 1")
        started = time.perf_counter()
        self.calls.append(EngineCall(prompt=prompt, priority=priority, kind=kind))

        text = self._scripted.popleft() if self._scripted else _canned(kind)
        for marker in stop or ():
            index = text.find(marker)
            if index != -1:
                text = text[:index]
        token_ids = list(text.encode("utf-8"))[:max_tokens]
        text = bytes(token_ids).decode("utf-8", errors="ignore")
        prompt_tokens = len(prompt.encode("utf-8")) if isinstance(prompt, str) else len(prompt)

        return Generation(
            text=text,
            token_ids=token_ids,
            prompt_tokens=prompt_tokens,
            completion_tokens=len(token_ids),
            elapsed_ms=round((time.perf_counter() - started) * 1000),
        )


def _canned(kind: GenerationKind | None) -> str:
    if kind == "note":
        return FAKE_NOTE
    if kind == "agent":
        return FAKE_AGENT_TURN
    return FAKE_DEFAULT


class VllmEngine:
    """vLLM AsyncLLM engine in this process. Skeleton; the engine itself lands in #14.

    vllm is imported only in `start`, so this module imports on machines without vllm.
    """

    name: Literal["fake", "vllm"] = "vllm"

    def __init__(self, model: str) -> None:
        self.model = model
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    async def start(self) -> None:
        try:
            import vllm  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "ENGINE=vllm needs the vllm package, which the droplet container provides"
            ) from exc
        raise NotImplementedError("VllmEngine.start is implemented in #14")

    async def close(self) -> None:
        self._ready = False

    async def generate(
        self,
        prompt: Prompt,
        *,
        priority: Priority,
        max_tokens: int,
        temperature: float = 0.0,
        stop: Sequence[str] | None = None,
        kind: GenerationKind | None = None,
    ) -> Generation:
        raise NotImplementedError("VllmEngine.generate is implemented in #14")


def create_engine(settings: Settings) -> Engine:
    """Build the engine named by `ENGINE`. The caller starts and closes it."""
    if settings.engine == "fake":
        return FakeEngine(model=settings.model)
    if settings.engine == "vllm":
        return VllmEngine(model=settings.model)
    raise ValueError(f"unknown engine {settings.engine!r}; expected 'fake' or 'vllm'")
