import pytest

from telereason.settings import DEFAULT_GPU_MEMORY_UTILIZATION, load_settings


def test_gpu_memory_utilization_defaults_to_085(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GPU_MEMORY_UTILIZATION", raising=False)

    assert load_settings().gpu_memory_utilization == DEFAULT_GPU_MEMORY_UTILIZATION == 0.85


def test_gpu_memory_utilization_comes_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GPU_MEMORY_UTILIZATION", "0.6")

    assert load_settings().gpu_memory_utilization == 0.6


@pytest.mark.parametrize("raw", ["0", "1.2", "-0.5", "half", "nan"])
def test_gpu_memory_utilization_rejects_bad_values(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("GPU_MEMORY_UTILIZATION", raw)

    with pytest.raises(ValueError, match="GPU_MEMORY_UTILIZATION"):
        load_settings()


def test_night_source_defaults_to_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NIGHT_SOURCE", raising=False)

    assert load_settings().night_source == "stored"


def test_night_source_comes_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIGHT_SOURCE", " Stored ")

    assert load_settings().night_source == "stored"


@pytest.mark.parametrize("raw", ["-1", "1.5", "ten"])
def test_rate_limits_reject_bad_values(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("RATE_LIMIT_ASK_PER_MIN", raw)

    with pytest.raises(ValueError, match="RATE_LIMIT_ASK_PER_MIN"):
        load_settings()


@pytest.mark.parametrize("raw", ["live", "fixtures", "db"])
def test_night_source_rejects_unknown_values(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("NIGHT_SOURCE", raw)

    with pytest.raises(ValueError, match="NIGHT_SOURCE"):
        load_settings()
