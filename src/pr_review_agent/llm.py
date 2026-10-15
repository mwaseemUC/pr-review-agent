from __future__ import annotations

import logging
import os
import random
import time
from collections.abc import Callable
from typing import Any, Protocol

import anthropic
from pydantic import ValidationError

from pr_review_agent.context import REVIEW_TOOL_NAME
from pr_review_agent.models import ReviewResult, Severity, Usage

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5-5"
MODEL_ENV_VAR = "PR_REVIEW_MODEL"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# USD per million tokens: (input, output). Anthropic first-party API rates.
PRICING: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.00, 50.00),
    "claude-fable-5": (10.00, 50.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-5-5": (0.10, 0.50),  # prompts up to 100K tokens
    "claude-haiku-4-5": (1.00, 5.00),
}
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10

_RETRYABLE_ERRORS = (
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    anthropic.APIConnectionError,
)


class LLMError(RuntimeError):
    pass


class LLMClient(Protocol):
    def review(self, system: str, user: str) -> tuple[ReviewResult, Usage]: ...


def resolve_model(model: str | None = None) -> str:
    return model or os.environ.get(MODEL_ENV_VAR) or DEFAULT_MODEL


def compute_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    prices = PRICING.get(model)
    if prices is None:
        logger.warning("No pricing for model %s; cost reported as 0", model)
        return 0.0
    input_price, output_price = prices
    input_cost = (
        input_tokens
        + cache_write_tokens * CACHE_WRITE_MULTIPLIER
        + cache_read_tokens * CACHE_READ_MULTIPLIER
    ) * input_price
    return (input_cost + output_tokens * output_price) / 1_000_000


def review_tool() -> dict[str, Any]:
    finding = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "file",
            "line",
            "severity",
            "category",
            "issue",
            "suggested_fix",
            "confidence",
        ],
        "properties": {
            "file": {"type": "string", "description": "File path exactly as in the diff header"},
            "line": {
                "type": "integer",
                "description": "1-based NEW-side line number as printed in the diff",
            },
            "severity": {"type": "string", "enum": [s.value for s in Severity]},
            "category": {"type": "string", "description": "Short kebab-case bug class"},
            "issue": {"type": "string", "description": "What is wrong and why it matters"},
            "suggested_fix": {"type": "string", "description": "Concrete fix or replacement code"},
            "confidence": {
                "type": "number",
                "description": "Probability 0.0-1.0 that this is a real defect",
            },
        },
    }
    return {
        "name": REVIEW_TOOL_NAME,
        "description": "Submit the code review: a short summary and the list of defects found.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["summary", "findings"],
            "properties": {
                "summary": {"type": "string"},
                "findings": {"type": "array", "items": finding},
            },
        },
    }


def _usage_from_response(response: Any, requested_model: str) -> Usage:
    usage = response.usage
    cache_write = getattr(usage, "cache_creation_input_tokens", None) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", None) or 0
    served_model = getattr(response, "model", None) or requested_model
    pricing_model = served_model if served_model in PRICING else requested_model
    return Usage(
        served_models=[served_model],
        input_tokens=usage.input_tokens + cache_write + cache_read,
        output_tokens=usage.output_tokens,
        cost_usd=compute_cost(
            pricing_model,
            usage.input_tokens,
            usage.output_tokens,
            cache_write_tokens=cache_write,
            cache_read_tokens=cache_read,
        ),
    )


def _find_tool_use(response: Any) -> Any | None:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == REVIEW_TOOL_NAME:
            return block
    return None


class AnthropicLLM:
    def __init__(
        self,
        model: str | None = None,
        *,
        client: Any | None = None,
        api_key: str | None = None,
        max_tokens: int = 16_000,
        effort: str | None = "high",
        use_fallbacks: bool = True,
        max_api_retries: int = 3,
        backoff_base_s: float = 2.0,
        timeout_s: float = 300.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.model = resolve_model(model)
        self._client = client or anthropic.Anthropic(
            api_key=api_key, max_retries=0, timeout=timeout_s
        )
        self.max_tokens = max_tokens
        self.effort = effort
        self.use_fallbacks = use_fallbacks
        self.max_api_retries = max_api_retries
        self.backoff_base_s = backoff_base_s
        self._sleep = sleep

    def _request_kwargs(self, system: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "tools": [review_tool()],
            "tool_choice": {"type": "auto", "disable_parallel_tool_use": True},
            "messages": messages,
        }
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        if self.use_fallbacks:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    def _create(self, **kwargs: Any) -> Any:
        attempt = 0
        while True:
            try:
                return self._client.beta.messages.create(**kwargs)
            except _RETRYABLE_ERRORS as exc:
                if attempt >= self.max_api_retries:
                    raise LLMError(f"Anthropic API failed after {attempt + 1} attempts") from exc
                delay = self.backoff_base_s * (2**attempt) + random.uniform(0, 1)
                logger.warning(
                    "Anthropic API error (%s); retry %d/%d in %.1fs",
                    type(exc).__name__,
                    attempt + 1,
                    self.max_api_retries,
                    delay,
                )
                self._sleep(delay)
                attempt += 1
            except anthropic.APIStatusError as exc:
                raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc

    def review(self, system: str, user: str) -> tuple[ReviewResult, Usage]:
        start = time.perf_counter()
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        total = Usage()
        last_error = ""
        for attempt in range(2):
            response = self._create(**self._request_kwargs(system, messages))
            total = total + _usage_from_response(response, self.model)
            if response.stop_reason == "refusal":
                raise LLMError("Model declined to review this diff (stop_reason=refusal)")
            block = _find_tool_use(response)
            if block is None:
                last_error = f"You must call the `{REVIEW_TOOL_NAME}` tool; no call was made."
            elif response.stop_reason == "max_tokens":
                last_error = "Output was truncated at max_tokens; report fewer, terser findings."
            else:
                try:
                    result = ReviewResult.model_validate(block.input)
                except ValidationError as exc:
                    last_error = f"Tool input failed validation:\n{exc}"
                else:
                    total.latency_s = time.perf_counter() - start
                    return result, total
            if attempt == 0:
                logger.warning("Invalid structured output, retrying once")
                messages = [*messages, *_retry_turns(response.content, block, last_error)]
        raise LLMError(f"No valid review after retry: {last_error}")


def _retry_turns(content: Any, block: Any | None, error: str) -> list[dict[str, Any]]:
    fix = f"{error}\nCall `{REVIEW_TOOL_NAME}` again with corrected input."
    if block is None:
        reply: Any = fix
    else:
        reply = [{"type": "tool_result", "tool_use_id": block.id, "is_error": True, "content": fix}]
    return [{"role": "assistant", "content": content}, {"role": "user", "content": reply}]


class FakeLLM:
    def __init__(
        self,
        result: ReviewResult | None = None,
        usage: Usage | None = None,
        responder: Callable[[str, str], ReviewResult] | None = None,
    ) -> None:
        self.result = result or ReviewResult(summary="No issues found.", findings=[])
        self.usage = usage or Usage()
        self.responder = responder
        self.calls: list[tuple[str, str]] = []

    def review(self, system: str, user: str) -> tuple[ReviewResult, Usage]:
        self.calls.append((system, user))
        result = self.responder(system, user) if self.responder else self.result
        return result, self.usage.model_copy()
