"""Provider layer with routing by data class, same rule as the BIS (`llm/provider.py` there).

Every call states the data class of its prompt (the highest class of its parts). A provider
refuses classes it is not cleared for before anything leaves the system: the Mistral free tier
gets `public` only, `confidential` never reaches an external provider."""

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar

import httpx
from pydantic import BaseModel

from cia import settings

T = TypeVar("T", bound=BaseModel)

DataClass = Literal["public", "internal", "confidential"]
DATA_CLASSES: tuple[DataClass, ...] = ("public", "internal", "confidential")


@dataclass(frozen=True)
class Usage:
    """Tokens and cost of one provider call; the caller records it on its LLM span."""

    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_eur: float


class LLMError(RuntimeError):
    """The provider could not produce a usable answer (auth, outage, rate limit, bad output)."""


class LLMRoutingError(PermissionError):
    """A prompt was about to reach a provider not cleared for its data class. Deliberately not an
    LLMError: callers must not treat it as a soft failure."""


def highest_data_class(classes: Iterable[str]) -> DataClass:
    found = list(classes)
    for c in found:
        if c not in DATA_CLASSES:
            raise LLMRoutingError(f"Unknown data class {c!r}")
    if not found:
        return "public"
    return max(found, key=DATA_CLASSES.index)  # type: ignore[arg-type,return-value]


class LLMProvider(Protocol):
    name: str
    model: str
    allowed_data_classes: frozenset[str]
    calls: list[Usage]

    def generate(self, system: str, user: str, schema: type[T], data_class: DataClass) -> T:
        """Return an instance of `schema` (structured output)."""
        ...


def check_route(provider: LLMProvider, data_class: str) -> None:
    if data_class not in DATA_CLASSES:
        raise LLMRoutingError(f"Unknown data class {data_class!r}")
    if data_class not in provider.allowed_data_classes:
        raise LLMRoutingError(
            f"Provider {provider.name!r} is not cleared for {data_class!r} data "
            f"(allowed: {', '.join(sorted(provider.allowed_data_classes))})"
        )


def _inline_refs(schema: dict) -> dict:
    """Pydantic puts nested models under $defs; resolve them so the provider sees one flat schema."""
    defs = schema.get("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].rsplit("/", 1)[-1]])
            return {k: resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)


class MistralProvider:
    """Mistral chat completions with JSON-schema output (EU provider)."""

    name = "mistral"
    URL = "https://api.mistral.ai/v1/chat/completions"
    MAX_ATTEMPTS = 4

    def __init__(
        self,
        model: str,
        api_key: str,
        allowed_data_classes: frozenset[str] = frozenset({"public"}),
        price_eur_per_mtok: tuple[float, float] = (0.0, 0.0),
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.model = model
        self._api_key = api_key
        self.allowed_data_classes = allowed_data_classes
        self._price_in, self._price_out = price_eur_per_mtok
        self.client = client or httpx.Client(timeout=120.0)
        self._sleep = sleep
        self.calls: list[Usage] = []

    def generate(self, system: str, user: str, schema: type[T], data_class: DataClass) -> T:
        check_route(self, data_class)  # before anything leaves the system
        if not self._api_key:
            raise LLMError("No Mistral API key configured (CIA_MISTRAL_API_KEY)")
        body = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": _inline_refs(schema.model_json_schema())},
            },
        }
        data = self._post(body).json()
        usage = data.get("usage") or {}
        tokens_in, tokens_out = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
        cost = (tokens_in * self._price_in + tokens_out * self._price_out) / 1_000_000
        self.calls.append(Usage(self.name, self.model, tokens_in, tokens_out, round(cost, 6)))
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("Unexpected Mistral response") from exc
        if choice.get("finish_reason") == "length":
            raise LLMError("No structured output (finish_reason=length)")
        try:
            return schema.model_validate_json(content)
        except ValueError as exc:
            raise LLMError("Mistral output does not match the schema") from exc

    def _post(self, body: dict) -> httpx.Response:
        """POST with retries on rate limits and server errors (Retry-After or exponential backoff)."""
        headers = {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"}
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                response = self.client.post(self.URL, json=body, headers=headers)
            except httpx.HTTPError as exc:
                if attempt == self.MAX_ATTEMPTS:
                    raise LLMError("Mistral API not reachable") from exc
                self._sleep(2**attempt)
                continue
            if response.status_code in (401, 403):
                raise LLMError("Mistral credentials missing or invalid")
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == self.MAX_ATTEMPTS:
                    raise LLMError(f"Mistral API error {response.status_code} after {attempt} attempts")
                retry_after = response.headers.get("Retry-After", "")
                self._sleep(float(retry_after) if retry_after.replace(".", "", 1).isdigit() else 2**attempt)
                continue
            if response.status_code >= 400:
                raise LLMError(f"Request rejected by Mistral ({response.status_code})")
            return response
        raise LLMError("Mistral API not reachable")  # pragma: no cover - loop always returns or raises


def get_provider(role: str) -> LLMProvider | None:
    """Provider for an agent role (`central`, `department`), or None with CIA_LLM_PROVIDER=none."""
    name = settings.llm_provider()
    if name == "none":
        return None
    if name == "mistral":
        return MistralProvider(
            model=settings.llm_model(role),
            api_key=settings.mistral_api_key(),
            allowed_data_classes=settings.mistral_data_classes(),
            price_eur_per_mtok=settings.llm_price_eur_per_mtok(),
        )
    raise ValueError(f"Unknown CIA_LLM_PROVIDER {name!r} (supported: mistral, none)")
