import json

import pyarrow as pa
import pyarrow.parquet as pq

from telereason.data.loader import DataStore, WindowNotFoundError


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

    store = DataStore(tmp_path)
    result = store.list_windows(flagged=True)
    assert result.total == 1
    assert result.items[0].window_id == "W-1"
    assert store.get_signals("W-1", "signal_a").channels[0].name == "signal_a"


def test_store_reports_unknown_window(tmp_path) -> None:
    (tmp_path / "windows.json").write_text(json.dumps({"items": []}), encoding="utf-8")
    store = DataStore(tmp_path)

    try:
        store.get_window("missing")
    except WindowNotFoundError:
        pass
    else:
        raise AssertionError("missing window should raise WindowNotFoundError")


def test_store_reads_factorybench_style_signal_parquet(tmp_path) -> None:
    (tmp_path / "episodes.parquet").parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table({"id": ["episode-1"], "episode_metadata": ["{'task': 'pick_and_place'}"]}),
        tmp_path / "episodes.parquet",
    )
    signal_dir = tmp_path / "factorywave"
    signal_dir.mkdir()
    pq.write_table(
        pa.table(
            {
                "episode_id": ["episode-1", "episode-1"],
                "timestamp_ms": [0, 100],
                "signal_a": [1.0, 2.0],
            }
        ),
        signal_dir / "ur_signals.parquet",
    )

    signals = DataStore(tmp_path).get_signals("episode-1", "signal_a")
    assert signals.t_s == [0.0, 0.1]
    assert signals.channels[0].values == [1.0, 2.0]
