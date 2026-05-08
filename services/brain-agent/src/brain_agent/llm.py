"""LLM client wrapper.

Two implementations:
  - `AnthropicLLMClient` — production, calls the Anthropic API. Uses
    prompt caching (system prompt is large + reused across requests).
  - `StubLLMClient` — dev / test, returns a deterministic structured
    report so the rest of the pipeline can be exercised without an API
    key or network.

The interface is `complete(*, system, user) -> str`. Callers parse the
result into `InvestigationReport`. Parse failures count as low-
confidence reports (the agent does not retry; the analyst reruns).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Protocol

from fraudnet.obs import counter, get_logger, histogram

_log = get_logger("brain_agent.llm")

_LLM_REQUESTS = counter(
    "brain_agent_llm_requests_total",
    "LLM requests issued by brain-agent.",
    labelnames=("model", "outcome"),
)
_LLM_DURATION = histogram(
    "brain_agent_llm_request_seconds",
    "LLM request latency.",
    labelnames=("model",),
)
_LLM_TOKENS = counter(
    "brain_agent_llm_tokens_total",
    "LLM token consumption.",
    labelnames=("model", "kind"),  # kind: input | output | cache_hit
)


@dataclass(frozen=True)
class LLMResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0


class LLMClient(Protocol):
    async def complete(self, *, system: str, user: str) -> LLMResponse: ...


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


class AnthropicLLMClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "claude-opus-4-7",
        max_tokens: int = 4096,
        timeout_s: float = 60.0,
    ) -> None:
        # Imported lazily so the dev path doesn't pull anthropic when
        # the stub is in use.
        from anthropic import AsyncAnthropic  # type: ignore[import-not-found]

        self._client = AsyncAnthropic(api_key=api_key, timeout=timeout_s)
        self._model = model
        self._max_tokens = max_tokens

    async def complete(self, *, system: str, user: str) -> LLMResponse:
        # Cache the system prompt — it's large and identical across
        # investigations. cache_control=ephemeral is the API form.
        with _LLM_DURATION.labels(model=self._model).time():
            try:
                resp = await self._client.messages.create(
                    model=self._model,
                    max_tokens=self._max_tokens,
                    system=[
                        {
                            "type": "text",
                            "text": system,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=[{"role": "user", "content": user}],
                )
            except Exception:  # noqa: BLE001
                _LLM_REQUESTS.labels(model=self._model, outcome="error").inc()
                raise

        # Defensive — Anthropic returns content blocks; we want the text.
        parts = [
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        ]
        text = "".join(parts).strip()
        usage = getattr(resp, "usage", None)
        in_tok = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
        out_tok = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0
        cache_create = int(getattr(usage, "cache_creation_input_tokens", 0) or 0) if usage else 0
        cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0) if usage else 0

        _LLM_REQUESTS.labels(model=self._model, outcome="ok").inc()
        _LLM_TOKENS.labels(model=self._model, kind="input").inc(in_tok)
        _LLM_TOKENS.labels(model=self._model, kind="output").inc(out_tok)
        if cache_read:
            _LLM_TOKENS.labels(model=self._model, kind="cache_hit").inc(cache_read)
        return LLMResponse(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_creation_tokens=cache_create,
            cache_read_tokens=cache_read,
        )


# ---------------------------------------------------------------------------
# Stub (dev / test)
# ---------------------------------------------------------------------------


class StubLLMClient:
    """Deterministic report for any input. Used in dev and tests.

    Returns a valid `InvestigationReport` JSON shape so the rest of the
    pipeline (parse, persist, audit) is exercised end-to-end without
    network or API key.
    """

    def __init__(self, *, model: str = "stub-llm") -> None:
        self._model = model

    async def complete(self, *, system: str, user: str) -> LLMResponse:
        # Pretend to do work so timing tests are non-zero.
        await asyncio.sleep(0)
        # The stub does not parse the user message; it returns a generic
        # low-confidence report. Tests that need richer behaviour use
        # the `RecordingStubLLMClient` below.
        report = {
            "summary": "Stub investigation report — no real LLM was called.",
            "risk_assessment": (
                "The dev/test stub returns a fixed shape so the rest of the "
                "pipeline can be exercised without an API key."
            ),
            "key_findings": ["stub: no findings"],
            "evidence_chain": [],
            "recommended_actions": [],
            "data_gaps": [
                "stub: real LLM analysis was not performed; deploy with "
                "ANTHROPIC_API_KEY for live investigation"
            ],
            "confidence": "low",
            "confidence_rationale": "Stub LLM client; no real reasoning applied.",
        }
        text = json.dumps(report)
        _LLM_REQUESTS.labels(model=self._model, outcome="ok").inc()
        return LLMResponse(text=text, input_tokens=0, output_tokens=0)


class RecordingStubLLMClient:
    """Test helper: records every call and lets the test inject a response."""

    def __init__(self, response_text: str = "") -> None:
        self.response_text = response_text
        self.calls: list[tuple[str, str]] = []

    async def complete(self, *, system: str, user: str) -> LLMResponse:
        self.calls.append((system, user))
        return LLMResponse(text=self.response_text, input_tokens=10, output_tokens=20)
