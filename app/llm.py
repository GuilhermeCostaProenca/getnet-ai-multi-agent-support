"""Claude client (ADR-02): JSON-schema structured outputs, tool loop, server-side web search, token metering,
fallback model on outages (RES-01) and provider refusals surfaced as ModelRefusal (SEC-05)."""

import json
import threading
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from app.config import Settings
from app.schemas import Source, ToolCall, Usage
from app.telemetry import span

T = TypeVar("T", bound=BaseModel)
ToolExecutor = Callable[[str, dict[str, Any]], dict[str, Any]]


class ModelRefusal(RuntimeError):
    """The model declined on safety grounds: handled as the `unsafe` intent, never as a failure."""


# --- per-request token metering (OBS-01) -------------------------------------------------------------
class UsageMeter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.usage = Usage()

    def add(self, input_tokens: int, output_tokens: int) -> None:
        with self._lock:
            self.usage.llm_calls += 1
            self.usage.input_tokens += input_tokens
            self.usage.output_tokens += output_tokens


_meter: ContextVar[UsageMeter | None] = ContextVar("usage_meter", default=None)


def start_meter() -> UsageMeter:
    meter = UsageMeter()
    _meter.set(meter)
    return meter


_evidence: ContextVar[list[str] | None] = ContextVar("evidence", default=None)


def start_evidence() -> list[str]:
    """Per-request record of the excerpts the agents grounded on (used by the eval judge)."""
    evidence: list[str] = []
    _evidence.set(evidence)
    return evidence


def record_evidence(texts: list[str]) -> None:
    evidence = _evidence.get()
    if evidence is not None:
        evidence.extend(texts)


def current_evidence() -> list[str]:
    return list(_evidence.get() or [])


def record_usage(usage: Any) -> None:
    meter = _meter.get()
    if meter is not None and usage is not None:
        meter.add(int(getattr(usage, "input_tokens", 0) or 0), int(getattr(usage, "output_tokens", 0) or 0))


class LLM(Protocol):
    def structured(self, instructions: str, user_input: str, schema: type[T]) -> T: ...

    def tool_loop(self, instructions: str, user_input: str, tools: list[dict[str, Any]],
                  execute: ToolExecutor) -> tuple[str, list[ToolCall]]: ...

    def web_search(self, instructions: str, question: str) -> tuple[str, list[Source]]: ...


def _text(resp: Any) -> str:
    return "".join(b.text for b in resp.content if b.type == "text").strip()


class ClaudeLLM:
    MAX_TOOL_ROUNDS = 4

    def __init__(self, settings: Settings):
        import anthropic

        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=settings.llm_timeout_seconds,
                                           max_retries=2)
        self._outage = (anthropic.APIConnectionError, anthropic.APITimeoutError, anthropic.InternalServerError)
        self._model = settings.llm_model
        self._fallback = settings.fallback_model
        self._effort = settings.llm_effort
        self._max_tokens = settings.llm_max_tokens

    def _create(self, op: str, **kwargs: Any) -> Any:
        with span(f"llm.{op}", model=self._model):
            try:
                resp = self._client.messages.create(model=self._model, max_tokens=self._max_tokens, **kwargs)
            except self._outage:
                if not self._fallback or self._fallback == self._model:
                    raise
                resp = self._client.messages.create(model=self._fallback, max_tokens=self._max_tokens, **kwargs)
        record_usage(resp.usage)
        if resp.stop_reason == "refusal":
            raise ModelRefusal(op)
        return resp

    def structured(self, instructions: str, user_input: str, schema: type[T]) -> T:
        from anthropic import transform_schema

        resp = self._create("structured", system=instructions, messages=[{"role": "user", "content": user_input}],
                            output_config={"effort": self._effort,
                                           "format": {"type": "json_schema", "schema": transform_schema(schema)}})
        return schema.model_validate_json(_text(resp))

    def tool_loop(self, instructions: str, user_input: str, tools: list[dict[str, Any]],
                  execute: ToolExecutor) -> tuple[str, list[ToolCall]]:
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_input}]
        calls: list[ToolCall] = []
        for _ in range(self.MAX_TOOL_ROUNDS):
            resp = self._create("tool_loop", system=instructions, messages=messages, tools=tools,
                                output_config={"effort": self._effort})
            uses = [b for b in resp.content if b.type == "tool_use"]
            if not uses:
                return _text(resp), calls
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for use in uses:
                args = dict(use.input or {})
                calls.append(ToolCall(name=use.name, arguments=args))
                with span("tool.execute", tool=use.name):
                    output = execute(use.name, args)
                results.append({"type": "tool_result", "tool_use_id": use.id,
                                "content": json.dumps(output, default=str, ensure_ascii=False)})
            messages.append({"role": "user", "content": results})
        raise RuntimeError("tool loop exceeded the maximum number of rounds")

    def web_search(self, instructions: str, question: str) -> tuple[str, list[Source]]:
        messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
        tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}]
        resp = self._create("web_search", system=instructions, messages=messages, tools=tools,
                            output_config={"effort": self._effort})
        for _ in range(2):  # server tools may pause a long turn; continue it (bounded)
            if resp.stop_reason != "pause_turn":
                break
            messages.append({"role": "assistant", "content": resp.content})
            resp = self._create("web_search", system=instructions, messages=messages, tools=tools,
                                output_config={"effort": self._effort})
        cited: dict[str, Source] = {}
        consulted: dict[str, Source] = {}
        for block in resp.content:
            if block.type == "text":
                for c in getattr(block, "citations", None) or []:
                    if getattr(c, "url", None):
                        cited[c.url] = Source(title=getattr(c, "title", None) or c.url, url=c.url)
            elif block.type == "web_search_tool_result":
                for r in getattr(block, "content", None) or []:
                    if getattr(r, "url", None):
                        consulted[r.url] = Source(title=getattr(r, "title", None) or r.url, url=r.url)
        return _text(resp), list(cited.values()) or list(consulted.values())[:5]
