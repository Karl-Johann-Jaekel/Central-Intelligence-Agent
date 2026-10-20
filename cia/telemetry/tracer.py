"""Traces of runs and spans (plan section 5.6) into telemetry.runs / telemetry.spans.

Usage:
    with trace_run(store, "admin") as run:
        with run.span("tool", "bi.health") as span:
            span.attributes["path"] = result.path
        with run.span("llm", "prioritise", role="central") as span:
            span.record_usage(usage)

A span that raises ends with status `error` and the exception text, a run with `failed`; both
re-raise. Spans nest: a span opened inside another becomes its child."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal, Protocol

import psycopg
from psycopg.types.json import Jsonb

from cia.llm.provider import Usage

Trigger = Literal["daily_snapshot", "insight", "admin", "question"]
SpanKind = Literal["task", "step", "tool", "llm", "spawn"]
RunStatus = Literal["running", "completed", "failed", "budget_exceeded"]
SpanStatus = Literal["running", "ok", "error", "rejected"]


class TraceStore(Protocol):
    def start_run(
        self, run_id: str, trigger: Trigger, trigger_ref: str | None, sim_date: date | None
    ) -> None: ...

    def end_run(self, run_id: str, status: RunStatus, error: str | None, sim_date: date | None) -> None: ...

    def start_span(
        self,
        span_id: str,
        run_id: str,
        parent_span_id: str | None,
        kind: SpanKind,
        name: str,
        role: str | None,
        department: str | None,
    ) -> None: ...

    def end_span(self, span: "Span") -> None: ...


@dataclass
class Span:
    span_id: str
    run_id: str
    parent_span_id: str | None
    kind: SpanKind
    name: str
    role: str | None = None
    department: str | None = None
    status: SpanStatus = "ok"
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_eur: float | None = None
    error: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def record_usage(self, usage: Usage) -> None:
        """Adds one provider call; a span may cover several calls (retry after a guardrail hit)."""
        self.tokens_in = (self.tokens_in or 0) + usage.tokens_in
        self.tokens_out = (self.tokens_out or 0) + usage.tokens_out
        self.cost_eur = round((self.cost_eur or 0.0) + usage.cost_eur, 6)
        self.attributes.setdefault("provider", usage.provider)
        self.attributes.setdefault("model", usage.model)


class Run:
    def __init__(self, store: TraceStore, run_id: str):
        self.store = store
        self.run_id = run_id
        self.status: RunStatus = "completed"
        self.sim_date: date | None = None
        self._stack: list[str] = []

    @contextmanager
    def span(
        self, kind: SpanKind, name: str, role: str | None = None, department: str | None = None
    ) -> Iterator[Span]:
        parent = self._stack[-1] if self._stack else None
        span = Span(str(uuid.uuid4()), self.run_id, parent, kind, name, role, department)
        self.store.start_span(span.span_id, self.run_id, parent, kind, name, role, department)
        self._stack.append(span.span_id)
        try:
            yield span
        except BaseException as exc:
            span.status, span.error = "error", f"{type(exc).__name__}: {exc}"[:2000]
            raise
        finally:
            self._stack.pop()
            self.store.end_span(span)


@contextmanager
def trace_run(
    store: TraceStore, trigger: Trigger, trigger_ref: str | None = None, sim_date: date | None = None
) -> Iterator[Run]:
    run = Run(store, str(uuid.uuid4()))
    run.sim_date = sim_date
    store.start_run(run.run_id, trigger, trigger_ref, sim_date)
    try:
        yield run
    except BaseException as exc:
        store.end_run(run.run_id, "failed", f"{type(exc).__name__}: {exc}"[:2000], run.sim_date)
        raise
    store.end_run(run.run_id, run.status, None, run.sim_date)


class PostgresTraceStore:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def start_run(
        self, run_id: str, trigger: Trigger, trigger_ref: str | None, sim_date: date | None
    ) -> None:
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                "INSERT INTO telemetry.runs (run_id, trigger, trigger_ref, sim_date) VALUES (%s, %s, %s, %s)",
                (run_id, trigger, trigger_ref, sim_date),
            )

    def end_run(self, run_id: str, status: RunStatus, error: str | None, sim_date: date | None) -> None:
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                """
                UPDATE telemetry.runs
                SET ended_at = now(), status = %s, error = %s, sim_date = coalesce(%s, sim_date)
                WHERE run_id = %s
                """,
                (status, error, sim_date, run_id),
            )

    def start_span(
        self,
        span_id: str,
        run_id: str,
        parent_span_id: str | None,
        kind: SpanKind,
        name: str,
        role: str | None,
        department: str | None,
    ) -> None:
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                """
                INSERT INTO telemetry.spans (span_id, run_id, parent_span_id, kind, name, role, department)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (span_id, run_id, parent_span_id, kind, name, role, department),
            )

    def end_span(self, span: Span) -> None:
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                """
                UPDATE telemetry.spans
                SET ended_at = now(), status = %s, tokens_in = %s, tokens_out = %s, cost_eur = %s,
                    error = %s, attributes = attributes || %s
                WHERE span_id = %s
                """,
                (
                    span.status,
                    span.tokens_in,
                    span.tokens_out,
                    span.cost_eur,
                    span.error,
                    Jsonb(span.attributes),
                    span.span_id,
                ),
            )


class MemoryTraceStore:
    """In-memory store with the same shape as the tables (tests, dry runs)."""

    def __init__(self):
        self.runs: dict[str, dict[str, Any]] = {}
        self.spans: dict[str, dict[str, Any]] = {}

    def start_run(
        self, run_id: str, trigger: Trigger, trigger_ref: str | None, sim_date: date | None
    ) -> None:
        self.runs[run_id] = {
            "run_id": run_id,
            "trigger": trigger,
            "trigger_ref": trigger_ref,
            "sim_date": sim_date,
            "started_at": datetime.now(UTC),
            "ended_at": None,
            "status": "running",
            "error": None,
        }

    def end_run(self, run_id: str, status: RunStatus, error: str | None, sim_date: date | None) -> None:
        run = self.runs[run_id]
        run.update(
            ended_at=datetime.now(UTC), status=status, error=error, sim_date=sim_date or run["sim_date"]
        )

    def start_span(
        self,
        span_id: str,
        run_id: str,
        parent_span_id: str | None,
        kind: SpanKind,
        name: str,
        role: str | None,
        department: str | None,
    ) -> None:
        self.spans[span_id] = {
            "span_id": span_id,
            "run_id": run_id,
            "parent_span_id": parent_span_id,
            "kind": kind,
            "name": name,
            "role": role,
            "department": department,
            "started_at": datetime.now(UTC),
            "ended_at": None,
            "status": "running",
            "tokens_in": None,
            "tokens_out": None,
            "cost_eur": None,
            "error": None,
            "attributes": {},
        }

    def end_span(self, span: Span) -> None:
        row = self.spans[span.span_id]
        row.update(
            ended_at=datetime.now(UTC),
            status=span.status,
            tokens_in=span.tokens_in,
            tokens_out=span.tokens_out,
            cost_eur=span.cost_eur,
            error=span.error,
            attributes=row["attributes"] | span.attributes,
        )
