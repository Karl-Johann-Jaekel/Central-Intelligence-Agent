"""Against a real Postgres (CIA_PG_* set): migrations and inbox idempotency in SQL."""

import os
import uuid

import psycopg
import pytest
from conftest import example

from cia import settings
from cia.inbox import PostgresInbox
from cia.migrate import migrate

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not os.getenv("CIA_PG_PASSWORD"), reason="CIA_PG_PASSWORD not set"),
]


@pytest.fixture(scope="module")
def dsn() -> str:
    dsn = settings.dsn()
    migrate(dsn)
    return dsn


def test_migrate_is_idempotent(dsn):
    assert migrate(dsn) == []


def test_duplicate_store_keeps_one_row(dsn):
    inbox = PostgresInbox(dsn)
    payload = example("insight.v1.anomaly.json")
    event_id = str(uuid.uuid4())
    payload["insight_id"] = event_id
    try:
        assert inbox.store(event_id, "insight.v1", payload) is True
        assert inbox.store(event_id, "insight.v1", payload) is False
        with psycopg.connect(dsn) as conn:
            [(count,)] = conn.execute("SELECT count(*) FROM inbox.events WHERE event_id = %s", (event_id,))
        assert count == 1
        assert event_id in {e.event_id for e in inbox.claim_unprocessed(1000)}
        inbox.mark_processed(event_id)
        assert event_id not in {e.event_id for e in inbox.claim_unprocessed(1000)}
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute("DELETE FROM inbox.events WHERE event_id = %s", (event_id,))


def test_trace_store_writes_runs_and_spans(dsn):
    from cia.llm.provider import Usage
    from cia.telemetry.tracer import PostgresTraceStore, trace_run

    store = PostgresTraceStore(dsn)
    with trace_run(store, "admin", trigger_ref="test") as run:
        with run.span("step", "situation"):
            with run.span("llm", "prioritise", role="central") as span:
                span.record_usage(Usage("mistral", "m", 10, 5, 0.25))
                span.attributes["path"] = "/api/v1/health"
    try:
        with psycopg.connect(dsn) as conn:
            [(status, ended)] = conn.execute(
                "SELECT status, ended_at IS NOT NULL FROM telemetry.runs WHERE run_id = %s", (run.run_id,)
            )
            spans = conn.execute(
                """
                SELECT name, parent_span_id IS NOT NULL, status, tokens_in, cost_eur, attributes->>'path'
                FROM telemetry.spans WHERE run_id = %s ORDER BY started_at
                """,
                (run.run_id,),
            ).fetchall()
        assert (status, ended) == ("completed", True)
        assert [s[:4] for s in spans] == [("situation", False, "ok", None), ("prioritise", True, "ok", 10)]
        assert float(spans[1][4]) == 0.25 and spans[1][5] == "/api/v1/health"
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute("DELETE FROM telemetry.runs WHERE run_id = %s", (run.run_id,))


def test_report_store_and_schedule_trigger(dsn):
    from datetime import date

    from cia.agents.reports import PostgresReportStore
    from cia.telemetry.tracer import PostgresTraceStore, trace_run

    with trace_run(PostgresTraceStore(dsn), "schedule") as run:
        pass
    store = PostgresReportStore(dsn)
    insight_id = str(uuid.uuid4())
    report = {
        "report_id": str(uuid.uuid4()),
        "run_id": run.run_id,
        "sim_date": "2099-01-09",
        "mode": "fallback",
        "topics": [{"insight_ids": [insight_id]}],
    }
    try:
        store.save(report)
        assert store.for_sim_date(date(2099, 1, 9)) == report
        assert store.latest() == report  # far-future sim_date sorts first
        assert store.reported_insight_ids(date(2099, 1, 10), 7) == {insight_id}
        assert store.reported_insight_ids(date(2099, 1, 9), 7) == set()  # same day is not "earlier"
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute("DELETE FROM telemetry.runs WHERE run_id = %s", (run.run_id,))
