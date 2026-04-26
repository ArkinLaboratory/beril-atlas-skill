"""
LLM client abstraction for the BERIL Atlas — multi-provider, JSON-aware.

Provides a uniform `LLMClient` Protocol so extractors don't care which
provider is active. Phase 2b ships:
  - CBORGClient — OpenAI-compatible API at https://api.cborg.lbl.gov/v1
                   (per memory: Claude-via-CBORG returns fenced JSON even
                   with response_format set; the JSON parser handles both)
  - AnthropicClient — direct Anthropic API (stub; wire up if ACTIVE_PROVIDER=anthropic)
  - GoogleClient — direct Gemini API (stub; wire up if ACTIVE_PROVIDER=google)
  - MockLLMClient — deterministic test double; never hits the network

All clients return ChatResponse with the same shape. JSON-mode requests
go through `chat_json()` which extracts JSON whether the model returned
fenced or raw.

Failure model: clients raise LLMClientError on transport errors,
LLMRateLimitError on 429s, LLMValidationError on response shape issues.
Caller decides retry / give-up policy.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from . import llm_config


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------

class LLMClientError(Exception):
    """Base for all LLM client errors."""


class LLMRateLimitError(LLMClientError):
    """Provider returned a 429 / quota exhausted."""


class LLMValidationError(LLMClientError):
    """Response didn't match expected shape (e.g., JSON parse failure in chat_json)."""


# --------------------------------------------------------------------------
# Response model
# --------------------------------------------------------------------------

@dataclass
class ChatResponse:
    """Provider-agnostic chat completion result."""

    content: str
    model_id: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    finish_reason: Optional[str] = None
    raw: Any = field(default=None, repr=False)  # original provider response, for debugging


# --------------------------------------------------------------------------
# Protocol
# --------------------------------------------------------------------------

class LLMClient(Protocol):
    """Provider-agnostic chat client."""

    def chat(self, messages: list[dict], *,
             model: Optional[str] = None,
             max_tokens: Optional[int] = None,
             temperature: Optional[float] = None,
             response_format: Optional[str] = None) -> ChatResponse:
        ...


# --------------------------------------------------------------------------
# JSON extraction helper — tolerates fenced-JSON responses
# --------------------------------------------------------------------------

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)


def extract_json(text: str) -> Any:
    """Extract JSON from an LLM response that may include markdown fences.

    Handles:
      - Raw JSON: `{"x": 1}`
      - Fenced:   ```json\n{"x": 1}\n```
      - Fenced no language: ```\n{"x": 1}\n```
      - Object or array at top level

    Raises LLMValidationError if no parseable JSON found.
    """
    text = text.strip()

    # 1. Try raw parse first (cheap)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Try fenced extraction
    m = _JSON_FENCE_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 3. Last resort: find first { ... } or [ ... ] balanced span
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if 0 <= start < end:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue

    # 4. Permissive fallback via json5 — handles trailing commas, single
    # quotes, unquoted keys, and a handful of other Claude-via-CBORG quirks.
    # Notably does NOT recover from unescaped double-quote chars inside
    # string values; that ambiguity is fundamental and only the prompt can
    # prevent it (see prompts/extract_universal.v1.md rule 11). json5 is a
    # last-resort attempt before giving up.
    try:
        import json5  # noqa: I001
    except ImportError:
        json5 = None  # type: ignore
    if json5 is not None:
        # Try the full text and the bracket-balanced slices in turn.
        candidates = [text]
        for opener, closer in (("{", "}"), ("[", "]")):
            start = text.find(opener)
            end = text.rfind(closer)
            if 0 <= start < end:
                candidates.append(text[start:end + 1])
        for cand in candidates:
            try:
                return json5.loads(cand)
            except (ValueError, Exception):  # json5 raises ValueError + custom
                continue

    raise LLMValidationError(f"No parseable JSON in response: {text[:200]!r}")


# --------------------------------------------------------------------------
# CBORG client (OpenAI-compatible)
# --------------------------------------------------------------------------

class CBORGClient:
    """Client for CBORG's OpenAI-compatible gateway.

    Ships with the openai SDK. Per memory `reference_jsonmode_cborg_claude`:
    Claude-via-CBORG ignores response_format=json_object and returns fenced
    JSON; the chat_json() helper handles this transparently.
    """

    def __init__(self, config: llm_config.LLMConfig):
        if config.provider != llm_config.PROVIDER_CBORG:
            raise ValueError(f"CBORGClient requires provider=cborg, got {config.provider}")
        try:
            from openai import OpenAI
        except ImportError as e:
            raise LLMClientError(
                "openai package required for CBORGClient: pip install openai"
            ) from e
        self.config = config
        self.client = OpenAI(api_key=config.api_key, base_url=config.base_url)

    def chat(self, messages: list[dict], *,
             model: Optional[str] = None,
             max_tokens: Optional[int] = None,
             temperature: Optional[float] = None,
             response_format: Optional[str] = None) -> ChatResponse:
        kwargs = {
            "model": model or self.config.default_model,
            "messages": messages,
            "max_tokens": max_tokens or self.config.default_max_tokens,
            "temperature": temperature if temperature is not None else self.config.default_temperature,
        }
        if response_format == "json":
            # CBORG honors this for OpenAI; ignored for Claude (we still try)
            kwargs["response_format"] = {"type": "json_object"}

        try:
            resp = self.client.chat.completions.create(**kwargs)
        except Exception as e:
            # Detect rate-limit-shape errors generically
            msg = str(e).lower()
            if "rate" in msg or "429" in msg or "quota" in msg:
                raise LLMRateLimitError(str(e)) from e
            raise LLMClientError(f"CBORG chat failed: {e}") from e

        choice = resp.choices[0]
        usage = resp.usage
        return ChatResponse(
            content=choice.message.content or "",
            model_id=resp.model or kwargs["model"],
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            finish_reason=choice.finish_reason,
            raw=resp,
        )


# --------------------------------------------------------------------------
# Anthropic client — stub (wire up when needed)
# --------------------------------------------------------------------------

class AnthropicClient:
    """Direct Anthropic API client. Stub until ACTIVE_PROVIDER=anthropic is set."""

    def __init__(self, config: llm_config.LLMConfig):
        if config.provider != llm_config.PROVIDER_ANTHROPIC:
            raise ValueError(f"AnthropicClient requires provider=anthropic, got {config.provider}")
        self.config = config

    def chat(self, messages, *, model=None, max_tokens=None, temperature=None, response_format=None):
        raise NotImplementedError(
            "AnthropicClient not yet implemented. Set ACTIVE_PROVIDER=cborg or "
            "implement the Anthropic SDK adapter when ready."
        )


# --------------------------------------------------------------------------
# Google client — stub
# --------------------------------------------------------------------------

class GoogleClient:
    """Google Gen AI / Gemini client. Stub until ACTIVE_PROVIDER=google is set."""

    def __init__(self, config: llm_config.LLMConfig):
        if config.provider != llm_config.PROVIDER_GOOGLE:
            raise ValueError(f"GoogleClient requires provider=google, got {config.provider}")
        self.config = config

    def chat(self, messages, *, model=None, max_tokens=None, temperature=None, response_format=None):
        raise NotImplementedError(
            "GoogleClient not yet implemented. Set ACTIVE_PROVIDER=cborg or "
            "implement the Google Gen AI SDK adapter when ready."
        )


# --------------------------------------------------------------------------
# Mock client — deterministic test double
# --------------------------------------------------------------------------

class MockLLMClient:
    """Returns canned responses without hitting any network.

    Configure with a list of `responses` (str, JSON-serializable dict, or
    Exception). Each call to chat() consumes one. Raises StopIteration if
    exhausted.
    """

    def __init__(self, responses: Optional[list] = None,
                 model_id: str = "mock-model"):
        self._responses = list(responses or [])
        self.model_id = model_id
        self.calls: list[dict] = []  # records every call for assertions

    def queue(self, response) -> None:
        self._responses.append(response)

    def chat(self, messages: list[dict], *,
             model: Optional[str] = None,
             max_tokens: Optional[int] = None,
             temperature: Optional[float] = None,
             response_format: Optional[str] = None) -> ChatResponse:
        self.calls.append({
            "messages": messages,
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "response_format": response_format,
        })
        if not self._responses:
            raise LLMClientError("MockLLMClient: response queue exhausted")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        if isinstance(nxt, (dict, list)):
            content = json.dumps(nxt)
        else:
            content = str(nxt)
        return ChatResponse(
            content=content,
            model_id=model or self.model_id,
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            finish_reason="stop",
        )


# --------------------------------------------------------------------------
# Factory
# --------------------------------------------------------------------------

def build_client(config: Optional[llm_config.LLMConfig] = None) -> LLMClient:
    """Construct the LLMClient for the active provider in config.

    If config is None, loads from .env via load_atlas_config().
    """
    if config is None:
        config = llm_config.load_atlas_config()
    if config.provider == llm_config.PROVIDER_CBORG:
        return CBORGClient(config)
    if config.provider == llm_config.PROVIDER_ANTHROPIC:
        return AnthropicClient(config)
    if config.provider == llm_config.PROVIDER_GOOGLE:
        return GoogleClient(config)
    raise ValueError(f"No client implementation for provider: {config.provider}")
