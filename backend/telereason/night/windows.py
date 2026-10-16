"""The night's windows: FactoryBench L4-t1 troubleshooting items from the leak-free pool.

One window is one item of ``factorybench_qa/level_4.jsonl`` with ``template_id`` 1.  The pool
keeps an item when it has a root-cause label, does not come from voraus-AD (CC BY-NC-SA 4.0,
never used) and touches no episode of any FactoryBench Lite item (Lite is the benchmark's test
set).  This is the 11 Oct pool: 8,508 windows at revision 2561c2c9.

A window id is ``W-`` plus the last six decimal digits of the CRC-32 of the item id, as on 11 Oct.
Colliding ids keep the plain id for the item id that sorts first; every other item in the
collision is re-hashed with a ``#n`` suffix until its id is free.  The item id is kept as
``ms_id``.

The gate's groups are built here too: FactoryWave windows group by the recording day of their
episode (``created_at`` in ``episodes.parquet``), AURSAD windows by experiment-number block.
The group strings are the 11 Oct ones because the fold assignment hashes them.
"""

from __future__ import annotations

import json
import logging
import math
import re
import zlib
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

from telereason.api.schemas import Line
from telereason.data.paths import dataset_dir
from telereason.night.channels import plan_measured_pairs, setting_b, vocabulary

log = logging.getLogger(__name__)

POOL_FILE = "factorybench_qa/level_4.jsonl"
LITE_FILES = tuple(f"factorybench_lite/level_{level}.jsonl" for level in (1, 2, 3, 4))
EPISODES_FILE = "factorywave/episodes.parquet"
TEMPLATE_ID = 1

# AURSAD episode ids look like experiment_<N>_<M>; N is the experiment number.  The 11 Oct
# grouping cut experiments 1-4094 (the range over its L4-t1 and L2-t7 items) into 20 equal blocks.
AURSAD_EPISODE = re.compile(r"^[A-Za-z]+[_-]?(\d+)(?:[_-]\d+)*$")
AURSAD_BLOCKS = 20
AURSAD_EXPERIMENTS = (1, 4094)

_ROW = re.compile(r"^\s*t\s*=\s*([^:]+?)\s*:\s*(.*)$")
_KEY = re.compile(r"[A-Za-z_][\w.\-]*$")
_TASK = re.compile(r"""['"]task['"]\s*:\s*['"]([^'"]+)['"]""")


@dataclass(frozen=True)
class Window:
    window_id: str
    ms_id: str
    line: Line
    task: str
    episode: str
    start: int
    length: int
    label: str
    group: str
    t_ms: np.ndarray
    channels: tuple[str, ...]
    # One row per time step, one column per channel; NaN where a row lacks the channel.
    values: np.ndarray = field(repr=False)

    def column(self, channel: str) -> np.ndarray | None:
        try:
            return self.values[:, self.channels.index(channel)]
        except ValueError:
            return None

    @property
    def first_row(self) -> list[str]:
        """Channels present in the first row (the 11 Oct vocabulary counted these)."""
        if not len(self.values):
            return []
        return [name for name, x in zip(self.channels, self.values[0], strict=True) if x == x]


@dataclass
class Pool:
    windows: list[Window]
    counts: dict[str, int]
    # Setting B channels in vocabulary order, and their plan-versus-measured pairs.
    channels: list[str]
    pairs: list[tuple[str, str]]

    def __post_init__(self) -> None:
        self._by_id = {window.window_id: window for window in self.windows}

    def get(self, window_id: str) -> Window | None:
        return self._by_id.get(window_id)


# ---------------------------------------------------------------- items


def _json_field(value: Any) -> Any:
    if isinstance(value, str) and value.strip()[:1] in "{[":
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def read_items(path: Path) -> Iterator[dict[str, Any]]:
    """Items of a FactoryBench JSONL file; unreadable lines and items without an id are skipped."""
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                log.warning("%s: line %d is not JSON, skipped", path.name, number)
                continue
            if not item.get("id"):
                continue
            for key in ("provenance", "context", "options"):
                if key in item:
                    item[key] = _json_field(item[key])
            yield item


def _provenance_values(provenance: Any, keys: tuple[str, ...]) -> list[str]:
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in keys and isinstance(value, str) and value:
                    found.append(value)
                elif isinstance(value, dict | list):
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(provenance)
    return found


def episode_ids(item: dict[str, Any]) -> list[str]:
    """Episodes an item draws on, including nested and two-series provenance."""
    provenance = item.get("provenance") or {}
    episodes = _provenance_values(provenance, ("episode", "episode_a", "episode_b"))
    if not episodes:
        episodes = _provenance_values(provenance, ("sampled_subfolder", "counterpart_subfolder"))
    return sorted(set(episodes))


def datasets(item: dict[str, Any]) -> list[str]:
    provenance = item.get("provenance") or {}
    found = _provenance_values(provenance, ("dataset", "dataset_a", "dataset_b"))
    return [value.lower() for value in found]


def is_voraus(item: dict[str, Any]) -> bool:
    return any("voraus" in value for value in datasets(item))


def label(item: dict[str, Any]) -> str | None:
    """Normalised root cause of an L4-t1 item."""
    raw = item.get("root_cause")
    if not raw:
        return None
    text = re.sub(r"[\s\-]+", "_", str(raw).strip().strip("`'\".").lower())
    return re.sub(r"[^a-z0-9_]", "", text) or None


def lite_episodes(root: Path) -> set[str]:
    """Every episode touched by any FactoryBench Lite item, voraus-AD included."""
    episodes: set[str] = set()
    for name in LITE_FILES:
        for item in read_items(root / name):
            episodes.update(episode_ids(item))
    return episodes


# ---------------------------------------------------------------- telemetry


def _acronyms(context: dict[str, Any]) -> dict[str, str]:
    fmt = context.get("time_series_format")
    mapping = fmt.get("acronym_mapping") if isinstance(fmt, dict) else None
    mapping = mapping or context.get("acronym_mapping")
    return dict(mapping) if isinstance(mapping, dict) else {}


def _number(text: str) -> float | None:
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _rows(lines: Iterable[Any]) -> tuple[list[float], list[str]]:
    """Rows with a numeric time: (times in ms, bodies)."""
    times: list[float] = []
    bodies: list[str] = []
    for line in lines:
        match = _ROW.match(line) if isinstance(line, str) else None
        if match is None:
            continue
        t = _number(match.group(1))
        if t is not None:
            times.append(t)
            bodies.append(match.group(2))
    return times, bodies


def _uniform_values(bodies: list[str]) -> tuple[list[str], np.ndarray] | None:
    """Fast path: every row lists the same keys in the same order and every value is a number."""
    raw_keys = bodies[0].replace("=", ",").split(",")[0::2]
    tokens = ",".join(bodies).replace("=", ",").split(",")
    width = len(raw_keys)
    if len(tokens) != 2 * width * len(bodies) or tokens[0::2] != raw_keys * len(bodies):
        return None
    keys = [key.strip() for key in raw_keys]
    if not all(_KEY.match(key) for key in keys) or len(set(keys)) != width:
        return None
    try:
        numbers = list(map(float, tokens[1::2]))
    except ValueError:
        return None
    values = np.array(numbers, dtype="float64").reshape(len(bodies), width)
    values[~np.isfinite(values)] = np.nan
    return keys, values


def _mixed_values(bodies: list[str]) -> tuple[list[str], np.ndarray]:
    order: dict[str, int] = {}
    cells: list[tuple[int, int, float]] = []
    for row, body in enumerate(bodies):
        for part in body.split(","):
            key, equals, text = part.partition("=")
            key = key.strip()
            if not equals or not _KEY.match(key):
                continue
            value = _number(text)
            if value is not None:
                cells.append((row, order.setdefault(key, len(order)), value))
    values = np.full((len(bodies), len(order)), np.nan)
    if cells:
        rows, columns, numbers = zip(*cells, strict=True)
        values[rows, columns] = numbers
    return list(order), values


def parse_series(item: dict[str, Any]) -> tuple[np.ndarray, tuple[str, ...], np.ndarray]:
    """``t=<ms>: acronym=value, ...`` rows as (t in ms, channel names, values with NaN gaps).

    Acronyms become long channel names; non-numeric values and rows without a numeric time
    are dropped, as on 11 Oct.
    """
    context = item.get("context") or {}
    if not isinstance(context, dict):
        return np.zeros(0), (), np.zeros((0, 0))
    lines = context.get("time_series") or []
    if isinstance(lines, str):
        lines = [line for line in lines.splitlines() if line.strip()]
    times, bodies = _rows(lines)
    if not bodies:
        return np.zeros(0), (), np.zeros((0, 0))
    keys, values = _uniform_values(bodies) or _mixed_values(bodies)
    names = _acronyms(context)
    return np.asarray(times, dtype="float64"), tuple(names.get(k, k) for k in keys), values


# ---------------------------------------------------------------- ids and groups


def base_window_id(ms_id: str) -> str:
    return f"W-{zlib.crc32(ms_id.encode()) % 10**6:06d}"


def assign_window_ids(ms_ids: Iterable[str]) -> dict[str, str]:
    """Item id -> window id, unique over the given items (see the module docstring)."""
    by_base: dict[str, list[str]] = {}
    for ms_id in sorted(set(ms_ids)):
        by_base.setdefault(base_window_id(ms_id), []).append(ms_id)
    taken = set(by_base)
    out: dict[str, str] = {}
    for base, members in sorted(by_base.items()):
        out[members[0]] = base
        for ms_id in members[1:]:
            n = 1
            while (candidate := base_window_id(f"{ms_id}#{n}")) in taken:
                n += 1
            taken.add(candidate)
            out[ms_id] = candidate
    return out


def aursad_block(episode: str) -> str | None:
    """``au:bNN``, the experiment-number block of an AURSAD episode, or None."""
    match = AURSAD_EPISODE.match(episode)
    if match is None:
        return None
    low, high = AURSAD_EXPERIMENTS
    width = (high - low + 1) / AURSAD_BLOCKS
    block = min(AURSAD_BLOCKS - 1, int((int(match.group(1)) - low) / width))
    return f"au:b{block:02d}"


def factorywave_episodes(path: Path) -> dict[str, tuple[str | None, str | None]]:
    """FactoryWave episode -> (recording day ``gun:YYYY-MM-DD``, task) from ``episodes.parquet``."""
    table = pq.read_table(path, columns=["id", "created_at", "episode_metadata"])
    out: dict[str, tuple[str | None, str | None]] = {}
    for row in table.to_pylist():
        created = row.get("created_at")
        day = f"gun:{created.strftime('%Y-%m-%d')}" if created is not None else None
        match = _TASK.search(str(row.get("episode_metadata") or ""))
        out[str(row["id"])] = (day, match.group(1) if match else None)
    return out


def group_of(line: Line, episode: str, day: str | None) -> str:
    """The gate's group: recording day on FactoryWave, experiment block on AURSAD.

    An episode without a day or block keeps its own id behind ``?``, as on 11 Oct.
    """
    group = aursad_block(episode) if line == "AURSAD" else day
    return group or f"?{episode}"


# ---------------------------------------------------------------- pool


def select_items(root: Path, counts: Counter[str]) -> Iterator[tuple[dict[str, Any], str, str]]:
    """Pool items as (item, episode, root cause); ``counts`` records what was read and dropped."""
    excluded = lite_episodes(root)
    for item in read_items(root / POOL_FILE):
        if item.get("template_id") != TEMPLATE_ID:
            continue
        counts["read"] += 1
        if is_voraus(item):
            counts["voraus"] += 1
            continue
        item_episodes = episode_ids(item)
        if not item_episodes:
            counts["no_episode"] += 1
            continue
        if excluded.intersection(item_episodes):
            counts["lite"] += 1
            continue
        root_cause = label(item)
        if root_cause is None:
            counts["unlabelled"] += 1
            continue
        yield item, item_episodes[0], root_cause


def load_pool(data_dir: str | Path) -> Pool:
    """Read the L4-t1 pool under ``DATA_DIR/factorybench`` and log what was dropped."""
    root = dataset_dir(data_dir)
    episodes = factorywave_episodes(root / EPISODES_FILE)
    counts: Counter[str] = Counter()
    parsed: list[dict[str, Any]] = []
    for item, episode, root_cause in select_items(root, counts):
        provenance = item.get("provenance") or {}
        line: Line = "AURSAD" if "aursad" in datasets(item) else "FactoryWave"
        day, task = episodes.get(episode, (None, None))
        t_ms, channels, values = parse_series(item)
        parsed.append(
            {
                "ms_id": str(item["id"]),
                "line": line,
                "task": "aursad" if line == "AURSAD" else (task or "unknown"),
                "episode": episode,
                "start": int(provenance.get("subseries_start_index") or 0),
                "length": int(provenance.get("subseries_length") or len(t_ms)),
                "label": root_cause,
                "group": group_of(line, episode, day),
                "t_ms": t_ms,
                "channels": channels,
                "values": values,
            }
        )
    ids = assign_window_ids(fields["ms_id"] for fields in parsed)
    windows = [Window(window_id=ids[fields["ms_id"]], **fields) for fields in parsed]
    counts["kept"] = len(windows)
    counts["renamed"] = sum(1 for w in windows if w.window_id != base_window_id(w.ms_id))
    channels = setting_b(vocabulary(window.first_row for window in windows))
    log.info(
        "night pool: %d L4-t1 items read, %d voraus-AD dropped, %d touching Lite dropped, "
        "%d without episode, %d unlabelled, %d kept; %d window ids re-hashed",
        counts["read"],
        counts["voraus"],
        counts["lite"],
        counts["no_episode"],
        counts["unlabelled"],
        counts["kept"],
        counts["renamed"],
    )
    return Pool(
        windows=windows, counts=dict(counts), channels=channels, pairs=plan_measured_pairs(channels)
    )
