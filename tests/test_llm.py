"""LLM provider: routing by data class before any request, Mistral errors and retries."""

import json

import httpx
import pytest
from pydantic import BaseModel

from cia.llm import provider as llm
from cia.llm.provider import LLMError, LLMRoutingError, MistralProvider, highest_data_class


class Answer(BaseModel):
    text: str


def _mistral(handler, **kwargs) -> tuple[MistralProvider, list[float]]:
    slept: list[float] = []
    provider = MistralProvider(
        model="mistral-small-latest",
        api_key=kwargs.pop("api_key", "test-key"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=slept.append,
        **kwargs,
    )
    return provider, slept


def _ok(content: str, finish_reason: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30},
        },
    )


def _never(request: httpx.Request) -> httpx.Response:
    raise AssertionError("no request may leave the system")


@pytest.mark.parametrize("data_class", ["confidential", "internal"])
def test_free_tier_refuses_non_public_before_sending(data_class):
    provider, _ = _mistral(_never)
    with pytest.raises(LLMRoutingError):
        provider.generate("s", "u", Answer, data_class)


def test_confidential_is_refused_even_when_internal_is_cleared():
    provider, _ = _mistral(_never, allowed_data_classes=frozenset({"public", "internal"}))
    with pytest.raises(LLMRoutingError):
        provider.generate("s", "u", Answer, "confidential")


def test_routing_error_is_not_a_soft_llm_error():
    assert not issubclass(LLMRoutingError, LLMError)


def test_highest_data_class():
    assert highest_data_class([]) == "public"
    assert highest_data_class(["public", "internal", "public"]) == "internal"
    with pytest.raises(LLMRoutingError):
        highest_data_class(["secret"])


def test_structured_output_usage_and_cost():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["Authorization"]
        return _ok('{"text": "ok"}')

    provider, _ = _mistral(handler, price_eur_per_mtok=(1.0, 3.0))
    assert provider.generate("system", "user", Answer, "public") == Answer(text="ok")
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["response_format"]["json_schema"]["name"] == "Answer"
    [usage] = provider.calls
    assert (usage.tokens_in, usage.tokens_out) == (120, 30)
    assert usage.cost_eur == pytest.approx((120 * 1.0 + 30 * 3.0) / 1_000_000)


def test_retries_rate_limit_with_retry_after():
    responses = iter([httpx.Response(429, headers={"Retry-After": "1.5"}), _ok('{"text": "ok"}')])
    provider, slept = _mistral(lambda request: next(responses))
    assert provider.generate("s", "u", Answer, "public").text == "ok"
    assert slept == [1.5]


def test_gives_up_after_max_attempts():
    provider, slept = _mistral(lambda request: httpx.Response(503))
    with pytest.raises(LLMError, match="503"):
        provider.generate("s", "u", Answer, "public")
    assert len(slept) == MistralProvider.MAX_ATTEMPTS - 1


def test_bad_credentials_do_not_retry():
    provider, slept = _mistral(lambda request: httpx.Response(401))
    with pytest.raises(LLMError, match="credentials"):
        provider.generate("s", "u", Answer, "public")
    assert slept == []


def test_missing_key_fails_without_request():
    provider, _ = _mistral(_never, api_key="")
    with pytest.raises(LLMError, match="CIA_MISTRAL_API_KEY"):
        provider.generate("s", "u", Answer, "public")


@pytest.mark.parametrize(
    "response",
    [_ok('{"wrong": 1}'), _ok('{"text": "cut', finish_reason="length"), httpx.Response(200, json={})],
)
def test_unusable_output_is_an_llm_error(response):
    provider, _ = _mistral(lambda request: response)
    with pytest.raises(LLMError):
        provider.generate("s", "u", Answer, "public")


def test_get_provider_from_environment(monkeypatch):
    monkeypatch.setenv("CIA_LLM_PROVIDER", "mistral")
    monkeypatch.setenv("CIA_LLM_MODEL_CENTRAL", "mistral-large-latest")
    monkeypatch.delenv("CIA_MISTRAL_DATA_CLASSES", raising=False)
    central = llm.get_provider("central")
    assert central is not None
    assert central.model == "mistral-large-latest"
    assert central.allowed_data_classes == frozenset({"public"})
    assert llm.get_provider("department").model == "mistral-small-latest"  # type: ignore[union-attr]
    monkeypatch.setenv("CIA_LLM_PROVIDER", "none")
    assert llm.get_provider("central") is None
