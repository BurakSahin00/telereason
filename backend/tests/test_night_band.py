from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from telereason.api.main import create_app
from telereason.data.loader import DataStore, WindowNotFoundError
from telereason.night.band import NightSignals, default_channels, normal_band, resample
from telereason.night.facts import WindowFacts
from telereason.night.robust import MAD_SCALE
from telereason.settings import DEFAULT_MODEL, Settings
from tests.test_night_windows import _dataset, _item, _write_jsonl


def _rows(a: list[float], gap: float = 0.0) -> list[str]:
    return [
        f"t={1000 + 100 * n}: sig_a={x}, spo0=1.0, fpo0={1.0 + gap * n}" for n, x in enumerate(a)
    ]


def _band_dataset(tmp_path: Path) -> Path:
    data_dir = _dataset(tmp_path)
    pool = [
        _item(f"au-n{n}", "aursad", f"experiment_{n + 1}_1", rows=_rows([n, n + 1.0, n + 2.0]))
        for n in range(6)
    ]
    pool.append(
        _item(
            "au-target",
            "aursad",
            "experiment_4094_1",
            root_cause="missing_screw",
            rows=_rows([20.0, 21.0, 22.0, 23.0], gap=0.5),
        )
    )
    _write_jsonl(data_dir / "factorybench" / "factorybench_qa" / "level_4.jsonl", pool)
    return data_dir


def _window_id(signals: NightSignals, ms_id: str) -> str:
    night = signals._load()
    return next(w.window_id for w in night.pool.windows if w.ms_id == ms_id)


def test_resample_stretches_and_fills_gaps() -> None:
    assert resample(np.array([0.0, 2.0]), 3).tolist() == [0.0, 1.0, 2.0]
    assert resample(np.array([0.0, np.nan, 4.0]), 3).tolist() == [0.0, 2.0, 4.0]
    assert resample(np.array([np.nan]), 3) is None


def test_normal_band_is_median_plus_minus_two_robust_spreads() -> None:
    references = [np.array([float(n), float(n)]) for n in range(6)]

    low, high = normal_band(references, 2, floor=0.5) or (None, None)

    mad = MAD_SCALE * 1.5
    assert np.allclose(low, 2.5 - 2 * mad)
    assert np.allclose(high, 2.5 + 2 * mad)
    flat = [np.ones(2)] * 5
    low, high = normal_band(flat, 2, floor=0.5) or (None, None)
    assert low.tolist() == [0.0, 0.0]
    assert high.tolist() == [2.0, 2.0]
    assert normal_band(references[:4], 2, floor=0.5) is None


def test_default_channels_take_both_sides_of_a_pair() -> None:
    facts = WindowFacts(
        window_id="W-000001",
        ms_id="x",
        line="AURSAD",
        task="aursad",
        reference="other experiment blocks",
        deviating=[{"signal": s, "z": z} for s, z in (("a", 3.0), ("b", 2.0), ("c", 1.0))],
        gap={"pair": "feedback_pos_0 vs setpoint_pos_0", "z": 4.0},
        missing_signals=0,
        look_first="feedback_pos_0 vs setpoint_pos_0",
    )

    assert default_channels(facts) == ["feedback_pos_0", "setpoint_pos_0", "a", "b"]


def test_signals_of_a_pool_window(tmp_path: Path) -> None:
    signals = NightSignals(_band_dataset(tmp_path))
    window_id = _window_id(signals, "au-target")

    response = signals.get(window_id)

    assert response.window_id == window_id
    assert response.t_s == [0.0, 0.1, 0.2, 0.3]
    names = [channel.name for channel in response.channels]
    assert names[:2] == ["feedback_pos_0", "setpoint_pos_0"]
    assert "sig_a" in names
    for channel in response.channels:
        assert len(channel.values) == len(channel.normal_low) == len(response.t_s)
        assert all(lo <= hi for lo, hi in zip(channel.normal_low, channel.normal_high, strict=True))
    sig_a = next(channel for channel in response.channels if channel.name == "sig_a")
    assert sig_a.values == [20.0, 21.0, 22.0, 23.0]
    assert all(v > hi for v, hi in zip(sig_a.values, sig_a.normal_high, strict=True))


def test_requested_channels_and_unknown_names(tmp_path: Path) -> None:
    signals = NightSignals(_band_dataset(tmp_path))
    window_id = _window_id(signals, "au-target")

    response = signals.get(window_id, ["sig_a", "no_such_channel"])

    assert [channel.name for channel in response.channels] == ["sig_a"]


def test_store_and_api_serve_pool_signals(tmp_path: Path) -> None:
    data_dir = _band_dataset(tmp_path)
    store = DataStore(data_dir)
    window_id = _window_id(store.night_signals, "au-target")

    assert store.get_signals(window_id, "sig_a").channels[0].name == "sig_a"
    with pytest.raises(WindowNotFoundError):
        store.get_signals("W-999999")
    client = TestClient(
        create_app(Settings(engine="vllm", model=DEFAULT_MODEL, data_dir=str(data_dir)))
    )
    assert client.get(f"/api/windows/{window_id}/signals").status_code == 200
    response = client.get("/api/windows/W-999999/signals")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_a_pair_name_is_served_as_measured_minus_planned(tmp_path: Path) -> None:
    signals = NightSignals(_band_dataset(tmp_path))
    window_id = _window_id(signals, "au-target")

    response = signals.get(window_id, ["feedback_pos_0 vs setpoint_pos_0"])

    [channel] = response.channels
    assert channel.name == "feedback_pos_0 vs setpoint_pos_0"
    assert channel.values == [0.0, 0.5, 1.0, 1.5]
    assert channel.normal_low[0] <= 0.0 <= channel.normal_high[0]
