-- C1: situation reports of the central agent; nightly fallback run as its own trigger.

CREATE SCHEMA IF NOT EXISTS agent;

-- One report per run. `mode` = agent (LLM text passed the guardrails) or fallback (situation
-- without agent text: no provider, LLM error, budget, routing block).
CREATE TABLE agent.reports (
    report_id  uuid PRIMARY KEY,
    run_id     uuid        NOT NULL UNIQUE REFERENCES telemetry.runs (run_id) ON DELETE CASCADE,
    sim_date   date,
    mode       text        NOT NULL CHECK (mode IN ('agent', 'fallback')),
    report     jsonb       NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX reports_sim_date_idx ON agent.reports (sim_date DESC, created_at DESC);

-- Before BIS A2 there is no daily_snapshot.v1: the worker starts a nightly run itself.
ALTER TABLE telemetry.runs DROP CONSTRAINT runs_trigger_check;
ALTER TABLE telemetry.runs ADD CONSTRAINT runs_trigger_check
    CHECK (trigger IN ('daily_snapshot', 'insight', 'admin', 'question', 'schedule'));
