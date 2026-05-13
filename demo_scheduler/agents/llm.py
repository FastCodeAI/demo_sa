"""Single LLM-client abstraction so agents stay testable.

Production code picks a real client via `default_llm()`:
  * `OpenAILLM` when `OPENAI_API_KEY` is set
  * `AnthropicLLM` when `ANTHROPIC_API_KEY` is set
  * otherwise an error — callers should use `MockLLM` explicitly

`.env` at the project root is auto-loaded on import so either key shows
up in `os.environ` without the caller having to source the file.

Tests use `MockLLM`, which replays canned responses and never makes
network calls.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


# Auto-load .env once at import time — no-op if dotenv isn't installed
# or if the file doesn't exist.
def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv as _ld
    except ImportError:
        return
    candidates = [
        Path(__file__).resolve().parents[2] / ".env",  # project root
        Path.cwd() / ".env",
    ]
    for c in candidates:
        if c.exists():
            _ld(c, override=False)
            return


_load_dotenv()


@dataclass
class LLMResponse:
    text: str
    raw: dict | None = None


class LLMClient(Protocol):
    def complete(self, *, system: str, user: str, max_tokens: int = 1024) -> LLMResponse: ...


@dataclass
class MockLLM:
    """Test double — returns the next queued response."""
    responses: list[str] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)

    def complete(self, *, system: str, user: str, max_tokens: int = 1024) -> LLMResponse:
        self.calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        if not self.responses:
            raise RuntimeError("MockLLM ran out of queued responses")
        return LLMResponse(text=self.responses.pop(0))


@dataclass
class OpenAILLM:
    """Real OpenAI SDK client (used when `OPENAI_API_KEY` is set).

    Default model is gpt-4o-mini — fast + cheap, plenty of reasoning
    headroom for the four agent prompts in this codebase (JSON patch,
    intent classification, KPI narration, persona summary). Bump to
    gpt-4o for the heavier reasoning paths if needed.
    """
    model: str = "gpt-4o-mini"
    api_key: str | None = None

    def __post_init__(self) -> None:
        self.api_key = self.api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY not set — use MockLLM or export the key")
        try:
            from openai import OpenAI
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("openai SDK not installed") from e
        self._client = OpenAI(api_key=self.api_key)

    def complete(self, *, system: str, user: str, max_tokens: int = 1024) -> LLMResponse:
        msg = self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        text = msg.choices[0].message.content or ""
        return LLMResponse(text=text, raw=msg.model_dump() if hasattr(msg, "model_dump") else None)


@dataclass
class AnthropicLLM:
    """Real Anthropic SDK client (used when ANTHROPIC_API_KEY is set).

    Defaults to claude-haiku-4-5 for cost/latency. Bump to claude-sonnet-4-6
    when reasoning quality matters more than turnaround. The catalog
    context is sent in `system` with cache_control set, so subsequent
    calls in the same session hit the prompt-cache.
    """
    model: str = "claude-haiku-4-5"
    api_key: str | None = None

    def __post_init__(self) -> None:
        self.api_key = self.api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set — use MockLLM or export the key")
        try:
            from anthropic import Anthropic
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("anthropic SDK not installed") from e
        self._client = Anthropic(api_key=self.api_key)

    def complete(self, *, system: str, user: str, max_tokens: int = 1024) -> LLMResponse:
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in msg.content if hasattr(b, "text"))
        return LLMResponse(text=text, raw=msg.model_dump() if hasattr(msg, "model_dump") else None)


def default_llm(prefer: str | None = None) -> LLMClient:
    """Pick the best available real LLM client.

    Selection rule (in order):
      1. If `prefer` is given, honour it.
      2. If OPENAI_API_KEY is set → OpenAILLM.
      3. If ANTHROPIC_API_KEY is set → AnthropicLLM.
      4. Otherwise raise — the caller should fall back to MockLLM
         explicitly rather than have us pick silently.
    """
    pref = (prefer or "").strip().lower()
    if pref == "openai":
        return OpenAILLM()
    if pref == "anthropic":
        return AnthropicLLM()
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAILLM()
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicLLM()
    raise RuntimeError(
        "no LLM API key found (OPENAI_API_KEY / ANTHROPIC_API_KEY). "
        "Either set one or use MockLLM."
    )
