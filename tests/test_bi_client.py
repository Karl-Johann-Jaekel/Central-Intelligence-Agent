"""BIS API client: reproducible evidence paths, errors, client credentials."""

from datetime import date

import httpx
import pytest

from cia import bi_client, settings
from cia.bi_client import BIClient, BIError, ClientCredentials


def _client(handler, auth=None) -> BIClient:
    transport = httpx.MockTransport(handler)
    return BIClient("http://bis", client=httpx.Client(base_url="http://bis", transport=transport, auth=auth))


def test_paths_are_sorted_and_match_the_request():
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url).removeprefix("http://bis"))
        return httpx.Response(200, json={"rows": []})

    client = _client(handler)
    result = client.breakdown("revenue", start=date(2026, 10, 1), end=date(2026, 10, 7), dim="region")
    assert result.tool == "bi.breakdown"
    assert result.path == "/api/v1/kpis/revenue/breakdown?dim=region&from=2026-10-01&to=2026-10-07"
    assert requested == [result.path]
    assert result.data == {"rows": []}

    insights = client.insights(types=["anomaly", "briefing"], severity="warning")
    assert insights.path == "/api/v1/insights?severity=warning&type=anomaly&type=briefing"


def test_invalid_kpi_key_never_reaches_the_api():
    client = _client(lambda request: pytest.fail("request sent"))
    with pytest.raises(BIError, match="Invalid KPI key"):
        client.series("../health")


def test_health_503_is_an_answer():
    client = _client(lambda request: httpx.Response(503, json={"status": "down"}))
    assert client.health().data == {"status": "down"}


def test_errors_carry_the_status():
    client = _client(lambda request: httpx.Response(404, json={"detail": "Unknown KPI"}))
    with pytest.raises(BIError) as caught:
        client.series("nope")
    assert caught.value.status_code == 404


def test_unreachable_api():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(BIError, match="not reachable"):
        _client(handler).kpis()


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _token_server(tokens: list[str]):
    issued = iter(tokens)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"access_token": next(issued), "expires_in": 300})

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def test_client_credentials_cache_and_refresh():
    token_client, token_calls = _token_server(["t1", "t2", "t3"])
    clock = _Clock()
    auth = ClientCredentials("http://kc/token", "cia-agent", "s3cret", client=token_client, clock=clock)
    seen = []

    def api(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Authorization"])
        return httpx.Response(401 if request.headers["Authorization"] == "Bearer t2" else 200, json=[])

    client = _client(api, auth=auth)
    client.kpis()
    client.kpis()
    assert seen == ["Bearer t1", "Bearer t1"]  # cached
    clock.now += 300  # inside the safety margin -> new token
    client.kpis()  # t2 is rejected once, t3 is fetched and accepted
    assert seen[2:] == ["Bearer t2", "Bearer t3"]
    assert len(token_calls) == 3
    assert token_calls[0].content == b"grant_type=client_credentials"
    assert token_calls[0].headers["Authorization"].startswith("Basic ")


def test_token_failure_is_a_bi_error():
    token_client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(401)))
    auth = ClientCredentials("http://kc/token", "cia-agent", "wrong", client=token_client)
    with pytest.raises(BIError, match="Token request failed"):
        _client(lambda request: httpx.Response(200, json=[]), auth=auth).kpis()


def test_credentials_settings(monkeypatch):
    monkeypatch.delenv("CIA_OIDC_TOKEN_URL", raising=False)
    assert settings.bis_client_credentials() is None
    monkeypatch.setenv("CIA_OIDC_TOKEN_URL", "http://kc/token")
    monkeypatch.delenv("CIA_AGENT_CLIENT_SECRET", raising=False)
    with pytest.raises(settings.ConfigError):
        bi_client.get_client()
