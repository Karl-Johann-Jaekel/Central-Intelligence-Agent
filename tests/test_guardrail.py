"""Guardrails: numbers must come from tool results, evidence must resolve."""

import pytest

from cia.llm.guardrail import canonical_path, german, unresolved_evidence, unsupported_numbers

TOOL_RESULT = {
    "kpi": "revenue",
    "current": 1234.5,
    "previous": 1100,
    "change_pct": 12.23,
    "period": "2026-10-01",
}


def test_german_notation_of_tool_results():
    text = german(TOOL_RESULT)
    assert "1234,5" in text and "12,23" in text and "1100" in text
    assert german(1e-7) == "0,0000001"


@pytest.mark.parametrize(
    "output",
    [
        "Umsatz 1.234,5 BRL",
        "Umsatz 1234,5",
        "Plus 12,2 %",  # fewer decimals, rounds to the source value
        "Vorperiode 1.100",
        "Stand 2026-10-01",
        "Rückgang −12,23 %",  # sign does not matter, direction is in the text
    ],
)
def test_supported_numbers_pass(output):
    assert unsupported_numbers(output, german(TOOL_RESULT)) == []


@pytest.mark.parametrize(
    ("output", "bad"),
    [
        ("Umsatz 1.300", ["1.300"]),
        ("Plus 12,5 %", ["12,5"]),
        ("Stand 2026-10-02", ["2026-10-02"]),
        ("Umsatz 12.5", ["12.5"]),  # decimal point is not German notation: reads as 125
    ],
)
def test_invented_numbers_fail(output, bad):
    assert unsupported_numbers(output, german(TOOL_RESULT)) == bad


def test_canonical_path_sorts_query():
    assert canonical_path("/api/v1/kpis/x/breakdown?grain=day&dim=region") == (
        "/api/v1/kpis/x/breakdown?dim=region&grain=day"
    )
    assert canonical_path("/api/v1/kpis") == "/api/v1/kpis"


def test_evidence_must_resolve():
    insight_id = "3f2b8c1e-0000-4000-8000-000000000001"
    paths = ["/api/v1/kpis/revenue/breakdown?dim=region&grain=day"]
    good = [
        {"kind": "query", "ref": "/api/v1/kpis/revenue/breakdown?grain=day&dim=region"},
        {"kind": "insight", "id": insight_id.upper()},
    ]
    bad = [
        {"kind": "query", "ref": "/api/v1/kpis/revenue/breakdown?dim=category&grain=day"},
        {"kind": "insight", "id": "3f2b8c1e-0000-4000-8000-000000000002"},
        {"kind": "chunk", "document_id": "d", "chunk_id": "c"},
    ]
    assert unresolved_evidence(good, paths, [insight_id]) == []
    assert unresolved_evidence(good + bad, paths, [insight_id]) == bad
