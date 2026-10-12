-- C0: event inbox and trace tables.

CREATE SCHEMA IF NOT EXISTS inbox;
CREATE SCHEMA IF NOT EXISTS telemetry;

-- One row per BIS event id; duplicate deliveries are ignored (ON CONFLICT DO NOTHING).
CREATE TABLE inbox.events (
    event_id     uuid PRIMARY KEY,
    event_type   text        NOT NULL CHECK (event_type IN ('insight.v1', 'daily_snapshot.v1')),
    payload      jsonb       NOT NULL,
    received_at  timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);
CREATE INDEX events_unprocessed_idx ON inbox.events (received_at) WHERE processed_at IS NULL;

-- A run of the central agent (nightly, critical insight, admin, question).
CREATE TABLE telemetry.runs (
    run_id      uuid PRIMARY KEY,
    trigger     text        NOT NULL CHECK (trigger IN ('daily_snapshot', 'insight', 'admin', 'question')),
    trigger_ref text,
    sim_date    date,
    started_at  timestamptz NOT NULL DEFAULT now(),
    ended_at    timestamptz,
    status      text        NOT NULL DEFAULT 'running'
                CHECK (status IN ('running', 'completed', 'failed', 'budget_exceeded')),
    error       text
);

-- Every step inside a run (plan section 5.6): task, step, tool and LLM call, spawn decision.
CREATE TABLE telemetry.spans (
    span_id        uuid PRIMARY KEY,
    run_id         uuid        NOT NULL REFERENCES telemetry.runs (run_id) ON DELETE CASCADE,
    parent_span_id uuid REFERENCES telemetry.spans (span_id) ON DELETE CASCADE,
    kind           text        NOT NULL CHECK (kind IN ('task', 'step', 'tool', 'llm', 'spawn')),
    name           text        NOT NULL,
    role           text,
    department     text,
    started_at     timestamptz NOT NULL DEFAULT now(),
    ended_at       timestamptz,
    status         text        NOT NULL DEFAULT 'running'
                   CHECK (status IN ('running', 'ok', 'error', 'rejected')),
    tokens_in      integer CHECK (tokens_in >= 0),
    tokens_out     integer CHECK (tokens_out >= 0),
    cost_eur       numeric(12, 6) CHECK (cost_eur >= 0),
    error          text,
    attributes     jsonb       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX spans_run_idx ON telemetry.spans (run_id, started_at);
CREATE INDEX spans_parent_idx ON telemetry.spans (parent_span_id);
