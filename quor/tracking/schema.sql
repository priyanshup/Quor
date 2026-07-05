-- Quor tracking schema v2
-- All migrations tracked in schema_migrations table.
-- Project paths stored as Path.as_posix() — backslashes never appear.
--
-- project_key_normalized (v2): precomputed project-identity column,
-- populated at write time via normalize_project_path() (see
-- quor/tracking/db.py). Nullable for backward compatibility with rows
-- written before this column existed — query_gain() lazily backfills any
-- NULL values it finds (reusing normalize_project_path() itself as a
-- registered SQL function, not a re-implementation of its rule), so no
-- manual migration is required. Once populated, project-scoped aggregation
-- queries by simple equality/LIKE-prefix match against this column, not by
-- re-deriving normalization at read time.

CREATE TABLE IF NOT EXISTS invocations (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    command                  TEXT    NOT NULL,
    project_path             TEXT    NOT NULL,
    original_tokens          INTEGER NOT NULL DEFAULT 0,
    final_tokens             INTEGER NOT NULL DEFAULT 0,
    filter_name              TEXT,                            -- NULL means passthrough
    was_passthrough          INTEGER NOT NULL DEFAULT 0,      -- 1 if no filter matched
    duration_ms              REAL    NOT NULL DEFAULT 0,
    recorded_at              TEXT    NOT NULL DEFAULT (datetime('now')),
    schema_version           INTEGER NOT NULL DEFAULT 1,
    project_key_normalized   TEXT                             -- NULL until backfilled (v2)
);

CREATE INDEX IF NOT EXISTS idx_invocations_project
    ON invocations (project_path, recorded_at);

CREATE INDEX IF NOT EXISTS idx_invocations_filter
    ON invocations (filter_name, recorded_at);

CREATE INDEX IF NOT EXISTS idx_invocations_project_key
    ON invocations (project_key_normalized);

CREATE INDEX IF NOT EXISTS idx_invocations_project_key_recorded_at
    ON invocations (project_key_normalized, recorded_at);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
