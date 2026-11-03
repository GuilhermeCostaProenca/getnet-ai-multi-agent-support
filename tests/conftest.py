"""Test doubles: a scriptable fake LLM and an in-memory vector store (NFR-TEST-01)."""

import math
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import build_orchestrator, create_app
from app.rag.store import Hit
from app.schemas import RouteDecision, Source, ToolCall


def decision(intent="product_knowledge", confidence=0.95, consult_kb=False, language="en") -> RouteDecision:
    return RouteDecision(intent=intent, confidence=confidence, language=language, consult_knowledge_base=consult_kb,
                         retrieval_query="consulta", rationale="test")


class FakeLLM:
    """Records every call; behaviour is scripted per test."""

    def __init__(self):
        self.route = decision()
        self.tool_plan: list[tuple[str, dict[str, Any]]] = []
        self.flagged = False
        self.fail_on: set[str] = set()
        self.calls: list[tuple[str, Any]] = []
        self.tool_results: list[dict[str, Any]] = []

    def _record(self, name, payload):
        self.calls.append((name, payload))
        if name in self.fail_on:
            raise RuntimeError(f"{name} failed")

    def called(self, name) -> list[Any]:
        return [p for n, p in self.calls if n == name]

    def parse(self, instructions, user_input, schema):
        self._record("parse", user_input)
        return self.route

    def generate(self, instructions, user_input):
        self._record("generate", user_input)
        return "grounded answer [1]"

    def web_search(self, instructions, question):
        self._record("web_search", question)
        return "web answer", [Source(title="example", url="https://example.com")]

    def run_tools(self, instructions, user_input, tools, execute):
        self._record("run_tools", user_input)
        calls = []
        for name, args in self.tool_plan:
            self.tool_results.append(execute(name, args))
            calls.append(ToolCall(name=name, arguments=args))
        return "support answer", calls

    def embed(self, texts):
        self._record("embed", texts)
        return [[float(len(t) % 7 + 1), 1.0] for t in texts]

    def moderate(self, text):
        self._record("moderate", text)
        return self.flagged


class MemoryStore:
    def __init__(self, hits: list[Hit] | None = None):
        self.hits = hits if hits is not None else [Hit("Pix text", "Pix - Getnet", "https://site.getnet.com.br/pix/", 0.8)]
        self.added: list[tuple] = []

    def count(self):
        return len(self.hits) + len(self.added)

    def add(self, ids, texts, embeddings, metadatas):
        self.added.extend(zip(ids, texts, embeddings, metadatas))

    def query(self, embedding, k):
        assert not math.isnan(embedding[0])
        return self.hits[:k]

    def reset(self):
        self.added.clear()


@pytest.fixture
def settings() -> Settings:
    # _env_file=None: tests must not depend on the developer's local .env
    return Settings(_env_file=None, openai_api_key="test", auto_ingest=False, moderation_enabled=True,
                    router_min_confidence=0.55, rag_min_similarity=0.3)


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def orchestrator(settings, llm, store):
    return build_orchestrator(settings, llm, store)


@pytest.fixture
def client(settings, llm, store):
    with TestClient(create_app(llm=llm, store=store, settings=settings)) as c:
        yield c
