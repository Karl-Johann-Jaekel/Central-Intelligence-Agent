"""Read-only client for the BIS API: the `bi.*` tools of the agents (plan section 5.3).

Each call returns a ToolResult with the exact API path it requested (query parameters sorted),
so a statement can cite it as evidence (`{"kind": "query", "ref": path}`) and the guardrail can
check that the run really made this request. No other access to BIS data exists (no warehouse)."""

import re
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal
from urllib.parse import urlencode

import httpx

from cia import settings

Grain = Literal["day", "month"]
Severity = Literal["info", "warning", "critical"]
# KPI keys end up in the URL path and may come from model output: registry pattern only.
KPI_KEY = re.compile(r"^[a-z][a-z0-9_]*$")


class BIError(RuntimeError):
    """The BIS API did not answer usefully (unreachable, auth, 4xx/5xx)."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ToolResult:
    tool: str  # e.g. bi.breakdown
    path: str  # /api/v1/... with sorted query, usable as evidence ref
    data: Any


class ClientCredentials(httpx.Auth):
    """OAuth2 client credentials (Keycloak client `cia-agent`), token cached until shortly before
    expiry and fetched again once on a 401 (key rotation, revoked session)."""

    MARGIN_SECONDS = 30

    def __init__(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.token_url, self.client_id, self._secret = token_url, client_id, client_secret
        self._client = client or httpx.Client(timeout=10.0)
        self._clock = clock
        self._token: str | None = None
        self._expires_at = 0.0

    def _fetch(self) -> str:
        try:
            response = self._client.post(
                self.token_url,
                data={"grant_type": "client_credentials"},
                auth=(self.client_id, self._secret),
            )
        except httpx.HTTPError as exc:
            raise BIError("Token endpoint not reachable") from exc
        if response.status_code != 200:
            raise BIError(f"Token request failed ({response.status_code})", response.status_code)
        body = response.json()
        self._token = body["access_token"]
        self._expires_at = self._clock() + float(body.get("expires_in", 60)) - self.MARGIN_SECONDS
        return self._token

    def token(self) -> str:
        if self._token is None or self._clock() >= self._expires_at:
            return self._fetch()
        return self._token

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response]:
        request.headers["Authorization"] = f"Bearer {self.token()}"
        response = yield request
        if response.status_code == 401:
            request.headers["Authorization"] = f"Bearer {self._fetch()}"
            yield request


def _kpi_path(key: str, suffix: str) -> str:
    if not KPI_KEY.fullmatch(key):
        raise BIError(f"Invalid KPI key {key!r}")
    return f"/api/v1/kpis/{key}/{suffix}"


def _path(path: str, params: dict[str, Any]) -> str:
    """Path with sorted, non-empty query parameters; lists become repeated parameters."""
    items: list[tuple[str, str]] = []
    for key, value in params.items():
        if value is None:
            continue
        for v in value if isinstance(value, list | tuple) else [value]:
            items.append((key, v.isoformat() if isinstance(v, date) else str(v)))
    query = urlencode(sorted(items))
    return f"{path}?{query}" if query else path


class BIClient:
    def __init__(self, base_url: str, auth: httpx.Auth | None = None, client: httpx.Client | None = None):
        self._client = client or httpx.Client(base_url=base_url, auth=auth, timeout=30.0)

    def _get(self, tool: str, path: str, params: dict[str, Any] | None = None, ok=(200,)) -> ToolResult:
        full = _path(path, params or {})
        try:
            response = self._client.get(full)
        except httpx.HTTPError as exc:
            raise BIError(f"BIS API not reachable ({tool})") from exc
        if response.status_code not in ok:
            detail = response.text[:200]
            raise BIError(f"{tool}: BIS API answered {response.status_code}: {detail}", response.status_code)
        return ToolResult(tool=tool, path=full, data=response.json())

    def health(self) -> ToolResult:
        """Pipeline status and sim_date; 503 means no data yet and is a valid answer."""
        return self._get("bi.health", "/api/v1/health", ok=(200, 503))

    def kpis(self) -> ToolResult:
        return self._get("bi.kpis", "/api/v1/kpis")

    def series(
        self,
        key: str,
        start: date | None = None,
        end: date | None = None,
        grain: Grain | None = None,
        dim: str | None = None,
        values: list[str] | None = None,
        top: int | None = None,
    ) -> ToolResult:
        params = {"from": start, "to": end, "grain": grain, "dim": dim, "value": values, "top": top}
        return self._get("bi.series", _kpi_path(key, "series"), params)

    def breakdown(
        self,
        key: str,
        start: date | None = None,
        end: date | None = None,
        grain: Grain | None = None,
        dim: str | None = None,
    ) -> ToolResult:
        params = {"from": start, "to": end, "grain": grain, "dim": dim}
        return self._get("bi.breakdown", _kpi_path(key, "breakdown"), params)

    def insights(
        self,
        since: date | None = None,
        types: list[str] | None = None,
        severity: Severity | None = None,
        kpi: str | None = None,
        limit: int | None = None,
    ) -> ToolResult:
        params = {"since": since, "type": types, "severity": severity, "kpi": kpi, "limit": limit}
        return self._get("bi.insights", "/api/v1/insights", params)


def get_client() -> BIClient:
    credentials = settings.bis_client_credentials()
    auth = ClientCredentials(*credentials) if credentials else None
    return BIClient(settings.bis_api_url(), auth=auth)
