-- Forward migration for existing deployments; no run records are rewritten.
BEGIN;
ALTER TABLE runs DROP CONSTRAINT IF EXISTS runs_status_check;
ALTER TABLE runs ADD CONSTRAINT runs_status_check
    CHECK (status IN ('queued', 'running', 'awaiting_approval', 'succeeded', 'partial', 'failed', 'rejected'));
COMMIT;
