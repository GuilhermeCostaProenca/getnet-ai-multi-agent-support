"""Test doubles and fixtures: a scriptable fake LLM, an in-memory knowledge base and SQLite (EVAL-01)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.rag.store import Hit
from app.schemas import KnowledgeAnswer, Plan, Source, Task, ToolCall
from app.security import issue_token
from app.storage import Store

SECRET = "test-secret-long-enough-for-hs256-0123456789abcdef"
GETNET_HIT = Hit("Pix text", "Pix - Getnet", "https://site.getnet.com.br/pix/", 0.8)


def task(intent: str = "product_knowledge", query: str = "q", consult_kb: bool = False) -> Task:
    return Task(intent=intent, query=query, retrieval_query="consulta", consult_knowledge_base=consult_kb)


def plan(*tasks: Task, confidence: float = 0.95, clarify: bool = False, question: str = "", language: str = "en",
         wants_human: bool = False) -> Plan:
    return Plan(language=language, confidence=confidence, needs_clarification=clarify, clarification_question=question,
                wants_human=wants_human, tasks=list(tasks), rationale="test")


class FakeLLM:
    """Records every call; behaviour is scripted per test."""

    def __init__(self) -> None:
        self.plans: list[Plan] = []
        self.default_plan = plan(task())
        self.kb_answer = KnowledgeAnswer(answer="grounded answer [1]", answered_from_context=True)
        self.web_result: tuple[str, list[Source]] = ("web answer", [Source(title="ex", url="https://example.com")])
        self.getnet_web_result: tuple[str, list[Source]] = ("", [])
        self.tool_plan: list[tuple[str, dict[str, Any]]] = []
        self.tool_results: list[dict[str, Any]] = []
        self.flagged = False
        self.review_ok = True
        self.fail_on: set[str] = set()
        self.calls: list[tuple[str, Any]] = []

    def _record(self, name: str, payload: Any) -> None:
        self.calls.append((name, payload))
        if name in self.fail_on:
            raise self.fail_on[name] if isinstance(self.fail_on, dict) else RuntimeError(f"{name} failed")

    def called(self, name: str) -> list[Any]:
        return [p for n, p in self.calls if n == name]

    def parse(self, instructions, user_input, schema):
        if schema is KnowledgeAnswer:
            self._record("parse_kb", user_input)
            return self.kb_answer
        if schema.__name__ == "Summary":
            self._record("summary", user_input)
            return schema(text=f"SUMMARY#{len(self.called('summary'))}")
        if schema.__name__ == "Review":
            self._record("review", user_input)
            return schema(consistent=self.review_ok, issues="" if self.review_ok else "amount R$ 999 not in tool results")
        if schema.__name__ == "Revision":
            self._record("revise", user_input)
            return schema(answer="revised support answer")
        self._record("plan", user_input)
        return self.plans.pop(0) if self.plans else self.default_plan

    def web_search(self, instructions, question):
        if "site:getnet.com.br" in question:
            self._record("web_getnet", question)
            return self.getnet_web_result
        self._record("web", question)
        return self.web_result

    def run_tools(self, instructions, user_input, tools, execute):
        self._record("run_tools", user_input)
        calls = []
        for name, args in self.tool_plan:
            self.tool_results.append(execute(name, args))
            calls.append(ToolCall(name=name, arguments=args))
        return "support answer", calls

    def embed(self, texts):
        self._record("embed", texts)
        return [[1.0, 0.0] for _ in texts]

    def moderate(self, text):
        self._record("moderate", text)
        return self.flagged


class MemoryKB:
    def __init__(self, hits: list[Hit] | None = None) -> None:
        self.hits = [GETNET_HIT] if hits is None else hits

    def count(self) -> int:
        return len(self.hits)

    def add(self, ids, texts, embeddings, metadatas) -> None:
        self._ids = self.ids() | set(ids)

    def query(self, embedding, k):
        return self.hits[:k]

    def reset(self) -> None:
        self.hits = []

    def ids(self) -> set[str]:
        return set(getattr(self, "_ids", set()))

    def delete(self, ids) -> None:
        self._ids = self.ids() - set(ids)


def make_settings(**overrides: Any) -> Settings:
    base = dict(_env_file=None, openai_api_key="test", jwt_secret=SECRET, auto_ingest=False, enable_dev_tokens=True,
                rate_limit_per_minute=1000, moderation_enabled=True, planner_min_confidence=0.55, max_clarifications=1)
    return Settings(**{**base, **overrides})


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def kb() -> MemoryKB:
    return MemoryKB()


@pytest.fixture
def store() -> Store:
    return Store(":memory:")


@pytest.fixture
def client(settings, llm, kb, store):
    with TestClient(create_app(settings=settings, llm=llm, kb=kb, store=store)) as c:
        yield c


@pytest.fixture
def auth(settings):
    def headers(sub: str = "cliente1988", role: str = "merchant", ttl: int | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {issue_token(settings, sub, role, ttl)}"}
    return headers


@pytest.fixture
def chat(client, auth):
    def send(message: str, conversation_id: str | None = None, sub: str = "cliente1988", **extra: Any):
        body = {"message": message, "conversation_id": conversation_id, **extra}
        return client.post("/v1/chat", json=body, headers=auth(sub))
    return send
