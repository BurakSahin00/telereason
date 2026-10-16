"""scripts/import_night.py on a tiny synthetic 11 Oct-shaped night and pool (#48, #29)."""

import json
import re
from collections.abc import Sequence
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from scripts.import_night import NightImportError, import_night, main
from telereason.api.schemas import WINDOW_ID_PATTERN
from telereason.data.paths import dataset_dir
from telereason.night.windows import base_window_id
from telereason.store.db import NightDb
from tests.test_night_windows import _item, _write_jsonl

RUN_ID = "2026-10-11-amd"
SIGNAL = "feedback_pos_0"  # acronym fpo0 in tests.test_night_windows


@cache
def colliding_ids(count: int = 2) -> tuple[str, ...]:
    """Item ids with the same plain window id, in sorted order."""
    seen: dict[str, list[str]] = {}
    for n in range(10_000_000):
        ms_id = f"item-{n}"
        group = seen.setdefault(base_window_id(ms_id), [])
        group.append(ms_id)
        if len(group) == count:
            return tuple(sorted(group))
    raise AssertionError("no collision found")


def pool_item(ms_id: str, dataset: str, episode: str, values: list[float]) -> dict[str, Any]:
    rows = [f"t={i * 100}: fpo0={v}, spo0=0.0" for i, v in enumerate(values)]
    return _item(ms_id, dataset, episode, rows=rows)


def note_row(
    ms_id: str, line: str, window_mean: float, score: float | None, text: str
) -> dict[str, Any]:
    window_id = base_window_id(ms_id)
    facts = {
        "window": window_id,
        "line": line,
        "task": "aursad" if line == "AURSAD" else "screwing",
        "deviating_signals": [
            {
                "signal": SIGNAL,
                "window_mean": window_mean,
                "normal_mean": 0.0,
                "z": 3.5,
                "difference": window_mean,
            }
        ],
        "look_first": SIGNAL,
    }
    return {
        "pencere": window_id,
        "hat": line,
        "skor": score,
        "en_z": 3.5,
        "girdi": json.dumps(facts),
        "son_gecti": True,
        "deneme": [{"metin": f"**{text}**", "s": 8.5, "token": 200, "gecti": True, "ihlal": []}],
    }


def write_night(
    tmp_path: Path,
    rows: list[dict[str, Any]] | None = None,
    extra_items: Sequence[dict[str, Any]] = (),
) -> tuple[Path, Path]:
    """Write results and a dataset with a night pool; return (results dir, data dir).

    Night order: the AURSAD item that sorts second, a FactoryWave item, then the AURSAD item
    that sorts first. Both AURSAD items have the same plain window id.
    """
    first, second = colliding_ids()
    results, data = tmp_path / "results", tmp_path / "data"
    results.mkdir()
    if rows is None:
        rows = [
            note_row(second, "AURSAD", 5.0, 0.95, "Score 0.95 for the second item."),
            note_row("fw-1", "FactoryWave", 7.0, None, "Facts for the FactoryWave item."),
            note_row(first, "AURSAD", 2.0, 0.5, "Score 0.5 for the first item."),
        ]
    with (results / "gece_notlar.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    flagged = sum(1 for row in rows if row["hat"] == "AURSAD" and row["skor"] >= 0.9)
    night = {
        "N": len(rows),
        "sure_s": {"kapi": 10.0, "girdi": 2.0, "tarama": 30.0, "rapor": 0.1, "toplam": 42.5},
        "rapor": {"AURSAD": {"isaretli": flagged}},
    }
    (results / "p35_gece.json").write_text(json.dumps(night), encoding="utf-8")
    gate = {
        "hat": {
            "FactoryWave": {"karar": "DAGITMA", "anomali": {"auroc": 0.53, "aralik": [0.3, 0.7]}},
            "AURSAD": {"karar": "KOSULLU", "anomali": {"auroc": 0.91, "aralik": [0.9, 0.92]}},
        }
    }
    (results / "kapi_gpu.json").write_text(json.dumps(gate), encoding="utf-8")

    root = dataset_dir(data)
    items = [
        pool_item(first, "aursad", "experiment_1_1", [1.0, 2.0, 3.0]),
        pool_item(second, "aursad", "experiment_2_2", [4.0, 5.0, 6.0]),
        pool_item("fw-1", "factorywave", "ep-a", [7.0, 7.0]),
        pool_item("fw-voraus", "vorausad", "vo-1", [7.0]),
        pool_item("fw-in-lite", "factorywave", "ep-lite", [7.0]),
        _item("fw-other-template", "factorywave", "ep-a", template_id=2),
        *extra_items,
    ]
    _write_jsonl(root / "factorybench_qa" / "level_4.jsonl", items)
    lite = [{"id": "lite-1", "provenance": {"episode": "ep-lite"}}]
    _write_jsonl(root / "factorybench_lite" / "level_1.jsonl", lite)
    for level in (2, 3, 4):
        _write_jsonl(root / "factorybench_lite" / f"level_{level}.jsonl", [])
    episodes = pa.table(
        {
            "id": ["ep-a", "ep-b", "ep-lite"],
            "created_at": [datetime(2026, 4, 7, 9)] * 3,
            "episode_metadata": ["{'task': 'screwing'}"] * 3,
        }
    )
    (root / "factorywave").mkdir(parents=True)
    pq.write_table(episodes, root / "factorywave" / "episodes.parquet")
    return results, data


@pytest.fixture
def db(tmp_path: Path) -> NightDb:
    return NightDb(tmp_path / "night.sqlite")


def test_import_writes_one_finished_run(tmp_path: Path, db: NightDb) -> None:
    results, data = write_night(tmp_path)

    imported = import_night([results], data, db)
    run = db.get_run(RUN_ID)

    assert run is not None and db.latest_finished_run() == run
    assert (run.status, run.source, run.step, run.done, run.total) == (
        "done",
        "import",
        "report",
        3,
        3,
    )
    assert run.finished_at == "2026-10-11"
    assert run.timings is not None and run.timings.total == 42.5
    assert run.gate is not None
    assert [(g.line, g.verdict, g.auroc) for g in run.gate.lines] == [
        ("FactoryWave", "closed", 0.53),
        ("AURSAD", "screening", 0.91),
    ]
    assert imported.counts["notes"] == 3
    assert imported.counts["flagged"] == 1


def test_colliding_ids_follow_the_29_rule(tmp_path: Path, db: NightDb) -> None:
    first, second = colliding_ids()
    results, data = write_night(tmp_path)

    imported = import_night([results], data, db)
    windows = {w.window.ms_id: w for w in db.windows(RUN_ID)}

    assert windows[first].window.window_id == base_window_id(first)
    assert windows[second].window.window_id == base_window_id(f"{second}#1")
    assert all(re.fullmatch(WINDOW_ID_PATTERN, w.window.window_id) for w in windows.values())
    assert imported.counts["repeated_ids"] == 1
    assert imported.counts["renamed"] == 1
    assert imported.counts["matched_by_facts"] == 2


def test_each_note_lands_on_the_item_its_facts_came_from(tmp_path: Path, db: NightDb) -> None:
    first, second = colliding_ids()
    results, data = write_night(tmp_path)

    import_night([results], data, db)
    windows = {w.window.ms_id: w for w in db.windows(RUN_ID)}

    assert windows[first].note is not None
    assert windows[first].note.text == "Score 0.5 for the first item."
    assert windows[first].window.facts[0].window_mean == 2.0
    assert windows[second].note is not None
    assert windows[second].note.text_raw == "**Score 0.95 for the second item.**"
    assert (windows[second].window.score, windows[second].window.flagged) == (0.95, True)
    # Night order is kept: the second item came first.
    assert [w.window.ms_id for w in db.windows(RUN_ID)] == [second, "fw-1", first]


def test_closed_line_windows_have_no_score_or_flag(tmp_path: Path, db: NightDb) -> None:
    results, data = write_night(tmp_path)

    import_night([results], data, db)
    stored = db.window(RUN_ID, base_window_id("fw-1"))

    assert stored is not None
    assert (stored.window.score, stored.window.flagged) == (None, None)
    assert stored.window.facts[0].plain == "Clearly off normal (3.5 standard deviations)."
    assert stored.verified


def test_reimport_replaces_the_run(tmp_path: Path, db: NightDb) -> None:
    results, data = write_night(tmp_path)

    import_night([results], data, db)
    import_night([results], data, db)

    assert db.count_windows(RUN_ID) == 3
    assert db.count_notes(RUN_ID) == 3


def test_results_are_searched_in_order(tmp_path: Path, db: NightDb) -> None:
    results, data = write_night(tmp_path)
    extra = tmp_path / "extra"
    extra.mkdir()
    (results / "kapi_gpu.json").rename(extra / "kapi_gpu.json")

    import_night([results, extra], data, db)

    assert db.count_windows(RUN_ID) == 3


def test_missing_dataset_stops_the_import(tmp_path: Path, db: NightDb) -> None:
    results, _ = write_night(tmp_path)

    with pytest.raises(NightImportError, match="level_4.jsonl"):
        import_night([results], tmp_path / "nowhere", db)


def test_note_count_must_match_the_night(tmp_path: Path, db: NightDb) -> None:
    results, data = write_night(tmp_path)
    night = json.loads((results / "p35_gece.json").read_text(encoding="utf-8"))
    night["N"] = 4
    (results / "p35_gece.json").write_text(json.dumps(night), encoding="utf-8")

    with pytest.raises(NightImportError, match="N=4"):
        import_night([results], data, db)


def test_a_pool_window_outside_the_night_can_keep_the_plain_id(tmp_path: Path, db: NightDb) -> None:
    # The pool-wide rule: an item that is in the pool but not in the night sorts first and keeps
    # the plain id, so the night's window is re-hashed (three such ids on 11 Oct).
    _, first, second = colliding_ids(3)
    rows = [note_row(second, "AURSAD", 5.0, 0.95, "Night item.")]
    extra = [
        pool_item(first, "factorywave", "ep-b", [9.0]),
        pool_item(second, "aursad", "experiment_3_3", [4.0, 5.0, 6.0]),
    ]
    results, data = write_night(tmp_path, rows, extra)

    import_night([results], data, db)
    (stored,) = db.windows(RUN_ID)

    assert stored.window.ms_id == second
    assert stored.window.window_id == base_window_id(f"{second}#1")


def test_a_note_outside_the_pool_stops_the_import(tmp_path: Path, db: NightDb) -> None:
    rows = [note_row("fw-in-lite", "FactoryWave", 7.0, None, "Lite item.")]
    results, data = write_night(tmp_path, rows)

    with pytest.raises(NightImportError, match="not in the night pool"):
        import_night([results], data, db)


def test_windows_keep_the_pools_item_id_and_group(tmp_path: Path, db: NightDb) -> None:
    results, data = write_night(tmp_path)

    import_night([results], data, db)
    stored = db.window(RUN_ID, base_window_id("fw-1"))

    assert stored is not None
    assert (stored.window.ms_id, stored.window.group_id) == ("fw-1", "gun:2026-04-07")
    assert all(w.window.ms_id and w.window.group_id for w in db.windows(RUN_ID))


def test_main_reads_data_dir_and_db_path_from_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results, data = write_night(tmp_path)
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "from-env.sqlite"))

    main(["--results", str(results)])

    assert NightDb(tmp_path / "from-env.sqlite").count_windows(RUN_ID) == 3


def test_main_prefers_the_command_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    results, data = write_night(tmp_path)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "nowhere"))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "from-env.sqlite"))
    db_path = tmp_path / "from-args.sqlite"

    main(["--results", str(results), "--data-dir", str(data), "--db", str(db_path)])

    assert NightDb(db_path).count_windows(RUN_ID) == 3
    assert not (tmp_path / "from-env.sqlite").exists()
