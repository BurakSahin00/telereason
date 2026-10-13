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
