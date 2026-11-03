"""LLM provider abstraction (NFR-PORT-01).

Agents depend on the `LLMClient` protocol only; `OpenAIClient` is the production
implementation and tests inject a fake.
"""

import json
from collections.abc import Callable
from typing import Any, Protocol, TypeVar

from openai import OpenAI
from pydantic import BaseModel

from app.config import Settings
from app.schemas import Source, ToolCall

T = TypeVar("T", bound=BaseModel)
ToolExecutor = Callable[[str, dict[str, Any]], dict[str, Any]]


class LLMClient(Protocol):
    def parse(self, instructions: str, user_input: str, schema: type[T]) -> T: ...

    def web_search(self, instructions: str, question: str) -> tuple[str, list[Source]]: ...

    def run_tools(
        self, instructions: str, user_input: str, tools: list[dict[str, Any]], execute: ToolExecutor
    ) -> tuple[str, list[ToolCall]]: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def moderate(self, text: str) -> bool: ...


class OpenAIClient:
    MAX_TOOL_ROUNDS = 4
    EMBED_BATCH = 96

    def __init__(self, settings: Settings):
        self._client = OpenAI(
            api_key=settings.openai_api_key, timeout=settings.llm_timeout_seconds, max_retries=2
        )
        self._model = settings.chat_model
        self._embedding_model = settings.embedding_model
        self._moderation_model = settings.moderation_model

    def parse(self, instructions: str, user_input: str, schema: type[T]) -> T:
        resp = self._client.responses.parse(
            model=self._model, instructions=instructions, input=user_input, text_format=schema
        )
        if resp.output_parsed is None:
            raise ValueError("LLM returned no structured output")
        return resp.output_parsed

    def web_search(self, instructions: str, question: str) -> tuple[str, list[Source]]:
        resp = self._client.responses.create(
            model=self._model, instructions=instructions, input=question, tools=[{"type": "web_search"}],
            include=["web_search_call.action.sources"],
        )
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
                    # URL sources have `url`; provider data APIs (e.g. weather) have only `name`.
                    url, name = getattr(src, "url", None), getattr(src, "name", None)
                    key = url or f"api:{name or 'unknown'}"
                    consulted.setdefault(key, Source(title=name or url or "web search", url=key))
        # Prefer sources the model actually cited; otherwise report what the search consulted.
        sources = list(cited.values()) or list(consulted.values())[:5]
        return resp.output_text.strip(), sources

    def run_tools(
        self, instructions: str, user_input: str, tools: list[dict[str, Any]], execute: ToolExecutor
    ) -> tuple[str, list[ToolCall]]:
        conversation: list[Any] = [{"role": "user", "content": user_input}]
        calls: list[ToolCall] = []
        for _ in range(self.MAX_TOOL_ROUNDS):
            resp = self._client.responses.create(
                model=self._model, instructions=instructions, input=conversation, tools=tools
            )
            function_calls = [item for item in resp.output if item.type == "function_call"]
            if not function_calls:
                return resp.output_text.strip(), calls
            conversation += resp.output
            for fc in function_calls:
                args = json.loads(fc.arguments or "{}")
                calls.append(ToolCall(name=fc.name, arguments=args))
                result = execute(fc.name, args)
                conversation.append(
                    {"type": "function_call_output", "call_id": fc.call_id, "output": json.dumps(result, default=str)}
                )
        raise RuntimeError("Tool loop exceeded the maximum number of rounds")

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self.EMBED_BATCH):
            resp = self._client.embeddings.create(model=self._embedding_model, input=texts[i : i + self.EMBED_BATCH])
            vectors.extend(d.embedding for d in resp.data)
        return vectors

    def moderate(self, text: str) -> bool:
        resp = self._client.moderations.create(model=self._moderation_model, input=text)
        return bool(resp.results[0].flagged)
