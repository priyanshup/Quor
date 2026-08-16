-- Quor tracking schema v4
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
--
-- files_changed (v4, QB-093 telemetry prep): nullable, populated only for
-- git-diff invocations (see quor/engine/dispatcher.py). NULL for every
-- other filter and for every row written before this version — not
-- backfilled, since the original diff text isn't retained anywhere to
-- recompute it from. Exists so a future decision on QB-093's evidence-
-- gated cross-file repeated-edit deduplication can be made from real
-- usage data instead of a guess; no reader of this column exists yet.

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
    project_key_normalized   TEXT,                            -- NULL until backfilled (v2)
    files_changed            INTEGER                          -- NULL except git-diff rows (v4)
);

-- idx_invocations_project (project_path, recorded_at) and
-- idx_invocations_filter (filter_name, recorded_at) used to be created here
-- (ADR-008) but are gone as of schema v3 — every real query scopes by
-- project_key_normalized below, not by bare project_path/filter_name, so
-- they only cost write-amplification with no read benefit. An existing
-- database that still has them from before v3 gets them dropped by
-- quor/tracking/db.py's _drop_obsolete_indexes().

CREATE INDEX IF NOT EXISTS idx_invocations_project_key
    ON invocations (project_key_normalized);

CREATE INDEX IF NOT EXISTS idx_invocations_project_key_recorded_at
    ON invocations (project_key_normalized, recorded_at);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
