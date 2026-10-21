"""Central agent, C1 (plan section 5.1): fixed sequence of steps, each traced as a span.

1. Situation: BIS health (sim_date), KPI registry, signal insights of the lookback window,
   breakdowns of the strongest KPIs.
2. Pre-ranking: deterministic score (cia/agents/priority.py).
3. Selection: the LLM picks at most N topics from the top candidates and bundles related ones
   (skipped when there are no more candidates than N).
4. Report: important / investigated / recommended / open. Every statement must cite evidence the
   run actually saw and may only use numbers from the data it was given (cia/llm/guardrail.py);
   one retry with the list of violations, then offending statements are dropped.
5. Fallback: without provider, on LLM errors, routing blocks or an exhausted budget the report is
   the situation without agent text (plan section 9).

Delegation to department agents follows in C2."""

import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import partial
from typing import Any, TypeVar

from pydantic import BaseModel, Field

from cia.agents.priority import SEVERITY, Candidate, rank
from cia.agents.reports import ReportStore
from cia.bi_client import BIClient, BIError, ToolResult
from cia.llm.guardrail import german, unresolved_evidence, unsupported_numbers
from cia.llm.provider import DataClass, LLMError, LLMProvider, LLMRoutingError, highest_data_class
from cia.telemetry.tracer import Run, TraceStore, Trigger, trace_run

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

SECTIONS = ("important", "investigated", "recommended")
# Keys whose values are identifiers, not data: their digits must not count as numbers the
# model may use (a UUID contains plenty of small integers).
ID_KEYS = frozenset({"insight_id", "path", "candidate_id", "evidence_options"})


@dataclass(frozen=True)
class CentralConfig:
    max_topics: int = 3
    max_candidates: int = 8
    lookback_days: int = 7
    max_breakdowns: int = 5
    breakdown_rows: int = 10
    token_budget: int = 60_000


class BudgetExceeded(RuntimeError):
    pass


# --- structured outputs -----------------------------------------------------------------------


class TopicChoice(BaseModel):
    candidate_ids: list[str] = Field(min_length=1, description="IDs der gebündelten Kandidaten")
    title: str = Field(description="Kurzer Titel des Themas")
    reason: str = Field(description="Warum das Thema heute wichtig ist, ein Satz")


class Selection(BaseModel):
    topics: list[TopicChoice]


class DraftStatement(BaseModel):
    text: str = Field(description="Ein Satz auf Deutsch")
    evidence: list[str] = Field(description="Pfade oder Insight-IDs aus evidence_options")


class ReportDraft(BaseModel):
    headline: str = Field(description="Eine Zeile: das Wichtigste des Tages")
    important: list[DraftStatement] = Field(description="Was ist wichtig")
    investigated: list[DraftStatement] = Field(description="Was wurde untersucht, was gefunden")
    recommended: list[DraftStatement] = Field(description="Was wird empfohlen")
    open_questions: list[str] = Field(description="Was ist offen")


SYSTEM_SELECT = """Du bist der zentrale Agent eines Agentic-BI-Systems für ein simuliertes Unternehmen.
Du bekommst vorsortierte Kandidaten-Themen (Score = Schwere × Geschäftswirkung × Konfidenz × Neuheit).
Wähle höchstens {max_topics} Themen für den heutigen Lagebericht. Bündle Kandidaten, die vermutlich
zusammenhängen (auch über Bereiche hinweg), zu einem Thema. Weiche nur mit gutem Grund vom Score ab.
Verwende ausschließlich die candidate_id-Werte aus den Daten."""

SYSTEM_REPORT = """Du bist der zentrale Agent eines Agentic-BI-Systems für ein simuliertes Unternehmen.
Schreibe den Lagebericht des Tages auf Deutsch, knapp und sachlich.

Regeln, deren Verletzung die Aussage verwirft:
- Jede Zahl und jedes Datum muss genau so (oder gerundet) in den Daten stehen. Keine eigenen
  Berechnungen, keine geschätzten Werte. Deutsche Schreibweise: Dezimalkomma, Tausenderpunkt.
- Jede Aussage nennt in `evidence` mindestens einen Eintrag aus `evidence_options`.
- Ursachen sind Hypothesen: formuliere sie als Vermutung, nie als Tatsache.
- „Kein Zusammenhang gefunden“ ist ein gültiges Ergebnis.
- Prozentwerte von Quoten stehen in den Daten bereits in Prozent (unit "%")."""


@dataclass
class Situation:
    sim_date: date | None
    pipeline: dict[str, Any]
    kpis: dict[str, dict[str, Any]] = field(default_factory=dict)
    insights: list[dict[str, Any]] = field(default_factory=list)
    breakdowns: dict[str, ToolResult] = field(default_factory=dict)
    paths: list[str] = field(default_factory=list)
    snapshot: dict[str, Any] | None = None


@dataclass
class Topic:
    title: str
    reason: str
    candidates: list[Candidate]

    @property
    def insight_ids(self) -> list[str]:
        return [i for c in self.candidates for i in c.insight_ids]


def _for_prompt(value: Any, scale: float = 1.0) -> Any:
    """Floats in German notation (strings), so the model copies the notation the guardrail checks."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int | float):
        scaled = round(value * scale, 4) if scale != 1.0 else value
        return german(scaled) if isinstance(scaled, float) else scaled
    if isinstance(value, dict):
        return {k: _for_prompt(v, scale) for k, v in value.items()}
    if isinstance(value, list):
        return [_for_prompt(v, scale) for v in value]
    return value


def _without_ids(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _without_ids(v) for k, v in value.items() if k not in ID_KEYS}
    if isinstance(value, list):
        return [_without_ids(v) for v in value]
    return value


def _evidence_ref(item: str) -> dict[str, str]:
    return {"kind": "query", "ref": item} if item.startswith("/api/v1/") else {"kind": "insight", "id": item}


class CentralAgent:
    def __init__(
        self,
        bi: BIClient,
        provider: LLMProvider | None,
        traces: TraceStore,
        reports: ReportStore,
        config: CentralConfig | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.bi, self.provider, self.traces, self.reports = bi, provider, traces, reports
        self.config = config or CentralConfig()
        self._clock = clock
        self._calls_at_start = 0

    # --- run ----------------------------------------------------------------------------------

    def run(self, trigger: Trigger, trigger_ref: str | None = None, snapshot: dict | None = None) -> dict:
        self._calls_at_start = len(self.provider.calls) if self.provider else 0
        sim_date = date.fromisoformat(snapshot["sim_date"]) if snapshot else None
        with trace_run(self.traces, trigger, trigger_ref, sim_date) as run:
            situation = self._situation(run, snapshot)
            run.sim_date = situation.sim_date
            candidates = self._prioritise(run, situation)

            fallback_reason: str | None = None
            draft: tuple[list[Topic], dict] | None = None
            if situation.sim_date is None:
                fallback_reason = "no_data"
            elif not candidates:
                fallback_reason = None  # nothing to report is a valid result, no LLM needed
            elif self.provider is None:
                fallback_reason = "no_provider"
            else:
                try:
                    topics = self._select(run, situation, candidates)
                    draft = topics, self._write(run, situation, topics)
                except BudgetExceeded:
                    run.status, fallback_reason = "budget_exceeded", "budget"
                except LLMRoutingError:
                    log.error(
                        "Prompt blocked by data-class routing; report without agent text", exc_info=True
                    )
                    fallback_reason = "routing_blocked"
                except LLMError as exc:
                    log.warning("LLM failed, report without agent text: %s", exc)
                    fallback_reason = "llm_error"

            with run.span("step", "report", role="central") as span:
                if draft is not None:
                    topics, body = draft
                    report = self._report(run, situation, "agent", topics, body, None)
                else:
                    topics = [self._single(situation, c) for c in candidates[: self.config.max_topics]]
                    report = self._report(
                        run,
                        situation,
                        "fallback",
                        topics,
                        self._fallback_body(situation, topics),
                        fallback_reason,
                    )
                self.reports.save(report)
                span.attributes.update(
                    report_id=report["report_id"],
                    mode=report["mode"],
                    fallback_reason=fallback_reason,
                    guardrail_violations=report["guardrail"]["violations"],
                )
        return report

    # --- steps --------------------------------------------------------------------------------

    def _tool(self, run: Run, name: str, call: Callable[[], ToolResult]) -> ToolResult:
        with run.span("tool", name, role="central") as span:
            result = call()
            span.attributes["path"] = result.path
        return result

    def _situation(self, run: Run, snapshot: dict | None) -> Situation:
        with run.span("step", "situation", role="central"):
            health = self._tool(run, "bi.health", self.bi.health)
            paths = [health.path]
            sim_date = date.fromisoformat(snapshot["sim_date"]) if snapshot else None
            if sim_date is None and health.data.get("sim_date"):
                sim_date = date.fromisoformat(health.data["sim_date"])
            situation = Situation(sim_date, health.data, paths=paths, snapshot=snapshot)
            if sim_date is None:
                return situation

            kpis = self._tool(run, "bi.kpis", self.bi.kpis)
            situation.kpis = {k["key"]: k for k in kpis.data}
            since = sim_date - timedelta(days=self.config.lookback_days)
            insights = self._tool(run, "bi.insights", partial(self.bi.insights, since=since, limit=200))
            situation.insights = insights.data
            paths += [kpis.path, insights.path]

            # Breakdowns for the KPIs of the strongest candidates (drivers by dimension).
            for candidate in rank(situation.insights, situation.kpis, (), snapshot)[
                : self.config.max_breakdowns
            ]:
                kpi = situation.kpis.get(candidate.kpi or "")
                if not kpi or not kpi.get("dimensions"):
                    continue
                dim = kpi.get("entity_type") or kpi["dimensions"][0]
                try:
                    result = self._tool(run, "bi.breakdown", partial(self.bi.breakdown, kpi["key"], dim=dim))
                except BIError:
                    continue  # recorded on the span; the report goes on without this breakdown
                situation.breakdowns[kpi["key"]] = result
                paths.append(result.path)
            return situation

    def _prioritise(self, run: Run, situation: Situation) -> list[Candidate]:
        with run.span("step", "prioritise", role="central") as span:
            if situation.sim_date is None:
                return []
            seen = self.reports.reported_insight_ids(situation.sim_date, self.config.lookback_days)
            candidates = rank(situation.insights, situation.kpis, seen, situation.snapshot)
            span.attributes["candidates"] = [
                {"topic_id": c.topic_id, "score": c.score, "department": c.department}
                for c in candidates[: self.config.max_candidates]
            ]
            return candidates

    def _select(self, run: Run, situation: Situation, candidates: list[Candidate]) -> list[Topic]:
        cfg = self.config
        offered = candidates[: cfg.max_candidates]
        if len(offered) <= cfg.max_topics:
            return [self._single(situation, c) for c in offered]
        data = {
            "max_topics": cfg.max_topics,
            "candidates": [
                {
                    "candidate_id": c.topic_id,
                    "kpi": c.kpi,
                    "department": c.department,
                    "score": c.score,
                    "insights": [i.get("summary") for i in c.insights[:3]],
                }
                for c in offered
            ],
        }
        selection = self._generate(
            run,
            "select_topics",
            SYSTEM_SELECT.format(max_topics=cfg.max_topics),
            json.dumps(_for_prompt(data), ensure_ascii=False),
            Selection,
            self._data_class(offered),
        )
        source = json.dumps(_without_ids(_for_prompt(data)), ensure_ascii=False)
        by_id = {c.topic_id: c for c in offered}
        used: set[str] = set()
        topics = []
        for choice in selection.topics:
            picked = [by_id[i] for i in dict.fromkeys(choice.candidate_ids) if i in by_id and i not in used]
            if not picked:
                continue
            used.update(c.topic_id for c in picked)
            # Title and reason reach the portal too: same number guardrail as the report.
            if unsupported_numbers(f"{choice.title} {choice.reason}", source):
                topics.append(Topic(self._single(situation, picked[0]).title, "", picked))
            else:
                topics.append(Topic(choice.title, choice.reason, picked))
            if len(topics) == cfg.max_topics:
                break
        return topics or [self._single(situation, c) for c in offered[: cfg.max_topics]]

    def _topic_data(self, situation: Situation, topic: Topic) -> dict[str, Any]:
        kpis = []
        for candidate in topic.candidates:
            kpi = situation.kpis.get(candidate.kpi or "", {})
            scale = 100.0 if kpi.get("unit") == "ratio" else 1.0
            unit = "%" if scale != 1.0 else kpi.get("unit")
            insights = [
                {
                    "insight_id": i["insight_id"],
                    "type": i["type"],
                    "severity": i["severity"],
                    "period": i["period"],
                    "direction": i.get("direction"),
                    "observed": _for_prompt(i.get("observed"), scale),
                    "expected": _for_prompt(i.get("expected"), scale),
                    "deviation_pct": _for_prompt(i.get("deviation_pct")),
                    "summary": i.get("summary"),
                }
                for i in candidate.insights
            ]
            entry: dict[str, Any] = {
                "kpi": candidate.kpi,
                "label": kpi.get("label"),
                "unit": unit,
                "direction": kpi.get("direction"),
                "department": candidate.department,
                "insights": insights,
            }
            breakdown = situation.breakdowns.get(candidate.kpi or "")
            if breakdown is not None:
                rows = breakdown.data.get("rows", [])[: self.config.breakdown_rows]
                entry["breakdown"] = {
                    "path": breakdown.path,
                    "dimension": breakdown.data.get("dimension"),
                    "current_window": breakdown.data.get("current_window"),
                    "previous_window": breakdown.data.get("previous_window"),
                    "rows": [
                        {
                            "member": r["dimension_value"],
                            "current": _for_prompt(r.get("current"), scale),
                            "previous": _for_prompt(r.get("previous"), scale),
                            "change_abs": _for_prompt(r.get("change_abs"), scale),
                            "change_pct": _for_prompt(r.get("change_pct")),
                        }
                        for r in rows
                    ],
                }
            kpis.append(entry)
        return {"title": topic.title, "reason": topic.reason, "kpis": kpis}

    def _write(self, run: Run, situation: Situation, topics: list[Topic]) -> dict:
        assert situation.sim_date is not None
        topic_data = [self._topic_data(situation, t) for t in topics]
        paths = [k["breakdown"]["path"] for t in topic_data for k in t["kpis"] if "breakdown" in k]
        ids = [i for t in topics for i in t.insight_ids]
        data = {
            "sim_date": situation.sim_date.isoformat(),
            "topic_count": len(topics),
            "topics": topic_data,
            "evidence_options": paths + ids,
        }
        prompt = json.dumps(data, ensure_ascii=False)
        source = json.dumps(_without_ids(data), ensure_ascii=False)
        data_class = self._data_class([c for t in topics for c in t.candidates])

        with run.span("step", "write", role="central") as span:
            draft = self._generate(run, "write_report", SYSTEM_REPORT, prompt, ReportDraft, data_class)
            problems = self._check(draft, source, paths, ids)
            span.attributes["first_draft_problems"] = len(problems)
        if problems:
            retry = (
                prompt
                + "\n\nDein letzter Entwurf verletzte die Regeln. Korrigiere diese Stellen:\n"
                + json.dumps(problems, ensure_ascii=False)
            )
            draft = self._generate(run, "write_report_retry", SYSTEM_REPORT, retry, ReportDraft, data_class)
        return self._apply_guardrails(draft, source, paths, ids, len(topics))

    # --- guardrails ---------------------------------------------------------------------------

    @staticmethod
    def _statement_problems(text: str, evidence: list[str], source: str, paths, ids) -> list[str]:
        problems = [f"Zahl ohne Beleg: {n}" for n in unsupported_numbers(text, source)]
        if not evidence:
            problems.append("kein Beleg")
        for ref in unresolved_evidence([_evidence_ref(e) for e in evidence], paths, ids):
            problems.append(f"Beleg nicht auflösbar: {ref.get('ref') or ref.get('id')}")
        return problems

    def _check(self, draft: ReportDraft, source: str, paths, ids) -> list[dict]:
        found = []
        for name in SECTIONS:
            for s in getattr(draft, name):
                if p := self._statement_problems(s.text, s.evidence, source, paths, ids):
                    found.append({"section": name, "text": s.text, "problems": p})
        for text in [draft.headline, *draft.open_questions]:
            if bad := unsupported_numbers(text, source):
                found.append(
                    {"section": "text", "text": text, "problems": [f"Zahl ohne Beleg: {n}" for n in bad]}
                )
        return found

    def _apply_guardrails(self, draft: ReportDraft, source: str, paths, ids, topic_count: int) -> dict:
        dropped: list[dict] = []
        sections: dict[str, list[dict]] = {}
        for name in SECTIONS:
            kept = []
            for s in getattr(draft, name):
                if p := self._statement_problems(s.text, s.evidence, source, paths, ids):
                    dropped.append({"section": name, "text": s.text, "problems": p})
                else:
                    kept.append({"text": s.text, "evidence": [_evidence_ref(e) for e in s.evidence]})
            sections[name] = kept
        headline = draft.headline
        if unsupported_numbers(headline, source):
            dropped.append({"section": "headline", "text": headline, "problems": ["Zahl ohne Beleg"]})
            headline = f"Lagebericht: {topic_count} Themen priorisiert"
        questions = []
        for q in draft.open_questions:
            if unsupported_numbers(q, source):
                dropped.append({"section": "open_questions", "text": q, "problems": ["Zahl ohne Beleg"]})
            else:
                questions.append(q)
        return {"headline": headline, "sections": sections, "open_questions": questions, "dropped": dropped}

    # --- helpers ------------------------------------------------------------------------------

    @staticmethod
    def _single(situation: Situation, candidate: Candidate) -> Topic:
        label = (situation.kpis.get(candidate.kpi or "") or {}).get("label")
        return Topic(label or candidate.topic_id, "", [candidate])

    def _data_class(self, candidates: list[Candidate]) -> DataClass:
        # Unknown classes count as confidential: never guess a lower class.
        classes = [i.get("data_class") or "confidential" for c in candidates for i in c.insights]
        return highest_data_class(classes)

    def _tokens_used(self) -> int:
        assert self.provider is not None
        return sum(u.tokens_in + u.tokens_out for u in self.provider.calls[self._calls_at_start :])

    def _generate(
        self, run: Run, name: str, system: str, user: str, schema: type[T], data_class: DataClass
    ) -> T:
        assert self.provider is not None
        if self._tokens_used() >= self.config.token_budget:
            raise BudgetExceeded(f"Run budget of {self.config.token_budget} tokens used up")
        with run.span("llm", name, role="central") as span:
            span.attributes.update(data_class=data_class, model=self.provider.model)
            before = len(self.provider.calls)
            try:
                return self.provider.generate(system, user, schema, data_class)
            finally:
                for usage in self.provider.calls[before:]:
                    span.record_usage(usage)

    def _fallback_body(self, situation: Situation, topics: list[Topic]) -> dict:
        """Situation without agent text: the BIS insight summaries, each with its insight as evidence."""
        if situation.sim_date is None:
            headline = "BI-System liefert noch keine Daten"
        elif not topics:
            headline = "Keine Auffälligkeiten im Betrachtungszeitraum"
        else:
            headline = f"Lagebild ohne Agententext: {len(topics)} Themen nach Score"
        important = []
        for topic in topics:
            strongest = max(
                topic.candidates[0].insights, key=lambda i: SEVERITY.get(i.get("severity", ""), 0)
            )
            important.append(
                {
                    "text": strongest.get("summary", ""),
                    "evidence": [{"kind": "insight", "id": strongest["insight_id"]}],
                }
            )
        sections = {"important": important, "investigated": [], "recommended": []}
        return {"headline": headline, "sections": sections, "open_questions": [], "dropped": []}

    def _report(
        self,
        run: Run,
        situation: Situation,
        mode: str,
        topics: list[Topic],
        body: dict,
        fallback_reason: str | None,
    ) -> dict:
        tokens_in = tokens_out = 0
        cost = 0.0
        if self.provider is not None:
            for u in self.provider.calls[self._calls_at_start :]:
                tokens_in, tokens_out, cost = (
                    tokens_in + u.tokens_in,
                    tokens_out + u.tokens_out,
                    cost + u.cost_eur,
                )
        return {
            "report_id": str(uuid.uuid4()),
            "run_id": run.run_id,
            "sim_date": situation.sim_date.isoformat() if situation.sim_date else None,
            "mode": mode,
            "fallback_reason": fallback_reason,
            "headline": body["headline"],
            "sections": body["sections"],
            "open_questions": body["open_questions"],
            "topics": [
                {
                    "title": t.title,
                    "reason": t.reason,
                    "kpis": [c.kpi for c in t.candidates],
                    "departments": sorted({c.department for c in t.candidates if c.department}),
                    "insight_ids": t.insight_ids,
                    "score": max(c.score for c in t.candidates),
                }
                for t in topics
            ],
            "guardrail": {"violations": len(body["dropped"]), "dropped": body["dropped"]},
            "pipeline": situation.pipeline,
            "usage": {"tokens_in": tokens_in, "tokens_out": tokens_out, "cost_eur": round(cost, 6)},
            "created_at": self._clock().isoformat(),
        }
