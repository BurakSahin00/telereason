"""Note counts of one night run in the SQLite store, written as JSON for results/.

Run from backend/:
    python scripts/notes_summary.py --db <DB_PATH> --run <run_id> --out <file>

Only the standard library, so it also runs in the droplet container. The store is opened read
only, and the output holds counts, never note texts:

- ``windows``: windows of the run; ``notes``: windows with at least one note attempt;
  ``windows_without_note``: the others; ``note_attempts``: attempts, retries included.
- ``verified``: windows whose last attempt passed the verifier, so their note is shown.
- ``passed_first_attempt`` and ``passed_second_attempt``: attempts 1 and 2 that passed.
- ``mean_completion_tokens``: mean over the attempts that stored a token count
  (``completion_tokens_counted``).
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

logger = logging.getLogger("notes_summary")

COUNTS = {
    "windows": "SELECT COUNT(*) FROM windows WHERE run_id = :run",
    "notes": "SELECT COUNT(DISTINCT window_id) FROM notes WHERE run_id = :run",
    "windows_without_note": (
        "SELECT COUNT(*) FROM windows w WHERE w.run_id = :run AND NOT EXISTS "
        "(SELECT 1 FROM notes n WHERE n.run_id = w.run_id AND n.window_id = w.window_id)"
    ),
    "note_attempts": "SELECT COUNT(*) FROM notes WHERE run_id = :run",
    "verified": (
        "SELECT COUNT(*) FROM notes n WHERE n.run_id = :run AND n.passed = 1 AND n.attempt = "
        "(SELECT MAX(attempt) FROM notes m WHERE m.run_id = n.run_id AND m.window_id = n.window_id)"
    ),
    "passed_first_attempt": (
        "SELECT COUNT(*) FROM notes WHERE run_id = :run AND attempt = 1 AND passed = 1"
    ),
    "passed_second_attempt": (
        "SELECT COUNT(*) FROM notes WHERE run_id = :run AND attempt = 2 AND passed = 1"
    ),
    "completion_tokens_counted": "SELECT COUNT(completion_tokens) FROM notes WHERE run_id = :run",
    "mean_completion_tokens": "SELECT AVG(completion_tokens) FROM notes WHERE run_id = :run",
}


class SummaryError(RuntimeError):
    """The store cannot be read or has no such run."""


def connect_read_only(db: Path) -> sqlite3.Connection:
    """The store opened read only; a missing file is an error, never created."""
    if not db.is_file():
        raise SummaryError(f"{db} does not exist")
    return sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)


def summarize(db: Path, run_id: str) -> dict[str, Any]:
    conn = connect_read_only(db)
    try:
        found = conn.execute("SELECT 1 FROM night_runs WHERE run_id = ?", (run_id,)).fetchone()
        if found is None:
            raise SummaryError(f"no night run {run_id} in {db}")
        counts = {
            name: conn.execute(sql, {"run": run_id}).fetchone()[0] for name, sql in COUNTS.items()
        }
    except sqlite3.Error as exc:
        raise SummaryError(f"{db}: {exc}") from exc
    finally:
        conn.close()
    return {"run_id": run_id, **counts}


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, required=True, help="SQLite store of night runs")
    parser.add_argument("--run", required=True, help="run id")
    parser.add_argument("--out", type=Path, required=True, help="JSON file to write")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        summary = summarize(args.db, args.run)
    except SummaryError as exc:
        raise SystemExit(str(exc)) from exc
    args.out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(summary, indent=2) + "\n"
    args.out.write_text(text, encoding="utf-8", newline="\n")
    logger.info(
        "run %s: %d windows, %d verified notes; written to %s",
        args.run,
        summary["windows"],
        summary["verified"],
        args.out,
    )


if __name__ == "__main__":
    main()
