import asyncio
import importlib
import sys
from typing import cast

import pytest

import telereason.ai
from telereason.ai.engine import (
    FAKE_AGENT_TURN,
    FAKE_DEFAULT,
    FAKE_NOTE,
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
    kwargs.setdefault("max_tokens", 512)
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

    assert first.text == second.text == FAKE_NOTE
    assert first.token_ids == second.token_ids
    assert first.prompt_tokens == second.prompt_tokens == len(b"note prompt")
    assert first.completion_tokens == len(first.token_ids) > 0
    assert first.elapsed_ms >= 0


def test_fake_returns_canned_text_per_kind() -> None:
    engine = started_fake()

    note = generate(engine, "p", kind="note")
    turn = generate(engine, "p", kind="agent")
    other = generate(engine, "p")

    assert note.text == FAKE_NOTE
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

    assert texts == ["first", "second", "third", FAKE_NOTE]


def test_fake_applies_stop_and_max_tokens() -> None:
    engine = started_fake(scripted=["answer<|call|>rest", "abcdef"])

    stopped = generate(engine, "p", stop=["<|call|>"])
    capped = generate(engine, "p", max_tokens=3)

    assert stopped.text == "answer"
    assert capped.text == "abc"
    assert capped.completion_tokens == 3


def test_fake_records_prompt_priority_and_kind() -> None:
    engine = started_fake()

    generate(engine, "night", priority=Priority.NIGHT, kind="note")
    generate(engine, [1, 2, 3], priority=Priority.LIVE, kind="agent")

    assert engine.calls == [
        EngineCall(prompt="night", priority=Priority.NIGHT, kind="note"),
        EngineCall(prompt=[1, 2, 3], priority=Priority.LIVE, kind="agent"),
    ]


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
    with pytest.raises(NotImplementedError, match="#14"):
        asyncio.run(engine.generate("p", priority=module.Priority.LIVE, max_tokens=8))
