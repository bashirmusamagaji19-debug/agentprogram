CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'partial', 'failed')),
    data_mode TEXT NOT NULL,
    request_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_summary TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    retention_deadline TIMESTAMPTZ NOT NULL,
    idempotency_key TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS runs_owner_idempotency_idx
    ON runs(owner_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS runs_owner_created_idx
    ON runs(owner_id, created_at DESC);

CREATE TABLE IF NOT EXISTS artifacts (
    owner_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    artifact_key TEXT NOT NULL,
    object_key TEXT NOT NULL,
    display_name TEXT NOT NULL,
    mime TEXT NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    sha256 TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    retention_deadline TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (owner_id, run_id, artifact_key)
);

CREATE INDEX IF NOT EXISTS artifacts_owner_run_idx
    ON artifacts(owner_id, run_id);

CREATE TABLE IF NOT EXISTS diagnostics (
    id BIGSERIAL PRIMARY KEY,
    owner_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS diagnostics_owner_run_idx
    ON diagnostics(owner_id, run_id);

CREATE TABLE IF NOT EXISTS jobs (
    owner_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    job_id TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (owner_id, run_id, job_id)
);

CREATE TABLE IF NOT EXISTS matches (
    owner_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    job_id TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (owner_id, run_id, job_id)
);
