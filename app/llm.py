"""LLM provider abstraction (ADR-07) with per-request token metering (ADR-09).

Agents depend only on the `LLMClient` protocol; tests inject a fake.
"""

import json
import threading
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any, Protocol, TypeVar

from openai import OpenAI
from pydantic import BaseModel

from app.config import Settings
from app.schemas import Source, ToolCall, Usage

T = TypeVar("T", bound=BaseModel)
ToolExecutor = Callable[[str, dict[str, Any]], dict[str, Any]]


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


def _record(usage: Any) -> None:
    meter = _meter.get()
    if meter is None or usage is None:
        return
    inp = getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    meter.add(int(inp), int(out))


class LLMClient(Protocol):
    def parse(self, instructions: str, user_input: str, schema: type[T]) -> T: ...

    def web_search(self, instructions: str, question: str) -> tuple[str, list[Source]]: ...

    def run_tools(self, instructions: str, user_input: str, tools: list[dict[str, Any]],
                  execute: ToolExecutor) -> tuple[str, list[ToolCall]]: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def moderate(self, text: str) -> bool: ...


class OpenAIClient:
    MAX_TOOL_ROUNDS = 4
    EMBED_BATCH = 96

    def __init__(self, settings: Settings):
        self._client = OpenAI(api_key=settings.openai_api_key, timeout=settings.llm_timeout_seconds, max_retries=2)
        self._model = settings.chat_model
        self._embedding_model = settings.embedding_model
        self._moderation_model = settings.moderation_model

    def parse(self, instructions: str, user_input: str, schema: type[T]) -> T:
        resp = self._client.responses.parse(model=self._model, instructions=instructions, input=user_input,
                                            text_format=schema)
        _record(resp.usage)
        if resp.output_parsed is None:
            raise ValueError("LLM returned no structured output")
        return resp.output_parsed

    def web_search(self, instructions: str, question: str) -> tuple[str, list[Source]]:
        resp = self._client.responses.create(model=self._model, instructions=instructions, input=question,
                                             tools=[{"type": "web_search"}],
                                             include=["web_search_call.action.sources"])
        _record(resp.usage)
        cited: dict[str, Source] = {}
        consulted: dict[str, Source] = {}
        for item in resp.output:
            if item.type == "message":
                for content in item.content:
                    for ann in getattr(content, "annotations", None) or []:
                        if getattr(ann, "type", "") == "url_citation" and ann.url not in cited:
                            cited[ann.url] = Source(title=ann.title or ann.url, url=ann.url)
            elif item.type == "web_search_call":
                for src in getattr(item.action, "sources", None) or []:
                    # URL sources carry `url`; provider data APIs (e.g. weather) carry only `name`
                    url, name = getattr(src, "url", None), getattr(src, "name", None)
                    key = url or f"api:{name or 'unknown'}"
                    consulted.setdefault(key, Source(title=name or url or "web search", url=key))
        return resp.output_text.strip(), list(cited.values()) or list(consulted.values())[:5]

    def run_tools(self, instructions: str, user_input: str, tools: list[dict[str, Any]],
                  execute: ToolExecutor) -> tuple[str, list[ToolCall]]:
        conversation: list[Any] = [{"role": "user", "content": user_input}]
        calls: list[ToolCall] = []
        for _ in range(self.MAX_TOOL_ROUNDS):
            resp = self._client.responses.create(model=self._model, instructions=instructions, input=conversation,
                                                 tools=tools)
            _record(resp.usage)
            function_calls = [item for item in resp.output if item.type == "function_call"]
            if not function_calls:
                return resp.output_text.strip(), calls
            conversation += resp.output
            for fc in function_calls:
                args = json.loads(fc.arguments or "{}")
                calls.append(ToolCall(name=fc.name, arguments=args))
                conversation.append({"type": "function_call_output", "call_id": fc.call_id,
                                     "output": json.dumps(execute(fc.name, args), default=str)})
        raise RuntimeError("tool loop exceeded the maximum number of rounds")

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self.EMBED_BATCH):
            resp = self._client.embeddings.create(model=self._embedding_model, input=texts[i:i + self.EMBED_BATCH])
            _record(resp.usage)
            vectors.extend(d.embedding for d in resp.data)
        return vectors

    def moderate(self, text: str) -> bool:
        resp = self._client.moderations.create(model=self._moderation_model, input=text)
        return bool(resp.results[0].flagged)
