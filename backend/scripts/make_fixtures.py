"""Regenerate fixtures/ from the 11 Oct MI300X run results.

Run from backend/:
    uv run python scripts/make_fixtures.py --results <results dir> --out ../fixtures

Every number comes from a file in the results directory. Fixed texts (labels,
explanations, example questions, version strings) are the examples in docs/api.md.
"""

import argparse
import json
import logging
import re
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

logger = logging.getLogger("make_fixtures")

RUN_ID = "2026-10-11-amd"
GPU_NAME = "AMD Instinct MI300X"
VERSIONS = {"rocm": "7.2.3", "vllm": "0.27.1", "python": "3.12"}

# AURSAD report threshold from .claude/rules/claims.md; checked against the run's
# flagged count below, so a wrong value stops the script.
REPORT_THRESHOLD = 0.9

REPORT_ITEMS_PER_LINE = 10
WINDOWS_PAGE_SIZE = 20

VERDICTS = {"DAGITMA": "closed", "KOSULLU": "screening", "GECER": "open"}
LABELS = {
    "closed": "Facts, not diagnosis",
    "screening": "Abnormal-behaviour screening only",
}
EXPLANATIONS = {
    "closed": (
        "On new days, fault prediction is no better than chance, so this line gets facts only."
    ),
    "screening": "Normal and faulty runs separate well, but the fault cause is not reliable.",
}
LINE_ORDER = ("FactoryWave", "AURSAD")

EXAMPLES = [
    {"lang": "en", "text": "What went wrong in this record?"},
    {"lang": "tr", "text": "Bu kayıtta ne ters gitti?"},
]
NIGHT_RUN_STARTED = {"run_id": "run-20261015-1", "status": "queued"}

ASK_QUESTION = "What went wrong in this record?"
ASK_TOOL = "classify_fault"
ASK_SUMMARY = "diagnosis closed on this line"

MIB_PER_GIB = 1024
MARKDOWN_CHARS = re.compile(r"[*`]")
WHITESPACE = re.compile(r"\s+")

GENERATED_SUFFIXES = (".json", ".sse")

README = """\
# Fixtures

Example API responses generated from the 11 Oct 2026 run on one AMD Instinct MI300X.
Do not edit by hand; regenerate from `backend/`:

```bash
uv run python scripts/make_fixtures.py --results <results dir> --out ../fixtures
```

`backend/tests/test_fixtures.py` validates every file against the models in
`backend/telereason/api/schemas.py`.

| File | Endpoint |
|---|---|
| `health.json` | `GET /api/health` |
| `system.json` | `GET /api/system` |
| `gate.json` | `GET /api/gate` |
| `report_latest.json` | `GET /api/report/latest` |
| `windows.json` | `GET /api/windows?page=1&page_size={page_size}` (no filter, night order) |
| `window_detail.json` | `GET /api/windows/{{window_id}}`: object keyed by window id |
| `ask_request.json` | `POST /api/ask` request body |
| `ask.sse` | `POST /api/ask` response stream |
| `examples.json` | `GET /api/examples` |
| `night_run_started.json` | `POST /api/night-runs` (202) |
| `night_run.json` | `GET /api/night-runs/{{run_id}}` (the stored 11 Oct night) |
| pending (#28) | `GET /api/windows/{{window_id}}/signals` (needs raw time series) |

`window_detail.json` holds {n_detail} windows: every window listed in `report_latest.json`
plus one unflagged AURSAD window.
By line: {detail_mix}.
`windows.json` is one page, so most of these windows are not in it; in fake mode
`GET /api/windows/{{window_id}}` reads `window_detail.json` first.

## Where the values come from

- Timings, window counts, flagged count, model: `p35_gece.json` (measured night run).
- Verdicts (`DAGITMA` = `closed`, `KOSULLU` = `screening`, `GECER` = `open`), AUROC and its
  interval, XGBoost version: `kapi_gpu.json`.
- Windows, facts, look-first signal, notes: `gece_notlar.jsonl`. The note is the last attempt
  with markdown marks removed; `verified` is whether that attempt passed the verifier. Window ids
  that occur more than once in the run are skipped.
- `plain`: |z| < 2 within the normal range, 2 to 3 slightly off normal, 3 or more clearly off
  normal (to be aligned with #16). `top_features` is empty until #45.
- AURSAD `threshold` {threshold}: from `.claude/rules/claims.md`; the script stops unless it
  flags exactly the run's flagged count.
- `memory_total_gib`: `p35_gece.json`. `memory_used_gib`: card peak in the load test while the
  engine was loaded (`p34_yuk.json`, phase C), MiB / 1024.
- Version strings other than `xgboost`, labels, explanations and example questions:
  `docs/api.md`.
- `ask.sse`: target behaviour on a FactoryWave window (diagnosis closed). `total_ms` is the
  single-question p90 alone on the card (`p35_amd.json`); `tokens` is the mean tokens per
  question with one user (`kapasite_k1.json`); `ms` is `null` because tool time was not measured.

Contains small samples derived from FactoryBench (CC BY 4.0); see `DATA_NOTICE.md`.
"""


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def iter_notes(path: Path) -> Iterator[dict[str, Any]]:
    """Yield one compact record per night-note row; token ids are dropped here."""
    with path.open(encoding="utf-8") as f:
        for raw in f:
            row = json.loads(raw)
            facts = json.loads(row["girdi"])
            yield {
                "window_id": row["pencere"],
                "line": row["hat"],
                "task": facts["task"],
                "score": row["skor"],
                "max_abs_z": row["en_z"],
                "look_first": facts["look_first"],
                "deviating_signals": facts["deviating_signals"],
                "note": clean_note(row["deneme"][-1]["metin"]),
                "verified": row["son_gecti"],
            }


def clean_note(text: str) -> str:
    return WHITESPACE.sub(" ", MARKDOWN_CHARS.sub("", text)).strip()


def plain_reading(z: float) -> str:
    # Simple first rule; issue #16 will align it with the technician wording.
    size = abs(z)
    if size < 2:
        reading = "Within the normal range"
    elif size < 3:
        reading = "Slightly off normal"
    else:
        reading = "Clearly off normal"
    return f"{reading} ({size:g} standard deviations)."


def flagged(window: dict[str, Any]) -> bool | None:
    return None if window["score"] is None else window["score"] >= REPORT_THRESHOLD


def summary(window: dict[str, Any]) -> dict[str, Any]:
    return {
        "window_id": window["window_id"],
        "line": window["line"],
        "task": window["task"],
        "score": window["score"],
        "flagged": flagged(window),
        "look_first": window["look_first"],
        "verified": window["verified"],
    }


def report_item(window: dict[str, Any]) -> dict[str, Any]:
    return {
        "window_id": window["window_id"],
        "task": window["task"],
        "score": window["score"],
        "look_first": window["look_first"],
        "note": window["note"],
        "verified": window["verified"],
    }


def detail(window: dict[str, Any], verdict: str) -> dict[str, Any]:
    facts = [
        {
            "signal": s["signal"],
            "window_mean": s["window_mean"],
            "normal_mean": s["normal_mean"],
            "difference": s["difference"],
            "z": s["z"],
            "plain": plain_reading(s["z"]),
        }
        for s in window["deviating_signals"]
    ]
    return {
        "window_id": window["window_id"],
        "line": window["line"],
        "task": window["task"],
        "verdict": verdict,
        "score": window["score"],
        "flagged": flagged(window),
        "look_first": window["look_first"],
        "facts": facts,
        "top_features": [],  # gate feature contributions arrive with issue #45
        "note": window["note"],
        "verified": window["verified"],
    }


def timings(sure_s: dict[str, float]) -> dict[str, float]:
    return {
        "gate": sure_s["kapi"],
        "facts": sure_s["girdi"],
        "scan": sure_s["tarama"],
        "report": sure_s["rapor"],
        "total": sure_s["toplam"],
    }


def sse_message(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def build(results: Path) -> tuple[dict[str, Any], str]:
    """Return the JSON fixtures by file name and the /api/ask SSE sample."""
    night = read_json(results / "p35_gece.json")
    gate_run = read_json(results / "kapi_gpu.json")
    amd = read_json(results / "p35_amd.json")
    capacity = read_json(results / "kapasite_k1.json")
    load = read_json(results / "p34_yuk.json")

    windows = list(iter_notes(results / "gece_notlar.jsonl"))
    if len(windows) != night["N"]:
        raise SystemExit(f"gece_notlar.jsonl has {len(windows)} rows, p35_gece.json N={night['N']}")

    # Some W- ids occur twice in the run; the API needs unique ids, so skip them.
    counts = Counter(w["window_id"] for w in windows)
    unique = [w for w in windows if counts[w["window_id"]] == 1]
    skipped = len(windows) - len(unique)
    logger.info("%d windows, %d with a repeated id skipped", len(windows), skipped)

    verdicts = {line: VERDICTS[gate_run["hat"][line]["karar"]] for line in LINE_ORDER}
    for line in LINE_ORDER:
        if verdicts[line] not in LABELS:
            raise SystemExit(f"No label for verdict {verdicts[line]!r} on {line}")
    by_line = {line: [w for w in unique if w["line"] == line] for line in LINE_ORDER}

    reported_flagged = night["rapor"]["AURSAD"]["isaretli"]
    n_flagged = sum(bool(flagged(w)) for w in windows if w["line"] == "AURSAD")
    if n_flagged != reported_flagged:
        raise SystemExit(
            f"Threshold {REPORT_THRESHOLD} flags {n_flagged} windows; the run reported "
            f"{reported_flagged}"
        )

    # Report order: AURSAD by score (stable sort, so ties keep the night's order);
    # FactoryWave by the largest deviation.
    ranked = {
        "AURSAD": sorted(by_line["AURSAD"], key=lambda w: -w["score"]),
        "FactoryWave": sorted(
            (w for w in by_line["FactoryWave"] if w["max_abs_z"] is not None),
            key=lambda w: -abs(w["max_abs_z"]),
        ),
    }
    top = {line: ranked[line][:REPORT_ITEMS_PER_LINE] for line in LINE_ORDER}

    timings_s = timings(night["sure_s"])

    health = {"status": "ok", "engine": "vllm", "model": night["model"], "ready": True}

    peak_mib = load["fazlar"]["C"]["arka"]["bellek"]["kart_tepe_mib"]
    system = {
        "gpu": {
            "name": GPU_NAME,
            "memory_total_gib": night["cihaz"]["toplam_gib"],
            "memory_used_gib": peak_mib / MIB_PER_GIB,
        },
        "versions": {
            "rocm": VERSIONS["rocm"],
            "vllm": VERSIONS["vllm"],
            "xgboost": gate_run["xgb_surum"],
            "python": VERSIONS["python"],
        },
        "last_night": {"run_id": RUN_ID, "timings_s": timings_s},
    }

    gate = {
        "run_id": RUN_ID,
        "lines": [
            {
                "line": line,
                "verdict": verdicts[line],
                "label": LABELS[verdicts[line]],
                "auroc": gate_run["hat"][line]["anomali"]["auroc"],
                "auroc_ci": gate_run["hat"][line]["anomali"]["aralik"],
                "explanation": EXPLANATIONS[verdicts[line]],
            }
            for line in LINE_ORDER
        ],
    }

    report_lines = []
    for line in ("AURSAD", "FactoryWave"):
        scored = verdicts[line] != "closed"
        report_lines.append(
            {
                "line": line,
                "verdict": verdicts[line],
                "label": LABELS[verdicts[line]],
                "scanned": night["rapor"][line]["taranan"],
                "flagged": night["rapor"][line]["isaretli"] if scored else None,
                "threshold": REPORT_THRESHOLD if scored else None,
                "items": [report_item(w) for w in top[line]],
            }
        )
    report = {"run_id": RUN_ID, "timings_s": timings_s, "lines": report_lines}

    # First page in the night's stored order, no filter.
    window_list = {
        "page": 1,
        "page_size": WINDOWS_PAGE_SIZE,
        "total": len(windows),
        "items": [summary(w) for w in unique[:WINDOWS_PAGE_SIZE]],
    }

    # Every report window opens in fake mode; one unflagged AURSAD window is added so
    # the UI also shows a window below the threshold. Report order, duplicates dropped.
    unflagged = next(w for w in by_line["AURSAD"] if flagged(w) is False)
    picked = [*top["AURSAD"], *top["FactoryWave"], unflagged]
    window_detail = {w["window_id"]: detail(w, verdicts[w["line"]]) for w in picked}

    night_run = {
        "run_id": RUN_ID,
        "status": "done",
        "step": "report",
        "done": night["N"],
        "total": night["N"],
        "timings_s": {k: timings_s[k] for k in ("gate", "facts", "scan", "report")},
    }

    closed_window = top["FactoryWave"][0]["window_id"]
    closed_gate = gate_run["hat"]["FactoryWave"]["anomali"]
    low, high = closed_gate["aralik"]
    answer_text = (
        "Diagnosis is closed on this line: on new days, fault prediction is no better than "
        f"chance (AUROC {closed_gate['auroc']:.3f}, interval {low:.3f} to {high:.3f}). "
        "Only verified facts are reported for this window."
    )
    tool_call = {"turn": 1, "tool": ASK_TOOL, "args": {"window_id": closed_window}}
    tool_result = {"turn": 1, "tool": ASK_TOOL, "summary": ASK_SUMMARY, "ms": None}
    answer = {"answer": "DIAGNOSIS_CLOSED", "answer_source": "tool", "text": answer_text}
    done = {
        "total_ms": round(amd["sirali_gece"]["canli_p90_yalniz"] * 1000),
        "tokens": round(capacity["seviye"]["1"]["token_ort"]),
    }
    sse = "".join(
        sse_message(event, data)
        for event, data in (
            ("tool_call", tool_call),
            ("tool_result", tool_result),
            ("answer", answer),
            ("done", done),
        )
    )

    fixtures = {
        "health.json": health,
        "system.json": system,
        "gate.json": gate,
        "report_latest.json": report,
        "windows.json": window_list,
        "window_detail.json": window_detail,
        "ask_request.json": {"question": ASK_QUESTION, "window_id": closed_window},
        "examples.json": {"examples": EXAMPLES},
        "night_run_started.json": NIGHT_RUN_STARTED,
        "night_run.json": night_run,
    }
    return fixtures, sse


def detail_mix(details: dict[str, dict[str, Any]]) -> str:
    """Describe the detail windows, e.g. '10 flagged AURSAD, 1 unflagged AURSAD, 10 FactoryWave'."""
    kinds = {True: "flagged ", False: "unflagged ", None: ""}
    counts = Counter(f"{kinds[w['flagged']]}{w['line']}" for w in details.values())
    order = [f"{kind}{line}" for line in ("AURSAD", "FactoryWave") for kind in kinds.values()]
    return ", ".join(f"{counts[name]} {name}" for name in order if counts[name])


def write(out: Path, fixtures: dict[str, Any], sse: str) -> None:
    files = {
        name: json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        for name, data in fixtures.items()
    }
    files["ask.sse"] = sse
    files["README.md"] = README.format(
        page_size=WINDOWS_PAGE_SIZE,
        n_detail=len(fixtures["window_detail.json"]),
        detail_mix=detail_mix(fixtures["window_detail.json"]),
        threshold=REPORT_THRESHOLD,
    )
    out.mkdir(parents=True, exist_ok=True)
    # Regenerate from scratch: drop generated files that this run no longer writes.
    for stale in out.iterdir():
        if stale.suffix in GENERATED_SUFFIXES and stale.name not in files:
            logger.info("Removing stale %s", stale.name)
            stale.unlink()
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8", newline="\n")
    logger.info("Wrote %d files to %s", len(files), out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results", type=Path, required=True, help="11 Oct results directory")
    parser.add_argument("--out", type=Path, required=True, help="fixtures directory to write")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    fixtures, sse = build(args.results)
    write(args.out, fixtures, sse)


if __name__ == "__main__":
    main()
