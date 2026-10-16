import asyncio
import importlib
import sys
from collections.abc import AsyncIterator
from types import ModuleType, SimpleNamespace
from typing import cast

import pytest

import telereason.ai
from telereason.ai.engine import (
    FAKE_AGENT_TURN,
    FAKE_DEFAULT,
    FAKE_NOTE,
    FAKE_NOTE_TURN,
    Engine,
    EngineCall,
    EngineNotReadyError,
    FakeEngine,
    Generation,
    Priority,
    VllmEngine,
    create_engine,
)
from telereason.settings import EngineKind, Settings


def started_fake(**kwargs: object) -> FakeEngine:
    engine = FakeEngine(**kwargs)  # type: ignore[arg-type]
    asyncio.run(engine.start())
    return engine


def generate(engine: Engine, prompt: str | list[int], **kwargs: object) -> Generation:
    kwargs.setdefault("priority", Priority.NIGHT)
    kwargs.setdefault("max_tokens", 1024)
    return asyncio.run(engine.generate(prompt, **kwargs))  # type: ignore[arg-type]


def test_live_runs_before_night() -> None:
    assert Priority.LIVE == 0
    assert Priority.NIGHT == 1
    assert Priority.LIVE < Priority.NIGHT


def test_both_engines_satisfy_the_interface() -> None:
    fake = FakeEngine()
    vllm = VllmEngine(model="openai/gpt-oss-120b")

    assert isinstance(fake, Engine)
    assert isinstance(vllm, Engine)
    assert fake.name == "fake"
    assert vllm.name == "vllm"
    assert vllm.model == "openai/gpt-oss-120b"


def test_fake_output_is_deterministic() -> None:
    first = generate(started_fake(), "note prompt", kind="note")
    second = generate(started_fake(), "note prompt", kind="note")

    assert first.text == second.text == FAKE_NOTE_TURN
    assert first.token_ids == second.token_ids
    assert first.prompt_tokens == second.prompt_tokens == len(b"note prompt")
    assert first.completion_tokens == len(first.token_ids) > 0
    assert first.elapsed_ms >= 0


def test_fake_returns_canned_text_per_kind() -> None:
    engine = started_fake()

    note = generate(engine, "p", kind="note")
    turn = generate(engine, "p", kind="agent")
    other = generate(engine, "p")

    assert note.text == FAKE_NOTE_TURN
    assert FAKE_NOTE in note.text
    assert "\n" not in note.text
    assert turn.text == FAKE_AGENT_TURN
    assert other.text == FAKE_DEFAULT


def test_fake_accepts_token_id_prompts() -> None:
    result = generate(started_fake(), [200006, 17360, 200008], kind="agent")

    assert result.prompt_tokens == 3
    assert result.text == FAKE_AGENT_TURN


def test_scripted_replies_come_in_order_then_default() -> None:
    engine = started_fake(scripted=["first", "second"])
    engine.script("third")

    texts = [generate(engine, "p", kind="note").text for _ in range(4)]

    assert texts == ["first", "second", "third", FAKE_NOTE_TURN]


def test_fake_applies_stop_and_max_tokens() -> None:
    engine = started_fake(scripted=["answer<|call|>rest", "abcdef"])

    stopped = generate(engine, "p", stop=["<|call|>"])
    capped = generate(engine, "p", max_tokens=3)

    assert stopped.text == "answer"
    assert capped.text == "abc"
    assert capped.completion_tokens == 3


def test_fake_records_prompt_priority_and_kind() -> None:
    engine = started_fake()

    generate(engine, "night", priority=Priority.NIGHT, kind="note", seed=11)
    generate(engine, [1, 2, 3], priority=Priority.LIVE, kind="agent")

    assert engine.calls == [
        EngineCall(prompt="night", priority=Priority.NIGHT, kind="note", seed=11),
        EngineCall(prompt=[1, 2, 3], priority=Priority.LIVE, kind="agent"),
    ]


def test_fake_output_does_not_depend_on_the_seed() -> None:
    first = generate(started_fake(), "p", kind="note", seed=1, temperature=1.0)
    second = generate(started_fake(), "p", kind="note", seed=2, temperature=1.0)

    assert first.text == second.text


def test_fake_ready_follows_start_and_close() -> None:
    engine = FakeEngine()
    assert engine.ready is False
    with pytest.raises(EngineNotReadyError):
        generate(engine, "p")

    asyncio.run(engine.start())
    assert engine.ready is True
    generate(engine, "p")

    asyncio.run(engine.close())
    assert engine.ready is False
    with pytest.raises(EngineNotReadyError):
        generate(engine, "p")


@pytest.mark.parametrize(
    ("kind", "engine_type"),
    [("fake", FakeEngine), ("vllm", VllmEngine)],
)
def test_create_engine_follows_engine_setting(kind: EngineKind, engine_type: type) -> None:
    engine = create_engine(Settings(engine=kind, model="openai/gpt-oss-120b"))

    assert isinstance(engine, engine_type)
    assert engine.name == kind
    assert engine.model == "openai/gpt-oss-120b"
    assert engine.ready is False


def test_create_engine_rejects_unknown_engine() -> None:
    settings = Settings(engine=cast(EngineKind, "tgi"), model="m")

    with pytest.raises(ValueError, match="unknown engine 'tgi'"):
        create_engine(settings)


def test_engine_module_imports_without_vllm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "vllm", None)  # makes `import vllm` raise ImportError
    monkeypatch.delitem(sys.modules, "telereason.ai.engine")
    monkeypatch.delattr(telereason.ai, "engine")

    module = importlib.import_module("telereason.ai.engine")
    engine = module.VllmEngine(model="openai/gpt-oss-120b")

    assert engine.ready is False
    with pytest.raises(RuntimeError, match="needs the vllm package"):
        asyncio.run(engine.start())
    assert engine.ready is False
    with pytest.raises(module.EngineNotReadyError):
        asyncio.run(engine.generate("p", priority=module.Priority.LIVE, max_tokens=8))


class StubLLM:
    """Stands in for vllm's AsyncLLM: records requests and returns one final output."""

    instances: list["StubLLM"] = []

    def __init__(self, args: SimpleNamespace) -> None:
        self.args = args
        self.requests: list[tuple[object, SimpleNamespace, str, int]] = []
        self.shut_down = False
        StubLLM.instances.append(self)

    @classmethod
    def from_engine_args(cls, args: SimpleNamespace) -> "StubLLM":
        return cls(args)

    async def generate(
        self, prompt: object, params: SimpleNamespace, request_id: str, *, priority: int
    ) -> AsyncIterator[SimpleNamespace]:
        self.requests.append((prompt, params, request_id, priority))
        completion = SimpleNamespace(text="<|channel|>final<|message|>ok", token_ids=(7, 8, 9))
        yield SimpleNamespace(prompt_token_ids=[1, 2, 3, 4], outputs=[completion])

    def shutdown(self) -> None:
        self.shut_down = True


@pytest.fixture
def stub_vllm(monkeypatch: pytest.MonkeyPatch) -> type[StubLLM]:
    StubLLM.instances = []
    vllm = ModuleType("vllm")
    vllm.AsyncEngineArgs = SimpleNamespace  # type: ignore[attr-defined]
    vllm.SamplingParams = SimpleNamespace  # type: ignore[attr-defined]
    vllm.TokensPrompt = dict  # type: ignore[attr-defined]
    sampling_params = ModuleType("vllm.sampling_params")
    sampling_params.RequestOutputKind = SimpleNamespace(FINAL_ONLY="final_only")  # type: ignore[attr-defined]
    async_llm = ModuleType("vllm.v1.engine.async_llm")
    async_llm.AsyncLLM = StubLLM  # type: ignore[attr-defined]
    for name, module in {
        "vllm": vllm,
        "vllm.sampling_params": sampling_params,
        "vllm.v1": ModuleType("vllm.v1"),
        "vllm.v1.engine": ModuleType("vllm.v1.engine"),
        "vllm.v1.engine.async_llm": async_llm,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    return StubLLM


def test_vllm_start_opens_one_priority_engine(stub_vllm: type[StubLLM]) -> None:
    engine = VllmEngine(model="openai/gpt-oss-120b", gpu_memory_utilization=0.85)

    asyncio.run(engine.start())
    asyncio.run(engine.start())

    assert engine.ready is True
    assert len(stub_vllm.instances) == 1
    args = stub_vllm.instances[0].args
    assert args.model == "openai/gpt-oss-120b"
    assert args.gpu_memory_utilization == 0.85
    assert args.scheduling_policy == "priority"


def test_vllm_generate_maps_request_and_output(stub_vllm: type[StubLLM]) -> None:
    engine = VllmEngine(model="m")
    asyncio.run(engine.start())

    text = generate(engine, "hello", priority=Priority.NIGHT, max_tokens=64, stop=["<|call|>"])
    tokens = generate(
        engine, [200006, 17360], priority=Priority.LIVE, max_tokens=8, kind="agent", seed=7
    )

    assert text == Generation(
        text="<|channel|>final<|message|>ok",
        token_ids=[7, 8, 9],
        prompt_tokens=4,
        completion_tokens=3,
        elapsed_ms=text.elapsed_ms,
    )
    assert tokens.completion_tokens == 3
    (p1, params1, id1, prio1), (p2, params2, id2, prio2) = stub_vllm.instances[0].requests
    assert p1 == "hello"
    assert p2 == {"prompt_token_ids": [200006, 17360]}
    assert (prio1, prio2) == (Priority.NIGHT, Priority.LIVE)
    assert params1.max_tokens == 64
    assert params1.temperature == 0.0
    assert params1.stop == ["<|call|>"]
    assert params1.skip_special_tokens is False
    assert params1.output_kind == "final_only"
    assert params1.seed is None
    assert params2.seed == 7
    assert params2.stop is None
    assert id1 != id2
    assert id2.startswith("agent-")


def test_vllm_close_shuts_down_the_engine(stub_vllm: type[StubLLM]) -> None:
    engine = VllmEngine(model="m")
    asyncio.run(engine.start())

    asyncio.run(engine.close())

    assert engine.ready is False
    assert stub_vllm.instances[0].shut_down is True
    with pytest.raises(EngineNotReadyError):
        generate(engine, "p")


def test_create_engine_passes_gpu_memory_utilization() -> None:
    settings = Settings(engine="vllm", model="m", gpu_memory_utilization=0.5)

    engine = create_engine(settings)

    assert isinstance(engine, VllmEngine)
    assert engine.gpu_memory_utilization == 0.5
