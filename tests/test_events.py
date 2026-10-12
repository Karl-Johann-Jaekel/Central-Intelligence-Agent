"""Event intake: signature, contract, idempotency (DoD C0)."""

import json

import pytest
from conftest import example
from fastapi.testclient import TestClient

from cia.api.events import signature
from cia.api.main import create_app
from cia.worker.main import process_once

SECRET = "test-secret"


def _body(payload: dict) -> bytes:
    # Same serialisation as the BIS WebhookSender.
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()


def _headers(event_id: str, event_type: str, body: bytes, secret: str = SECRET) -> dict:
    return {
        "Content-Type": "application/json",
        "X-BIS-Event-Id": event_id,
        "X-BIS-Event-Type": event_type,
        "X-BIS-Signature": signature(secret, body),
    }


@pytest.fixture
def client(inbox):
    return TestClient(create_app(inbox=inbox, secret=SECRET))


def _post(client, payload, event_id=None, event_type=None, **kwargs):
    body = _body(payload)
    event_id = event_id or payload.get("insight_id") or payload.get("snapshot_id")
    event_type = event_type or payload["schema_version"]
    return client.post("/events", content=body, headers=_headers(event_id, event_type, body, **kwargs))


@pytest.mark.parametrize("name", ["insight.v1.anomaly.json", "daily_snapshot.v1.example.json"])
def test_valid_event_is_stored(client, inbox, name):
    payload = example(name)
    response = _post(client, payload)
    assert response.status_code == 202
    assert response.json()["duplicate"] is False
    [stored] = inbox.events.values()
    assert stored.payload == payload
    assert stored.event_type == payload["schema_version"]


def test_duplicate_delivery_creates_one_entry(client, inbox):
    payload = example("insight.v1.anomaly.json")
    first = _post(client, payload)
    second = _post(client, payload)
    assert first.status_code == 202
    assert second.status_code == 200  # 2xx: the BIS stops retrying
    assert second.json()["duplicate"] is True
    assert len(inbox.events) == 1


def test_wrong_signature_is_rejected(client, inbox):
    response = _post(client, example("insight.v1.anomaly.json"), secret="other-secret")
    assert response.status_code == 401
    assert inbox.events == {}


def test_missing_signature_is_rejected(client, inbox):
    payload = example("insight.v1.anomaly.json")
    body = _body(payload)
    headers = _headers(payload["insight_id"], "insight.v1", body)
    del headers["X-BIS-Signature"]
    assert client.post("/events", content=body, headers=headers).status_code == 401
    assert inbox.events == {}


def test_tampered_body_is_rejected(client, inbox):
    payload = example("insight.v1.anomaly.json")
    body = _body(payload)
    headers = _headers(payload["insight_id"], "insight.v1", body)
    tampered = body.replace(b'"warning"', b'"critical"')
    assert client.post("/events", content=tampered, headers=headers).status_code == 401


def test_broken_payload_fails_contract(client, inbox):
    payload = example("insight.v1.anomaly.json")
    del payload["evidence"]
    response = _post(client, payload)
    assert response.status_code == 422
    assert response.json()["detail"]["contract"] == "insight.v1"
    assert inbox.events == {}


def test_unsupported_event_type_is_rejected(client, inbox):
    payload = example("finding.v1.supplier-outage.json")
    response = _post(client, payload, event_id="00000000-0000-4000-8000-000000000000")
    assert response.status_code == 422


def test_event_type_must_match_body(client, inbox):
    payload = example("insight.v1.anomaly.json")
    assert _post(client, payload, event_type="daily_snapshot.v1").status_code == 422


def test_event_id_must_match_body(client, inbox):
    payload = example("insight.v1.anomaly.json")
    response = _post(client, payload, event_id="00000000-0000-4000-8000-000000000000")
    assert response.status_code == 422
    assert inbox.events == {}


def test_missing_secret_disables_intake(inbox, monkeypatch):
    monkeypatch.delenv("CIA_WEBHOOK_SECRET", raising=False)
    client = TestClient(create_app(inbox=inbox))
    payload = example("insight.v1.anomaly.json")
    assert _post(client, payload).status_code == 503


def test_worker_marks_events_processed(client, inbox):
    _post(client, example("insight.v1.anomaly.json"))
    _post(client, example("daily_snapshot.v1.example.json"))
    assert process_once(inbox) == 2
    assert process_once(inbox) == 0


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "contracts": "contracts-v3-draft"}
