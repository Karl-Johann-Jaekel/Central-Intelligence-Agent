"""Deterministic pre-ranking of the central agent (plan section 5.1, step 2):

    score = severity × business impact × confidence × novelty      (each in [0, 1])

Insights on the same KPI form one candidate topic; the LLM then picks at most N topics from the
top candidates and may bundle them across departments."""

from collections.abc import Iterable
from dataclasses import dataclass, field

SEVERITY = {"info": 0.25, "warning": 0.6, "critical": 1.0}
# Insight types the central agent prioritises; briefings are the BIS's own summary, not a signal.
SIGNAL_TYPES = ("anomaly", "forecast_deviation", "data_quality")
# |deviation_pct| at which the impact factor saturates.
IMPACT_FULL_AT_PCT = 50.0
IMPACT_FLOOR = 0.1
DATA_QUALITY_IMPACT = 0.5
# evidence.score (e.g. stl_mad robust z) at which the confidence factor saturates.
CONFIDENCE_FULL_AT_SCORE = 5.0
CONFIDENCE_FLOOR = 0.3
CONFIDENCE_UNKNOWN = 0.5
# An insight that was already part of a report in the lookback window counts less.
NOVELTY_SEEN = 0.3

# KPI owner (registry) -> department of the reference company (BIS docs/reference-company.md).
# daily_snapshot.v1 names the department per KPI directly and takes precedence.
OWNER_DEPARTMENT = {
    "person:leitung-vertrieb": "sales",
    "person:leitung-logistik": "supply",
    "person:leitung-kundenservice": "customer",
    "person:leitung-marketing": "marketing",
    "person:leitung-finanzen": "finance",
}


@dataclass(frozen=True)
class Factors:
    severity: float
    impact: float
    confidence: float
    novelty: float

    @property
    def score(self) -> float:
        return round(self.severity * self.impact * self.confidence * self.novelty, 4)


@dataclass
class Candidate:
    """A topic: all signal insights on one KPI (or one insight without KPI)."""

    topic_id: str
    kpi: str | None
    department: str | None
    insights: list[dict] = field(default_factory=list)
    factors: Factors | None = None

    @property
    def score(self) -> float:
        return self.factors.score if self.factors else 0.0

    @property
    def insight_ids(self) -> list[str]:
        return [i["insight_id"] for i in self.insights]


def factors(insight: dict, seen_ids: set[str]) -> Factors:
    severity = SEVERITY.get(insight.get("severity", "info"), SEVERITY["info"])
    if insight.get("type") == "data_quality":
        impact = DATA_QUALITY_IMPACT
    elif insight.get("deviation_pct") is None:
        impact = IMPACT_FLOOR
    else:
        impact = max(IMPACT_FLOOR, min(1.0, abs(insight["deviation_pct"]) / IMPACT_FULL_AT_PCT))
    score = (insight.get("evidence") or {}).get("score")
    if score is None:
        confidence = CONFIDENCE_UNKNOWN
    else:
        confidence = max(CONFIDENCE_FLOOR, min(1.0, abs(score) / CONFIDENCE_FULL_AT_SCORE))
    novelty = NOVELTY_SEEN if insight["insight_id"] in seen_ids else 1.0
    return Factors(severity, round(impact, 4), round(confidence, 4), novelty)


def department_of(kpi: str | None, kpis: dict[str, dict], snapshot: dict | None) -> str | None:
    if kpi is None:
        return None
    for dept in (snapshot or {}).get("departments", []):
        if any(k.get("kpi") == kpi for k in dept.get("kpis", [])):
            return dept["department"]
    return OWNER_DEPARTMENT.get((kpis.get(kpi) or {}).get("owner", ""))


def rank(
    insights: Iterable[dict],
    kpis: dict[str, dict],
    seen_ids: Iterable[str] = (),
    snapshot: dict | None = None,
) -> list[Candidate]:
    """Candidates by descending score; a topic scores as its strongest insight. Ties: KPI key."""
    seen = set(seen_ids)
    topics: dict[str, Candidate] = {}
    for insight in insights:
        if insight.get("type") not in SIGNAL_TYPES:
            continue
        kpi = insight.get("kpi")
        key = f"kpi:{kpi}" if kpi else f"insight:{insight['insight_id']}"
        topic = topics.get(key)
        if topic is None:
            topic = topics[key] = Candidate(key, kpi, department_of(kpi, kpis, snapshot))
        topic.insights.append(insight)
        f = factors(insight, seen)
        if topic.factors is None or f.score > topic.factors.score:
            topic.factors = f
    return sorted(topics.values(), key=lambda c: (-c.score, c.topic_id))
