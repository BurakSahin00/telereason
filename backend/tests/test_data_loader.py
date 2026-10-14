import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from telereason.api.main import create_app
from telereason.data import download
from telereason.data.loader import DataNotAvailableError, DataStore, WindowNotFoundError
from telereason.data.paths import factorywave_dir
from telereason.settings import DEFAULT_MODEL, EngineKind, Settings


def write_factorywave(folder: Path) -> None:
    """Write a tiny FactoryBench-shaped episode table and one wide signal file."""
    folder.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table({"id": ["episode-1"], "episode_metadata": ["{'task': 'pick_and_place'}"]}),
        folder / "episodes.parquet",
    )
    pq.write_table(
        pa.table(
            {
                "episode_id": ["episode-1", "episode-1"],
                "timestamp_ms": [0, 100],
                "signal_a": [1.0, 2.0],
            }
        ),
        folder / "ur_signals.parquet",
    )


def test_store_reads_fixture_shaped_windows(tmp_path) -> None:
    (tmp_path / "windows.json").write_text(
        json.dumps(
            {
                "page": 1,
                "page_size": 1,
                "total": 1,
                "items": [
                    {
                        "window_id": "W-1",
                        "line": "AURSAD",
                        "task": "aursad",
                        "score": 0.9,
                        "flagged": True,
                        "look_first": "signal_a",
                        "verified": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "signals.json").write_text(
        json.dumps(
            {
                "W-1": {
                    "t_s": [0.0, 0.1],
                    "channels": [
                        {
                            "name": "signal_a",
                            "values": [1.0, 2.0],
                            "normal_low": [0.0, 0.0],
                            "normal_high": [3.0, 3.0],
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    store = DataStore(tmp_path / "data", use_fixtures=True, fixture_root=tmp_path)
    result = store.list_windows(flagged=True)
    assert result.total == 1
    assert result.items[0].window_id == "W-1"
    assert store.get_signals("W-1", "signal_a").channels[0].name == "signal_a"


def test_store_reports_unknown_window(tmp_path) -> None:
    (tmp_path / "windows.json").write_text(json.dumps({"items": []}), encoding="utf-8")
    store = DataStore(tmp_path / "data", use_fixtures=True, fixture_root=tmp_path)

    with pytest.raises(WindowNotFoundError):
        store.get_window("missing")


def test_store_opens_detail_missing_from_summaries(tmp_path) -> None:
    # windows.json holds one page only; a report window may be in window_detail.json alone.
    (tmp_path / "windows.json").write_text(json.dumps({"items": []}), encoding="utf-8")
    detail = {
        "window_id": "W-2",
        "line": "FactoryWave",
        "task": "pick_and_place",
        "verdict": "closed",
        "score": None,
        "flagged": None,
        "look_first": "signal_a",
        "facts": [],
        "top_features": [],
        "note": "",
        "verified": True,
    }
    (tmp_path / "window_detail.json").write_text(json.dumps({"W-2": detail}), encoding="utf-8")
    store = DataStore(tmp_path / "data", use_fixtures=True, fixture_root=tmp_path)

    assert store.get_window("W-2").model_dump(mode="json") == detail


def test_store_reads_factorybench_signal_parquet_without_band(tmp_path) -> None:
    write_factorywave(tmp_path / "factorybench" / "factorywave")
    store = DataStore(tmp_path)

    payload = store._read_parquet_signals("episode-1")
    assert payload == {"t_s": [0.0, 0.1], "channels": [{"name": "signal_a", "values": [1.0, 2.0]}]}
    with pytest.raises(DataNotAvailableError, match="normal band not computed yet"):
        store.get_signals("episode-1", "signal_a")


def test_downloaded_layout_is_read_instead_of_fixtures(monkeypatch, tmp_path) -> None:
    def fake_snapshot_download(**kwargs: object) -> str:
        local_dir = Path(str(kwargs["local_dir"]))
        write_factorywave(local_dir / "factorywave")
        return str(local_dir)

    monkeypatch.setattr(download, "snapshot_download", fake_snapshot_download)
    download.download_dataset(tmp_path)

    store = DataStore(tmp_path, use_fixtures=True)
    assert store.source == factorywave_dir(tmp_path)
    result = store.list_windows()
    assert result.total == 1
    assert [item.window_id for item in result.items] == ["episode-1"]


def test_store_without_data_ignores_fixtures_outside_fake_mode(tmp_path) -> None:
    with pytest.raises(DataNotAvailableError):
        DataStore(tmp_path).list_windows()
    assert DataStore(tmp_path, use_fixtures=True).list_windows().total > 0


def client_for(engine: EngineKind, data_dir: Path) -> TestClient:
    settings = Settings(engine=engine, model=DEFAULT_MODEL, data_dir=str(data_dir))
    return TestClient(create_app(settings))


def test_api_serves_fixtures_only_in_fake_mode(tmp_path) -> None:
    assert client_for("fake", tmp_path).get("/api/windows").status_code == 200

    response = client_for("vllm", tmp_path).get("/api/windows")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "engine_not_ready"


def test_api_signals_on_real_data_wait_for_normal_band(tmp_path) -> None:
    write_factorywave(factorywave_dir(tmp_path))

    response = client_for("vllm", tmp_path).get("/api/windows/episode-1/signals")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "engine_not_ready"
    assert "normal band not computed yet" in response.json()["error"]["message"]
