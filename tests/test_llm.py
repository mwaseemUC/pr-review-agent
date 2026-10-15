from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from pr_review_agent.context import REVIEW_TOOL_NAME
from pr_review_agent.llm import (
    DEFAULT_MODEL,
    PRICING,
    AnthropicLLM,
    FakeLLM,
    LLMError,
    compute_cost,
    resolve_model,
    review_tool,
)
from pr_review_agent.models import ReviewResult, Usage

VALID_INPUT = {
    "summary": "Changes indexing.",
    "findings": [
        {
            "file": "app.py",
            "line": 2,
            "severity": "high",
            "category": "off-by-one",
            "issue": "Returns the second element instead of the first.",
            "suggested_fix": "    return xs[0]",
            "confidence": 0.9,
        }
    ],
}


def tool_block(data: dict[str, Any], block_id: str = "toolu_1") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=REVIEW_TOOL_NAME, id=block_id, input=data)


def response(
    content: list[Any],
    stop_reason: str = "tool_use",
    model: str = DEFAULT_MODEL,
    input_tokens: int = 1000,
    output_tokens: int = 200,
) -> SimpleNamespace:
    usage = SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )
    return SimpleNamespace(content=content, stop_reason=stop_reason, model=model, usage=usage)


class FakeClient:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make_llm(outcomes: list[Any], **kwargs: Any) -> tuple[AnthropicLLM, FakeClient, list[float]]:
    client = FakeClient(outcomes)
    sleeps: list[float] = []
    llm = AnthropicLLM(DEFAULT_MODEL, client=client, sleep=sleeps.append, **kwargs)
    return llm, client, sleeps


def _request() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def status_error(cls: type[anthropic.APIStatusError], code: int) -> anthropic.APIStatusError:
    return cls("boom", response=httpx2.Response(code, request=_request()), body=None)


def test_pricing_has_default_model_and_cost_math() -> None:
    assert DEFAULT_MODEL in PRICING
    assert compute_cost("claude-sonnet-5-5", 1_000_000, 1_000_000) == pytest.approx(12.0)
    cached = compute_cost("claude-sonnet-5-5", 0, 0, cache_read_tokens=1_000_000)
    assert cached == pytest.approx(0.2)
    assert compute_cost("unknown-model", 100, 100) == 0.0


def test_resolve_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PR_REVIEW_MODEL", "")
    assert resolve_model() == DEFAULT_MODEL
    monkeypatch.setenv("PR_REVIEW_MODEL", "claude-opus-5-5")
    assert resolve_model() == "claude-opus-5-5"
    assert resolve_model("claude-haiku-5-5") == "claude-haiku-5-5"


def test_review_tool_schema_is_strict_compatible() -> None:
    tool = review_tool()
    assert tool["strict"] is True
    schema = tool["input_schema"]
    finding = schema["properties"]["findings"]["items"]
    assert schema["additionalProperties"] is False and finding["additionalProperties"] is False
    assert set(finding["required"]) == set(finding["properties"])
    text = str(tool)
    for unsupported in ("minimum", "maximum", "minLength", "maxLength"):
        assert unsupported not in text


def test_review_success_parses_and_records_usage() -> None:
    llm, client, _ = make_llm([response([tool_block(VALID_INPUT)])])
    result, usage = llm.review("system", "user")
    assert result.findings[0].category == "off-by-one"
    assert usage.input_tokens == 1000 and usage.output_tokens == 200
    assert usage.cost_usd == pytest.approx(compute_cost(DEFAULT_MODEL, 1000, 200))
    assert usage.latency_s >= 0
    call = client.calls[0]
    assert call["model"] == DEFAULT_MODEL
    assert call["tool_choice"]["type"] == "auto"
    assert call["tools"][0]["name"] == REVIEW_TOOL_NAME
    assert call["system"][0]["text"] == "system"
    assert call["messages"] == [{"role": "user", "content": "user"}]
    assert call["fallbacks"] == "default"


def test_retries_once_with_validation_error() -> None:
    bad = {**VALID_INPUT, "findings": [{**VALID_INPUT["findings"][0], "line": 0}]}
    llm, client, _ = make_llm(
        [response([tool_block(bad, "toolu_bad")]), response([tool_block(VALID_INPUT)])]
    )
    result, usage = llm.review("s", "u")
    assert len(result.findings) == 1
    assert usage.input_tokens == 2000
    retry_messages = client.calls[1]["messages"]
    assert retry_messages[1]["role"] == "assistant"
    tool_result = retry_messages[2]["content"][0]
    assert tool_result["type"] == "tool_result" and tool_result["tool_use_id"] == "toolu_bad"
    assert tool_result["is_error"] is True
    assert "line" in tool_result["content"]


def test_retries_when_no_tool_call_then_fails() -> None:
    text_only = response([SimpleNamespace(type="text", text="LGTM")], stop_reason="end_turn")
    llm, client, _ = make_llm([text_only, text_only])
    with pytest.raises(LLMError, match="No valid review"):
        llm.review("s", "u")
    assert len(client.calls) == 2
    assert REVIEW_TOOL_NAME in client.calls[1]["messages"][2]["content"]


def test_refusal_raises_without_retry() -> None:
    llm, client, _ = make_llm([response([], stop_reason="refusal")])
    with pytest.raises(LLMError, match="refusal"):
        llm.review("s", "u")
    assert len(client.calls) == 1


def test_api_errors_retry_with_backoff() -> None:
    outcomes = [
        status_error(anthropic.RateLimitError, 429),
        anthropic.APIConnectionError(request=_request()),
        response([tool_block(VALID_INPUT)]),
    ]
    llm, client, sleeps = make_llm(outcomes, backoff_base_s=1.0)
    result, _ = llm.review("s", "u")
    assert result.summary == "Changes indexing."
    assert len(client.calls) == 3
    assert len(sleeps) == 2 and 1.0 <= sleeps[0] < 2.0 and 2.0 <= sleeps[1] < 3.0


def test_api_errors_bounded() -> None:
    errors = [status_error(anthropic.InternalServerError, 500) for _ in range(3)]
    llm, client, sleeps = make_llm(errors, max_api_retries=2)
    with pytest.raises(LLMError, match="after 3 attempts"):
        llm.review("s", "u")
    assert len(sleeps) == 2


def test_client_errors_not_retried() -> None:
    llm, client, sleeps = make_llm([status_error(anthropic.BadRequestError, 400)])
    with pytest.raises(LLMError, match="400"):
        llm.review("s", "u")
    assert len(client.calls) == 1 and sleeps == []


def test_fallback_model_priced_by_served_model() -> None:
    served = response([tool_block(VALID_INPUT)], model="claude-opus-5-5")
    llm, _, _ = make_llm([served])
    _, usage = llm.review("s", "u")
    assert usage.cost_usd == pytest.approx(compute_cost("claude-opus-5-5", 1000, 200))


def test_fake_llm_records_calls() -> None:
    canned = ReviewResult.model_validate(VALID_INPUT)
    fake = FakeLLM(canned, Usage(input_tokens=5))
    result, usage = fake.review("sys", "usr")
    assert result is canned and usage.input_tokens == 5
    assert fake.calls == [("sys", "usr")]
    assert FakeLLM().review("a", "b")[0].findings == []
