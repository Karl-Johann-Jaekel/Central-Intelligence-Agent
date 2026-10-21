"""Deterministic pre-ranking: severity × impact × confidence × novelty."""

from conftest import example

from cia.agents.priority import department_of, factors, rank

KPIS = {
    "delivery_time_avg_days": {"key": "delivery_time_avg_days", "owner": "person:leitung-logistik"},
    "gmv": {"key": "gmv", "owner": "person:leitung-vertrieb"},
}


def _insight(n: int, kpi: str | None, severity="warning", deviation=45.6, score=4.9, type_="anomaly") -> dict:
    return {
        "insight_id": f"00000000-0000-4000-8000-{n:012d}",
        "type": type_,
        "kpi": kpi,
        "severity": severity,
        "deviation_pct": deviation,
        "evidence": {"method": "stl_mad", "score": score},
    }


def test_factors_of_the_contract_example():
    f = factors(example("insight.v1.anomaly.json"), set())
    assert (f.severity, f.impact, f.confidence, f.novelty) == (0.6, 0.912, 0.98, 1.0)
    assert f.score == round(0.6 * 0.912 * 0.98, 4)


def test_bounds_and_special_cases():
    assert factors(_insight(1, "gmv", deviation=500, score=50), set()).score == 0.6  # saturates at 1
    assert factors(_insight(1, "gmv", deviation=None, score=None), set()).impact == 0.1
    assert factors(_insight(1, "gmv", score=None), set()).confidence == 0.5
    assert factors(_insight(1, "gmv", score=0.1), set()).confidence == 0.3
    assert factors(_insight(1, None, type_="data_quality", deviation=None), set()).impact == 0.5


def test_seen_insights_rank_lower():
    fresh, seen = _insight(1, "gmv"), _insight(2, "delivery_time_avg_days")
    ranked = rank([seen, fresh], KPIS, seen_ids={seen["insight_id"]})
    assert [c.kpi for c in ranked] == ["gmv", "delivery_time_avg_days"]
    assert ranked[1].factors is not None and ranked[1].factors.novelty == 0.3


def test_topics_group_by_kpi_and_ignore_briefings():
    insights = [
        _insight(1, "gmv", severity="info"),
        _insight(2, "gmv", severity="critical"),
        _insight(3, "delivery_time_avg_days"),
        _insight(4, None, type_="briefing"),
    ]
    ranked = rank(insights, KPIS)
    assert [c.topic_id for c in ranked] == ["kpi:gmv", "kpi:delivery_time_avg_days"]
    assert ranked[0].insight_ids == [insights[0]["insight_id"], insights[1]["insight_id"]]
    assert ranked[0].factors is not None and ranked[0].factors.severity == 1.0  # strongest insight
    assert (ranked[0].department, ranked[1].department) == ("sales", "supply")


def test_snapshot_department_wins_over_owner():
    snapshot = example("daily_snapshot.v1.example.json")
    assert department_of("delivery_time_avg_days", {}, snapshot) == "supply"
    assert department_of("gmv", KPIS, None) == "sales"
    assert department_of("unknown", KPIS, None) is None
    assert department_of(None, KPIS, snapshot) is None
