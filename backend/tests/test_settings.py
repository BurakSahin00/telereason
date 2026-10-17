import pytest

from telereason.settings import DEFAULT_DB_PATH, DEFAULT_GPU_MEMORY_UTILIZATION, load_settings


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


def test_night_source_accepts_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIGHT_SOURCE", "db")

    assert load_settings().night_source == "db"


def test_db_path_defaults_to_the_example_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DB_PATH", raising=False)

    assert load_settings().db_path == DEFAULT_DB_PATH == "./telereason.sqlite"


def test_db_path_comes_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_PATH", " /var/lib/telereason/night.sqlite ")

    assert load_settings().db_path == "/var/lib/telereason/night.sqlite"


def test_cors_origins_are_trimmed_deduplicated_and_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CORS_ORIGINS",
        " https://demo.vercel.app/ , https://api.example.com,https://demo.vercel.app ",
    )

    assert load_settings().cors_origins == (
        "https://demo.vercel.app",
        "https://api.example.com",
    )


@pytest.mark.parametrize("raw", ["live", "fixtures", "sqlite"])
def test_night_source_rejects_unknown_values(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("NIGHT_SOURCE", raw)

    with pytest.raises(ValueError, match="NIGHT_SOURCE"):
        load_settings()


def test_gate_mode_defaults_to_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GATE_MODE", raising=False)

    assert load_settings().gate_mode == "auto"


def test_gate_mode_comes_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATE_MODE", " Full ")

    assert load_settings().gate_mode == "full"


def test_gate_mode_rejects_unknown_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATE_MODE", "sometimes")

    with pytest.raises(ValueError, match="GATE_MODE"):
        load_settings()
