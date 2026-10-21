"""Situation reports of the central agent (agent.reports, migrations/002_central_agent.sql)."""

from datetime import date, timedelta
from typing import Any, Protocol

import psycopg
from psycopg.types.json import Jsonb


class ReportStore(Protocol):
    def save(self, report: dict[str, Any]) -> None: ...

    def latest(self) -> dict[str, Any] | None: ...

    def for_sim_date(self, sim_date: date) -> dict[str, Any] | None:
        """Newest report for a simulated day."""
        ...

    def reported_insight_ids(self, sim_date: date, days: int) -> set[str]:
        """Insights that were topics of reports in the `days` simulated days before `sim_date`."""
        ...


def _topic_ids(report: dict[str, Any]) -> set[str]:
    return {i for topic in report.get("topics", []) for i in topic.get("insight_ids", [])}


class PostgresReportStore:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def save(self, report: dict[str, Any]) -> None:
        sim_date = date.fromisoformat(report["sim_date"]) if report["sim_date"] else None
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                """
                INSERT INTO agent.reports (report_id, run_id, sim_date, mode, report)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (report["report_id"], report["run_id"], sim_date, report["mode"], Jsonb(report)),
            )

    def _one(self, where: str, params: tuple) -> dict[str, Any] | None:
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(
                f"SELECT report FROM agent.reports {where} "
                "ORDER BY sim_date DESC NULLS LAST, created_at DESC LIMIT 1",
                params,
            ).fetchone()
        return row[0] if row else None

    def latest(self) -> dict[str, Any] | None:
        return self._one("", ())

    def for_sim_date(self, sim_date: date) -> dict[str, Any] | None:
        return self._one("WHERE sim_date = %s", (sim_date,))

    def reported_insight_ids(self, sim_date: date, days: int) -> set[str]:
        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT jsonb_array_elements_text(topic->'insight_ids')
                FROM agent.reports, jsonb_array_elements(report->'topics') AS topic
                WHERE sim_date >= %s AND sim_date < %s
                """,
                (sim_date - timedelta(days=days), sim_date),
            ).fetchall()
        return {r[0] for r in rows}


class MemoryReportStore:
    def __init__(self):
        self.reports: list[dict[str, Any]] = []

    def save(self, report: dict[str, Any]) -> None:
        self.reports.append(report)

    def _sorted(self) -> list[dict[str, Any]]:
        # Same order as SQL: newest sim_date first (None last), then newest saved first.
        indexed = list(enumerate(self.reports))
        indexed.sort(key=lambda p: (p[1]["sim_date"] is not None, p[1]["sim_date"] or "", p[0]), reverse=True)
        return [r for _, r in indexed]

    def latest(self) -> dict[str, Any] | None:
        reports = self._sorted()
        return reports[0] if reports else None

    def for_sim_date(self, sim_date: date) -> dict[str, Any] | None:
        return next((r for r in self._sorted() if r["sim_date"] == sim_date.isoformat()), None)

    def reported_insight_ids(self, sim_date: date, days: int) -> set[str]:
        start = (sim_date - timedelta(days=days)).isoformat()
        return {
            i
            for r in self.reports
            if r["sim_date"] and start <= r["sim_date"] < sim_date.isoformat()
            for i in _topic_ids(r)
        }
