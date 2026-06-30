-- Quor tracking schema v1
-- All migrations tracked in schema_migrations table.
-- Project paths stored as Path.as_posix() — backslashes never appear.
-- Queried with GLOB, not LIKE, for project-scoped aggregation.

CREATE TABLE IF NOT EXISTS invocations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    command          TEXT    NOT NULL,
    project_path     TEXT    NOT NULL,
    original_tokens  INTEGER NOT NULL DEFAULT 0,
    final_tokens     INTEGER NOT NULL DEFAULT 0,
    filter_name      TEXT,                            -- NULL means passthrough
    was_passthrough  INTEGER NOT NULL DEFAULT 0,      -- 1 if no filter matched
    duration_ms      REAL    NOT NULL DEFAULT 0,
    recorded_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    schema_version   INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_invocations_project
    ON invocations (project_path, recorded_at);

CREATE INDEX IF NOT EXISTS idx_invocations_filter
    ON invocations (filter_name, recorded_at);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
