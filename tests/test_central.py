"""Central agent end to end against a mocked BIS API and a scripted provider (DoD C1: every
number in the report is backed, every run has a timeline)."""

import json
from datetime import date

import httpx
import pytest
from conftest import FakeProvider

from cia.agents.central import CentralAgent, CentralConfig
from cia.agents.reports import MemoryReportStore
from cia.bi_client import BIClient
from cia.llm.guardrail import unsupported_numbers
from cia.llm.provider import LLMError, MistralProvider
from cia.telemetry.tracer import MemoryTraceStore

SIM_DATE = "2018-01-10"


def _kpi(key, label, unit, dims, owner):
    return {
        "key": key,
        "label": label,
        "unit": unit,
        "direction": "higher_is_better",
        "dimensions": dims,
        "entity_type": dims[-1] if dims else None,
        "owner": f"person:leitung-{owner}",
        "data_class": "public",
    }


KPIS = [
    _kpi("gmv", "Umsatz (GMV)", "BRL", ["region", "category"], "vertrieb"),
    _kpi("on_time_rate", "Pünktlichkeitsquote", "ratio", ["region"], "logistik"),
    _kpi("review_score_avg", "Ø Bewertung", "score", [], "kundenservice"),
    _kpi("new_customers", "Neukunden", "count", ["region"], "marketing"),
]


def _insight(n, kpi, severity="warning", observed=27.9, expected=19.2, deviation=45.3, data_class="public"):
    return {
        "schema_version": "insight.v1",
        "insight_id": f"00000000-0000-4000-8000-{n:012d}",
        "source": "business-intelligence-system",
        "type": "anomaly",
        "kpi": kpi,
        "period": {"start": SIM_DATE, "end": SIM_DATE, "grain": "day"},
        "severity": severity,
        "direction": "down",
        "observed": observed,
        "expected": expected,
        "deviation_pct": deviation,
        "entity_refs": [],
        "evidence": {"method": "stl_mad", "score": 4.5},
        "summary": f"Auffälligkeit {n} bei {kpi}.",
        "data_class": data_class,
        "created_at": "2026-10-10T02:00:00Z",
    }


INSIGHTS = [
    _insight(1, "on_time_rate", severity="critical", observed=0.71, expected=0.9, deviation=-21.1),
    _insight(2, "gmv", observed=81234.5, expected=95000.0, deviation=-14.49),
    _insight(3, "review_score_avg", observed=3.9, expected=4.2, deviation=-7.14),
    _insight(4, "new_customers", severity="info", observed=120, expected=150, deviation=-20.0),
    {**_insight(5, None), "type": "briefing"},
]
ID1, ID2 = INSIGHTS[0]["insight_id"], INSIGHTS[1]["insight_id"]
ON_TIME_PATH = "/api/v1/kpis/on_time_rate/breakdown?dim=region"
GMV_PATH = "/api/v1/kpis/gmv/breakdown?dim=category"


def _breakdown(kpi, dim, rows):
    return {
        "kpi": kpi,
        "grain": "day",
        "dimension": dim,
        "current_window": {"start": "2018-01-04", "end": SIM_DATE},
        "previous_window": {"start": "2017-12-28", "end": "2018-01-03"},
        "rows": rows,
    }


BREAKDOWNS = {
    ON_TIME_PATH: _breakdown("on_time_rate", "region", [
        {"dimension_value": "SP", "entity_id": "region:sp", "current": 0.6123, "previous": 0.91,
         "change_abs": -0.2977, "change_pct": -32.71},
    ]),
    GMV_PATH: _breakdown("gmv", "category", [
        {"dimension_value": "moveis", "entity_id": "category:moveis", "current": 10500.0, "previous": 14000.0,
         "change_abs": -3500.0, "change_pct": -25.0},
    ]),
}  # fmt: skip


class FakeBIS:
    def __init__(self, health=None, insights=INSIGHTS):
        self.health = health or (200, {"status": "ok", "sim_date": SIM_DATE, "latest_kpi_date": SIM_DATE})
        self.insights = insights
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.raw_path.decode()
        self.requests.append(path)
        if path == "/api/v1/health":
            return httpx.Response(self.health[0], json=self.health[1])
        if path == "/api/v1/kpis":
            return httpx.Response(200, json=KPIS)
        if path.startswith("/api/v1/insights"):
            return httpx.Response(200, json=self.insights)
        if path in BREAKDOWNS:
            return httpx.Response(200, json=BREAKDOWNS[path])
        return httpx.Response(404, json={"detail": "not found"})


SELECTION = {
    "topics": [
        {
            "candidate_ids": ["kpi:on_time_rate", "kpi:review_score_avg"],
            "title": "Lieferprobleme",
            "reason": "r",
        },
        {"candidate_ids": ["kpi:gmv", "kpi:unknown"], "title": "Umsatzrückgang", "reason": "r"},
    ]
}


def _draft(**overrides):
    draft = {
        "headline": "Pünktlichkeit fällt auf 71 %",
        "important": [
            {"text": "Die Pünktlichkeitsquote liegt bei 71 % statt 90 %.", "evidence": [ID1]},
            {"text": "In SP sank sie auf 61,2 % (−32,71 %).", "evidence": [ON_TIME_PATH]},
        ],
        "investigated": [{"text": "Umsatz in moveis: 10.500 BRL statt 14.000 BRL.", "evidence": [GMV_PATH]}],
        "recommended": [
            {"text": "Vermutlich Engpass beim Versand in SP prüfen.", "evidence": [ID1, ON_TIME_PATH]}
        ],
        "open_questions": ["Hängt der Umsatzrückgang mit der Lieferzeit zusammen?"],
    }
    return draft | overrides


def _agent(provider, bis=None, **config):
    bis = bis or FakeBIS()
    client = BIClient(
        "http://bis", client=httpx.Client(base_url="http://bis", transport=httpx.MockTransport(bis))
    )
    traces, reports = MemoryTraceStore(), MemoryReportStore()
    agent = CentralAgent(client, provider, traces, reports, CentralConfig(**config))
    return agent, traces, reports, bis


def _spans(traces, kind=None):
    return [s for s in traces.spans.values() if kind is None or s["kind"] == kind]


def test_agent_report_every_number_backed_and_timeline():
    provider = FakeProvider(SELECTION, _draft())
    agent, traces, reports, _ = _agent(provider)
    report = agent.run("admin", "test")

    assert report["mode"] == "agent" and report["fallback_reason"] is None
    assert report["sim_date"] == SIM_DATE
    assert report["guardrail"] == {"violations": 0, "dropped": []}
    assert [t["title"] for t in report["topics"]] == ["Lieferprobleme", "Umsatzrückgang"]
    assert report["topics"][0]["departments"] == ["customer", "supply"]
    assert report["topics"][1]["kpis"] == ["gmv"]  # unknown candidate id dropped
    assert len(report["sections"]["important"]) == 2
    assert report["sections"]["important"][1]["evidence"] == [{"kind": "query", "ref": ON_TIME_PATH}]
    assert reports.latest() == report

    # The model saw ratios in percent and German notation, never raw fractions or UUID digits as data.
    write_prompt = json.loads(provider.prompts[1]["user"])
    on_time = write_prompt["topics"][0]["kpis"][0]
    assert on_time["unit"] == "%" and on_time["insights"][0]["observed"] == "71,0"
    assert on_time["breakdown"]["rows"][0]["current"] == "61,23"
    assert set(write_prompt["evidence_options"]) >= {ID1, ON_TIME_PATH, GMV_PATH}

    # Timeline: steps, tool calls with paths, LLM calls with tokens; run completed.
    [run] = traces.runs.values()
    assert run["status"] == "completed" and run["sim_date"] == date(2018, 1, 10)
    tools = [(s["name"], s["attributes"].get("path"), s["status"]) for s in _spans(traces, "tool")]
    assert [t[:2] for t in tools[:3]] == [
        ("bi.health", "/api/v1/health"),
        ("bi.kpis", "/api/v1/kpis"),
        ("bi.insights", "/api/v1/insights?limit=200&since=2018-01-03"),
    ]
    # review_score_avg has no dimension; the new_customers breakdown fails (404) and is skipped.
    assert sorted(tools[3:], key=str) == sorted(
        [
            ("bi.breakdown", ON_TIME_PATH, "ok"),
            ("bi.breakdown", GMV_PATH, "ok"),
            ("bi.breakdown", None, "error"),
        ],
        key=str,
    )
    llm = _spans(traces, "llm")
    assert [s["name"] for s in llm] == ["select_topics", "write_report"]
    assert all(s["tokens_in"] == 100 and s["tokens_out"] == 20 for s in llm)
    assert report["usage"] == {"tokens_in": 200, "tokens_out": 40, "cost_eur": 0.0}
    assert all(s["ended_at"] for s in _spans(traces))
    assert [s["name"] for s in _spans(traces) if s["status"] != "ok"] == ["bi.breakdown"]


def test_invented_number_triggers_one_retry():
    bad = _draft(headline="Pünktlichkeit fällt auf 65 %")
    provider = FakeProvider(SELECTION, bad, _draft())
    agent, traces, _, _ = _agent(provider)
    report = agent.run("admin")
    assert report["guardrail"]["violations"] == 0
    assert "65" in provider.prompts[2]["user"].split("Korrigiere")[1]
    assert [s["name"] for s in _spans(traces, "llm")][-1] == "write_report_retry"


def test_statements_still_wrong_after_retry_are_dropped():
    bad = _draft(
        headline="Umsatz bricht um 99 % ein",
        important=[
            {"text": "Die Pünktlichkeitsquote liegt bei 71 %.", "evidence": [ID1]},
            {"text": "Der Umsatz fiel um 12.5 %.", "evidence": [GMV_PATH]},  # wrong notation
            {"text": "Lieferzeit 37 Tage.", "evidence": [ID1]},  # invented
            {"text": "Laut Suchindex gibt es Engpässe.", "evidence": ["/api/v1/search?q=x"]},  # not called
            {"text": "Ohne Beleg.", "evidence": []},
        ],
    )
    provider = FakeProvider(SELECTION, bad, bad)
    agent, _, _, _ = _agent(provider)
    report = agent.run("admin")
    kept = [s["text"] for s in report["sections"]["important"]]
    assert kept == ["Die Pünktlichkeitsquote liegt bei 71 %."]
    assert report["headline"] == "Lagebericht: 2 Themen priorisiert"
    assert report["guardrail"]["violations"] == 5  # 4 statements + headline
    problems = {d["text"]: d["problems"] for d in report["guardrail"]["dropped"]}
    assert problems["Der Umsatz fiel um 12.5 %."] == ["Zahl ohne Beleg: 12.5"]
    assert problems["Laut Suchindex gibt es Engpässe."] == ["Beleg nicht auflösbar: /api/v1/search?q=x"]
    assert problems["Ohne Beleg."] == ["kein Beleg"]


def test_every_number_in_a_kept_statement_is_in_the_data():
    provider = FakeProvider(SELECTION, _draft())
    agent, _, _, _ = _agent(provider)
    report = agent.run("admin")
    source = provider.prompts[1]["user"]
    for name, statements in report["sections"].items():
        for s in statements:
            assert unsupported_numbers(s["text"], source) == [], (name, s["text"])


def test_few_candidates_skip_the_selection_call():
    provider = FakeProvider(_draft())
    agent, traces, _, _ = _agent(provider, max_topics=4)
    report = agent.run("admin")
    assert [s["name"] for s in _spans(traces, "llm")] == ["write_report"]
    assert len(report["topics"]) == 4


def test_without_provider_the_report_is_the_situation():
    agent, traces, reports, _ = _agent(None)
    report = agent.run("admin")
    assert report["mode"] == "fallback" and report["fallback_reason"] == "no_provider"
    assert [t["title"] for t in report["topics"]] == ["Pünktlichkeitsquote", "Umsatz (GMV)", "Neukunden"]
    assert report["sections"]["important"][0] == {
        "text": "Auffälligkeit 1 bei on_time_rate.",
        "evidence": [{"kind": "insight", "id": ID1}],
    }
    assert _spans(traces, "llm") == []
    assert reports.latest() == report


def test_llm_error_falls_back_and_is_traced():
    agent, traces, _, _ = _agent(FakeProvider(LLMError("Mistral API error 503 after 4 attempts")))
    report = agent.run("admin")
    assert report["mode"] == "fallback" and report["fallback_reason"] == "llm_error"
    [llm] = _spans(traces, "llm")
    assert llm["status"] == "error" and "503" in llm["error"]
    assert next(iter(traces.runs.values()))["status"] == "completed"


def test_budget_ends_the_run_without_agent_text():
    agent, traces, _, _ = _agent(FakeProvider(SELECTION, _draft()), token_budget=100)
    report = agent.run("admin")
    assert report["fallback_reason"] == "budget"
    assert next(iter(traces.runs.values()))["status"] == "budget_exceeded"
    assert [s["name"] for s in _spans(traces, "llm")] == ["select_topics"]


def test_confidential_data_never_reaches_mistral():
    def never(request):
        raise AssertionError("request sent to Mistral")

    mistral = MistralProvider("m", "key", client=httpx.Client(transport=httpx.MockTransport(never)))
    insights = [_insight(1, "gmv", data_class="confidential"), _insight(2, "on_time_rate")]
    agent, _, _, _ = _agent(mistral, FakeBIS(insights=insights), max_topics=1)
    report = agent.run("admin")
    assert report["mode"] == "fallback" and report["fallback_reason"] == "routing_blocked"


def test_bis_without_data():
    bis = FakeBIS(health=(503, {"status": "down"}))
    agent, _, _, _ = _agent(FakeProvider(), bis)
    report = agent.run("admin")
    assert report["sim_date"] is None and report["fallback_reason"] == "no_data"
    assert report["headline"] == "BI-System liefert noch keine Daten"
    assert bis.requests == ["/api/v1/health"]


def test_quiet_day_needs_no_llm():
    provider = FakeProvider()
    agent, _, _, _ = _agent(provider, FakeBIS(insights=[]))
    report = agent.run("admin")
    assert report["mode"] == "fallback" and report["fallback_reason"] is None
    assert report["headline"] == "Keine Auffälligkeiten im Betrachtungszeitraum"
    assert provider.prompts == []


def test_snapshot_sets_the_day_and_earlier_topics_lose_novelty():
    agent, traces, reports, _ = _agent(None)
    first = agent.run("admin")
    assert first["topics"][0]["kpis"] == ["on_time_rate"]
    reports.reports[0]["sim_date"] = "2018-01-09"  # yesterday's report covered these insights
    snapshot = {"sim_date": SIM_DATE, "departments": []}
    second = agent.run("daily_snapshot", "snap-1", snapshot)
    assert second["sim_date"] == SIM_DATE
    prioritise = [s for s in _spans(traces) if s["name"] == "prioritise"][-1]
    assert all(c["score"] < 0.3 for c in prioritise["attributes"]["candidates"][:3])


def test_broken_bis_fails_the_run():
    bis = FakeBIS()
    bis.insights = None  # type: ignore[assignment]

    def broken(request):
        if request.url.path == "/api/v1/kpis":
            return httpx.Response(500, json={})
        return bis(request)

    agent, traces, reports, _ = _agent(None, broken)  # type: ignore[arg-type]
    with pytest.raises(Exception, match="500"):
        agent.run("admin")
    assert next(iter(traces.runs.values()))["status"] == "failed"
    assert reports.latest() is None


def test_topic_titles_with_invented_numbers_are_replaced():
    selection = {
        "topics": [{"candidate_ids": ["kpi:gmv"], "title": "Umsatz −37 %", "reason": "seit 5 Wochen"}]
    }
    agent, _, _, _ = _agent(FakeProvider(selection, _draft(), _draft()), max_topics=1)
    report = agent.run("admin")
    assert (report["topics"][0]["title"], report["topics"][0]["reason"]) == ("Umsatz (GMV)", "")
