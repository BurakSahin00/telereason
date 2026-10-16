"""Text generation engines: the one place that opens and closes the LLM engine.

`FakeEngine` runs without GPU or network (laptops and CI). `VllmEngine` wraps the vLLM
AsyncLLM engine on the droplet. Everything else receives an `Engine` and never opens one.
"""

import asyncio
import logging
import time
import uuid
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Literal, Protocol, runtime_checkable

from telereason.settings import DEFAULT_GPU_MEMORY_UTILIZATION, Settings

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
        seed: int | None = None,
    ) -> Generation: ...


FAKE_NOTE = (
    "During this window the cell kept its usual cycle rhythm, but the motor current on one "
    "axis drifted away from the line's normal band in the second half of the window while "
    "the commanded motion stayed the same. The abnormal-behaviour score for the window is "
    "above the line's usual level. The data does not support naming a cause, so this note "
    "reports only the verified facts; a technician should compare the next cycles with the "
    "cell's maintenance protocol before the morning shift."
)
FAKE_NOTE_TURN = f"<|channel|>final<|message|>{FAKE_NOTE}<|return|>"
FAKE_AGENT_TURN = (
    "<|channel|>final<|message|>I can answer questions about last night's report, the "
    "scanned windows and the maintenance protocols. I cannot start, stop or change any "
    "equipment.<|return|>"
)
FAKE_DEFAULT = "Fake engine output."

HARMONY_SYSTEM = (
    "<|start|>system<|message|>You are ChatGPT, a large language model trained by OpenAI.\n\n"
    "Reasoning: low\n\n"
    "# Valid channels: analysis, commentary, final. Channel must be included for every "
    "message.<|end|>"
)
WARMUP_QUESTION = "Reply with one word: ready."
WARMUP_MAX_TOKENS = 64


def harmony_prompt(question: str) -> str:
    """One user turn in the harmony format, ending where the assistant starts."""
    return f"{HARMONY_SYSTEM}<|start|>user<|message|>{question}<|end|><|start|>assistant"


@dataclass(frozen=True)
class EngineCall:
    prompt: Prompt
    priority: Priority
    kind: GenerationKind | None
    seed: int | None = None


class FakeEngine:
    """Deterministic engine without GPU or network.

    Tokens are UTF-8 bytes, so token counts are stable and need no tokenizer. Scripted
    replies are returned in order; once the queue is empty the canned text for `kind`
    is returned. `temperature` and `seed` do not change the output. Every call is
    recorded in `calls`.
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
        seed: int | None = None,
    ) -> Generation:
        if not self._ready:
            raise EngineNotReadyError("engine is not ready")
        if max_tokens < 1:
            raise ValueError("max_tokens must be at least 1")
        started = time.perf_counter()
        self.calls.append(EngineCall(prompt=prompt, priority=priority, kind=kind, seed=seed))

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
        return FAKE_NOTE_TURN
    if kind == "agent":
        return FAKE_AGENT_TURN
    return FAKE_DEFAULT


class VllmEngine:
    """vLLM AsyncLLM engine in this process, with priority scheduling.

    vllm is imported only in `start` and `generate`, so this module imports on machines
    without vllm. Text keeps harmony special tokens, like the fake agent turn.
    """

    name: Literal["fake", "vllm"] = "vllm"

    def __init__(
        self,
        model: str,
        gpu_memory_utilization: float = DEFAULT_GPU_MEMORY_UTILIZATION,
    ) -> None:
        self.model = model
        self.gpu_memory_utilization = gpu_memory_utilization
        self._llm: Any = None
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    async def start(self) -> None:
        if self._llm is not None:
            return
        try:
            from vllm import AsyncEngineArgs
            from vllm.v1.engine.async_llm import AsyncLLM
        except ImportError as exc:
            raise RuntimeError(
                "ENGINE=vllm needs the vllm package, which the droplet container provides"
            ) from exc

        started = time.perf_counter()
        args = AsyncEngineArgs(
            model=self.model,
            gpu_memory_utilization=self.gpu_memory_utilization,
            scheduling_policy="priority",
        )
        # Loading the weights takes minutes; a worker thread keeps the event loop free, so
        # /api/health can answer ready=false meanwhile.
        self._llm = await asyncio.to_thread(AsyncLLM.from_engine_args, args)
        self._ready = True
        logger.info(
            "vllm engine started in %.1f s (model=%s, gpu_memory_utilization=%.2f, "
            "scheduling_policy=priority)",
            time.perf_counter() - started,
            self.model,
            self.gpu_memory_utilization,
        )

    async def close(self) -> None:
        self._ready = False
        llm, self._llm = self._llm, None
        if llm is not None:
            llm.shutdown()
            logger.info("vllm engine closed")

    async def generate(
        self,
        prompt: Prompt,
        *,
        priority: Priority,
        max_tokens: int,
        temperature: float = 0.0,
        stop: Sequence[str] | None = None,
        kind: GenerationKind | None = None,
        seed: int | None = None,
    ) -> Generation:
        if not self._ready or self._llm is None:
            raise EngineNotReadyError("engine is not ready")
        if max_tokens < 1:
            raise ValueError("max_tokens must be at least 1")
        from vllm import SamplingParams, TokensPrompt
        from vllm.sampling_params import RequestOutputKind

        params = SamplingParams(
            max_tokens=max_tokens,
            temperature=temperature,
            stop=list(stop) if stop else None,
            seed=seed,
            skip_special_tokens=False,
            output_kind=RequestOutputKind.FINAL_ONLY,
        )
        request = prompt if isinstance(prompt, str) else TokensPrompt(prompt_token_ids=list(prompt))
        request_id = f"{kind or 'generate'}-{uuid.uuid4().hex}"
        started = time.perf_counter()

        llm = self._llm
        final = None
        try:
            async for output in llm.generate(request, params, request_id, priority=int(priority)):
                final = output
        except asyncio.CancelledError:
            # The client left or the request timed out: free the GPU slot now. AsyncLLM
            # aborts on cancellation too; aborting a finished request is a no-op.
            await llm.abort(request_id)
            logger.info("vllm request %s cancelled", request_id)
            raise
        if final is None or not final.outputs:
            raise RuntimeError(f"vllm returned no output for request {request_id}")

        completion = final.outputs[0]
        token_ids = list(completion.token_ids)
        return Generation(
            text=completion.text,
            token_ids=token_ids,
            prompt_tokens=len(final.prompt_token_ids or ()),
            completion_tokens=len(token_ids),
            elapsed_ms=round((time.perf_counter() - started) * 1000),
        )


async def warm_up(engine: Engine, prompt: Prompt | None = None) -> Generation:
    """Send one LIVE request so the first real question does not pay first-call costs.

    Pass a prompt as long as the real ones: the GPU kernels are compiled per prompt size.
    """
    return await engine.generate(
        prompt if prompt is not None else harmony_prompt(WARMUP_QUESTION),
        priority=Priority.LIVE,
        max_tokens=WARMUP_MAX_TOKENS,
    )


def create_engine(settings: Settings) -> Engine:
    """Build the engine named by `ENGINE`. The caller starts and closes it."""
    if settings.engine == "fake":
        return FakeEngine(model=settings.model)
    if settings.engine == "vllm":
        return VllmEngine(
            model=settings.model,
            gpu_memory_utilization=settings.gpu_memory_utilization,
        )
    raise ValueError(f"unknown engine {settings.engine!r}; expected 'fake' or 'vllm'")
