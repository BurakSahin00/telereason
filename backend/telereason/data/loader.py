"""Read TeleReason windows from downloaded data or, in fake mode, API-shaped fixtures."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pyarrow.dataset as ds
import pyarrow.parquet as pq

from telereason.api.schemas import (
    Line,
    SignalsResponse,
    Verdict,
    WindowDetail,
    WindowListResponse,
    WindowSummary,
)
from telereason.data.paths import factorywave_dir


class DataNotAvailableError(RuntimeError):
    """Raised when no downloaded or fixture-backed data source exists."""


class WindowNotFoundError(KeyError):
    """Raised when a requested window does not exist."""


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = ast.literal_eval(value)
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _line(row: dict[str, Any]) -> Line:
    value = row.get("line") or row.get("dataset") or "FactoryWave"
    return "AURSAD" if str(value).lower() == "aursad" else "FactoryWave"


def _task(row: dict[str, Any]) -> str:
    metadata = _mapping(row.get("episode_metadata"))
    return str(row.get("task") or metadata.get("task") or "unknown")


def _episode_summary(row: dict[str, Any]) -> dict[str, Any]:
    window_id = str(row.get("window_id") or row.get("id") or row.get("episode_id") or "")
    if not window_id:
        raise ValueError("episode row has no id/window_id/episode_id")
    score = row.get("score")
    return {
        "window_id": window_id,
        "line": _line(row),
        "task": _task(row),
        "score": float(score) if score is not None else None,
        "flagged": row.get("flagged"),
        "look_first": str(row.get("look_first") or ""),
        "verified": bool(row.get("verified", True)),
    }


def _fixture_root() -> Path:
    return Path(__file__).resolve().parents[3] / "fixtures"


class DataStore:
    """Small, read-only data access layer for the window API.

    The preferred source is the downloaded dataset under ``DATA_DIR`` (see
    ``telereason.data.paths``).  With ``use_fixtures`` (fake mode only) the
    fixture directory is used when that dataset has not been downloaded, which
    keeps tests and the laptop demo usable without network access.  Otherwise a
    missing dataset raises ``DataNotAvailableError``.
    """

    def __init__(
        self,
        data_dir: str | Path,
        *,
        use_fixtures: bool = False,
        fixture_root: str | Path | None = None,
    ) -> None:
        self.data_root = factorywave_dir(data_dir)
        self.use_fixtures = use_fixtures
        self.fixture_root = Path(fixture_root).resolve() if fixture_root else _fixture_root()
        self._summaries: dict[str, dict[str, Any]] | None = None
        self._declared_total: int | None = None
        self._details: dict[str, dict[str, Any]] | None = None
        self._signals: dict[str, dict[str, Any]] | None = None

    @property
    def source(self) -> Path:
        if (self.data_root / "episodes.parquet").exists():
            return self.data_root
        if self.use_fixtures and (self.fixture_root / "windows.json").exists():
            return self.fixture_root
        raise DataNotAvailableError(
            f"No windows data found in {self.data_root}; run telereason.data.download first."
        )

    def _load_summaries(self) -> dict[str, dict[str, Any]]:
        if self._summaries is not None:
            return self._summaries
        source = self.source
        windows_path = source / "windows.json"
        if windows_path.exists():
            payload = _read_json(windows_path)
            items = payload.get("items", payload) if isinstance(payload, dict) else payload
            if isinstance(payload, dict) and isinstance(payload.get("total"), int):
                self._declared_total = payload["total"]
            rows = [_episode_summary(item) for item in items]
        else:
            episode_rows = pq.read_table(source / "episodes.parquet").to_pylist()
            rows = [_episode_summary(row) for row in episode_rows]
        self._summaries = {row["window_id"]: row for row in rows}
        return self._summaries

    def _load_details(self) -> dict[str, dict[str, Any]]:
        if self._details is None:
            path = self.source / "window_detail.json"
            self._details = _read_json(path) if path.exists() else {}
        return self._details

    def _load_signals(self) -> dict[str, dict[str, Any]]:
        if self._signals is None:
            path = self.source / "signals.json"
            self._signals = _read_json(path) if path.exists() else {}
        return self._signals

    def list_windows(
        self,
        *,
        line: Line | None = None,
        flagged: bool | None = None,
        sort: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> WindowListResponse:
        rows = list(self._load_summaries().values())
        if line is not None:
            rows = [row for row in rows if row["line"] == line]
        if flagged is not None:
            rows = [row for row in rows if row["flagged"] == flagged]
        if sort == "score":
            rows.sort(key=lambda row: row["score"] is not None and row["score"], reverse=True)
        elif sort == "deviation":
            rows.sort(key=lambda row: row.get("max_abs_z", 0) or 0, reverse=True)
        start = (page - 1) * page_size
        total = self._declared_total if line is None and flagged is None else len(rows)
        return WindowListResponse(
            page=page,
            page_size=page_size,
            total=total if total is not None else len(rows),
            items=[WindowSummary.model_validate(row) for row in rows[start : start + page_size]],
        )

    def get_window(self, window_id: str) -> WindowDetail:
        # Details first: in fixture mode windows.json is a single page, so a report
        # window may exist only in window_detail.json.
        detail = self._load_details().get(window_id)
        if detail is not None:
            return WindowDetail.model_validate(detail)
        row = self._load_summaries().get(window_id)
        if row is None:
            raise WindowNotFoundError(window_id)
        verdict: Verdict = "screening" if row["line"] == "AURSAD" else "closed"
        return WindowDetail(
            **row,
            verdict=verdict,
            facts=[],
            top_features=[],
            note="",
        )

    def get_signals(self, window_id: str, channels: str | None = None) -> SignalsResponse:
        if window_id not in self._load_summaries():
            raise WindowNotFoundError(window_id)
        payload = self._load_signals().get(window_id)
        if payload is None:
            payload = self._read_parquet_signals(window_id)
        if payload is None:
            raise DataNotAvailableError(f"No signal data found for window {window_id}.")
        wanted = [name.strip() for name in channels.split(",")] if channels else None
        channel_rows = payload.get("channels", payload)
        if isinstance(channel_rows, dict):
            channel_rows = [dict(value, name=name) for name, value in channel_rows.items()]
        if any("normal_low" not in row or "normal_high" not in row for row in channel_rows):
            raise DataNotAvailableError(
                f"Signals for window {window_id}: normal band not computed yet."
            )
        if wanted:
            channel_rows = [row for row in channel_rows if row.get("name") in wanted]
        return SignalsResponse.model_validate(
            {"window_id": window_id, "t_s": payload.get("t_s", []), "channels": channel_rows}
        )

    def _read_parquet_signals(self, window_id: str) -> dict[str, Any] | None:
        """Read one episode from a FactoryBench wide signal parquet file.

        Channels carry values only: the normal band comes from normal windows
        and is not computed yet, so ``get_signals`` refuses these payloads.
        """
        if not self.data_root.exists():
            return None
        for path in sorted(self.data_root.glob("*_signals.parquet")):
            dataset = ds.dataset(path, format="parquet")
            names = set(dataset.schema.names)
            id_name = next(
                (name for name in ("episode_id", "id", "episode", "episode_uuid") if name in names),
                None,
            )
            if id_name is None:
                continue
            table = dataset.to_table(filter=ds.field(id_name) == window_id)
            if table.num_rows == 0:
                continue
            rows = table.to_pylist()
            time_name = next(
                (
                    name
                    for name in ("timestamp_ms", "timestamp", "time_s", "time", "t_s")
                    if name in names
                ),
                None,
            )
            numeric_names = [
                name
                for name in table.column_names
                if name not in {id_name, time_name}
                and all(isinstance(row.get(name), (int, float)) for row in rows)
            ]
            time_values = [float(row[time_name]) for row in rows] if time_name else []
            if time_name == "timestamp_ms":
                time_values = [value / 1000 for value in time_values]
            channels = []
            for name in numeric_names:
                values = [float(row[name]) for row in rows]
                if values:
                    channels.append({"name": name, "values": values})
            return {"t_s": time_values, "channels": channels}
        return None
