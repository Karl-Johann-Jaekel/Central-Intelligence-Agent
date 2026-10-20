"""Tracer: nesting, usage, error status (memory store)."""

from datetime import date

import pytest

from cia.llm.provider import Usage
from cia.telemetry.tracer import MemoryTraceStore, trace_run


def test_spans_nest_and_record_usage():
    store = MemoryTraceStore()
    with trace_run(store, "admin", trigger_ref="cli") as run:
        with run.span("step", "situation") as step:
            with run.span("tool", "bi.health") as tool:
                tool.attributes["path"] = "/api/v1/health"
        with run.span("llm", "prioritise", role="central") as llm:
            llm.record_usage(Usage("mistral", "m", 100, 20, 0.001))
            llm.record_usage(Usage("mistral", "m", 50, 10, 0.0005))
        run.sim_date = date(2026, 10, 7)

    [row] = store.runs.values()
    assert row["status"] == "completed" and row["sim_date"] == date(2026, 10, 7)
    spans = {s["name"]: s for s in store.spans.values()}
    assert spans["bi.health"]["parent_span_id"] == step.span_id
    assert spans["situation"]["parent_span_id"] is None
    assert spans["prioritise"]["parent_span_id"] is None  # sibling, not child of the closed step
    assert spans["bi.health"]["attributes"] == {"path": "/api/v1/health"}
    assert (spans["prioritise"]["tokens_in"], spans["prioritise"]["tokens_out"]) == (150, 30)
    assert spans["prioritise"]["cost_eur"] == pytest.approx(0.0015)
    assert spans["prioritise"]["attributes"]["model"] == "m"
    assert all(s["status"] == "ok" and s["ended_at"] for s in spans.values())
    assert llm.run_id == row["run_id"]


def test_errors_end_span_and_run():
    store = MemoryTraceStore()
    with pytest.raises(ValueError):
        with trace_run(store, "insight") as run:
            with run.span("tool", "bi.kpis"):
                raise ValueError("boom")
    [span] = store.spans.values()
    assert span["status"] == "error" and span["error"] == "ValueError: boom"
    [row] = store.runs.values()
    assert row["status"] == "failed" and "boom" in row["error"]


def test_explicit_statuses():
    store = MemoryTraceStore()
    with trace_run(store, "admin") as run:
        with run.span("spawn", "request") as span:
            span.status = "rejected"
        run.status = "budget_exceeded"
    assert [s["status"] for s in store.spans.values()] == ["rejected"]
    assert [r["status"] for r in store.runs.values()] == ["budget_exceeded"]
