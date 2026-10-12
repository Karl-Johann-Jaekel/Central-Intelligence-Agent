"""The CIA validates against the same contract version as the BIS (submodule vendor/bis)."""

import copy
import subprocess

import pytest
from conftest import EXAMPLES, example

from cia import contracts
from cia.contracts import CONTRACTS_DIR, CONTRACTS_TAG, ContractError

EXPECTED = {"insight.v1", "daily_snapshot.v1", "task.v1", "finding.v1", "action.v1", "agent_metrics.v1"}


def test_submodule_is_pinned_to_the_contracts_tag():
    tags = subprocess.run(
        ["git", "-C", str(CONTRACTS_DIR), "tag", "--points-at", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert CONTRACTS_TAG in tags


def test_all_contracts_are_available():
    assert EXPECTED <= set(contracts.names())


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.json")), ids=lambda p: p.name)
def test_bis_examples_validate(path):
    payload = example(path.name)
    assert contracts.errors(payload["schema_version"], payload) == []


def _broken() -> list[tuple[str, str, dict]]:
    insight = example("insight.v1.anomaly.json")
    snapshot = example("daily_snapshot.v1.example.json")
    cases = []

    missing = copy.deepcopy(insight)
    del missing["insight_id"]
    cases.append(("insight missing id", "insight.v1", missing))

    wrong_version = copy.deepcopy(insight)
    wrong_version["schema_version"] = "insight.v2"
    cases.append(("insight wrong version", "insight.v1", wrong_version))

    extra = copy.deepcopy(insight)
    extra["unexpected"] = True
    cases.append(("insight extra field", "insight.v1", extra))

    bad_severity = copy.deepcopy(insight)
    bad_severity["severity"] = "urgent"
    cases.append(("insight unknown severity", "insight.v1", bad_severity))

    bad_department = copy.deepcopy(snapshot)
    bad_department["departments"][0]["department"] = "legal"
    cases.append(("snapshot unknown department", "daily_snapshot.v1", bad_department))

    no_departments = copy.deepcopy(snapshot)
    no_departments["departments"] = []
    cases.append(("snapshot without departments", "daily_snapshot.v1", no_departments))
    return cases


@pytest.mark.parametrize(("contract", "payload"), [c[1:] for c in _broken()], ids=[c[0] for c in _broken()])
def test_broken_payloads_fail(contract, payload):
    assert contracts.errors(contract, payload)
    with pytest.raises(ContractError):
        contracts.validate(contract, payload)
