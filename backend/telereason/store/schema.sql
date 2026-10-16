-- TeleReason night store (SQLite, WAL). Created by telereason.store.db.NightDb.init().
-- JSON columns hold API models (api/schemas.py) dumped with Pydantic.

CREATE TABLE IF NOT EXISTS night_runs (
    run_id      TEXT PRIMARY KEY,
    status      TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed')),
    step        TEXT CHECK (step IN ('gate', 'facts', 'scan', 'report')),
    done        INTEGER NOT NULL DEFAULT 0,
    total       INTEGER NOT NULL DEFAULT 0,
    max_windows INTEGER,
    source      TEXT NOT NULL CHECK (source IN ('import', 'live')),
    -- ISO 8601 UTC; an imported run keeps only the date it ran.
    started_at  TEXT,
    finished_at TEXT,
    -- Seconds per step; NULL until the step finishes.
    t_gate      REAL,
    t_facts     REAL,
    t_scan      REAL,
    t_report    REAL,
    t_total     REAL,
    -- GateResponse of the run; NULL until the gate step finishes.
    gate_json   TEXT,
    error       TEXT
);

CREATE TABLE IF NOT EXISTS windows (
    run_id            TEXT NOT NULL REFERENCES night_runs (run_id) ON DELETE CASCADE,
    window_id         TEXT NOT NULL,
    -- Order of the window in the night; ties in the report keep this order.
    seq               INTEGER NOT NULL,
    -- FactoryBench item id the window id is hashed from; NULL when unknown.
    ms_id             TEXT,
    line              TEXT NOT NULL CHECK (line IN ('FactoryWave', 'AURSAD')),
    task              TEXT NOT NULL,
    group_id          TEXT,
    fold              INTEGER,
    -- Gate score and flag; NULL on a closed line.
    score             REAL,
    flagged           INTEGER,
    max_abs_z         REAL,
    look_first        TEXT NOT NULL DEFAULT '',
    -- list[Fact] and list[FeatureContribution].
    facts_json        TEXT NOT NULL DEFAULT '[]',
    top_features_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (run_id, window_id)
);

CREATE INDEX IF NOT EXISTS windows_by_seq ON windows (run_id, seq);

CREATE TABLE IF NOT EXISTS notes (
    run_id            TEXT NOT NULL,
    window_id         TEXT NOT NULL,
    -- 1 for the first attempt, 2 for the retry.
    attempt           INTEGER NOT NULL,
    text_raw          TEXT NOT NULL,
    -- The text shown: markdown marks removed.
    text              TEXT NOT NULL,
    passed            INTEGER NOT NULL,
    -- list[str]: what the verifier rejected.
    violations_json   TEXT NOT NULL DEFAULT '[]',
    completion_tokens INTEGER,
    ms                INTEGER,
    PRIMARY KEY (run_id, window_id, attempt),
    FOREIGN KEY (run_id, window_id) REFERENCES windows (run_id, window_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reports (
    run_id     TEXT NOT NULL REFERENCES night_runs (run_id) ON DELETE CASCADE,
    line       TEXT NOT NULL CHECK (line IN ('FactoryWave', 'AURSAD')),
    -- Order of the line in the report.
    position   INTEGER NOT NULL,
    verdict    TEXT NOT NULL CHECK (verdict IN ('closed', 'screening', 'open')),
    label      TEXT NOT NULL,
    scanned    INTEGER NOT NULL,
    flagged    INTEGER,
    threshold  REAL,
    -- list[ReportItem].
    items_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (run_id, line)
);
