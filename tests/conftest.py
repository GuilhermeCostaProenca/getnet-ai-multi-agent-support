"""Deterministic test harness (EVAL-01): a fake LLM that implements the LLM protocol, an in-memory store seeded
with knowledge chunks, and an app wired exactly like production."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm import record_usage
from app.main import create_app
from app.schemas import KnowledgeAnswer, Plan, Rerank, Review, Revision, Source, Summary, Task, ToolCall
from app.security import issue_token
from app.storage import Store

SECRET = "test-secret-" + "x" * 40


class _Usage:
    input_tokens = 100
    output_tokens = 20


def task(intent: str, query: str = "q", retrieval_query: str = "") -> Task:
    return Task(intent=intent, query=query, retrieval_query=retrieval_query)


def plan(*tasks: Task, confidence: float = 0.95, clarify: bool = False, human: bool = False, language: str = "pt") -> Plan:
    return Plan(language=language, confidence=confidence, needs_clarification=clarify,
                clarification_question="Pode detalhar?" if clarify else "", wants_human=human, tasks=list(tasks),
                rationale="test")


class FakeLLM:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.plans: list[Plan] = []
        self.default_plan = plan(task("product_knowledge", "Como funciona o crediário?", "crediario parcelas"))
        self.kb_answer = KnowledgeAnswer(answer="No Crediário você parcela em até 48x [1].", answered_from_context=True)
        self.rerank: list[int] | None = None
        self.review = Review(consistent=True, issues="")
        self.tool_plan: list[tuple[str, dict]] = []
        self.web = ("Resposta da web.", [Source(title="Getnet", url="https://site.getnet.com.br/pix/")])
        self.fail_on: dict[str, Exception] = {}

    def _call(self, op: str, payload: Any) -> None:
        self.calls.append((op, payload))
        record_usage(_Usage())
        if op in self.fail_on:
            raise self.fail_on[op]

    def called(self, op: str) -> list[Any]:
        return [p for o, p in self.calls if o == op]

    def structured(self, instructions: str, user_input: str, schema):
        op = {Plan: "plan", KnowledgeAnswer: "kb", Rerank: "rerank", Review: "review", Revision: "revise",
              Summary: "summary"}[schema]
        self._call(op, user_input)
        if schema is Plan:
            return self.plans.pop(0) if self.plans else self.default_plan
        if schema is KnowledgeAnswer:
            return self.kb_answer
        if schema is Rerank:
            return Rerank(order=self.rerank or [1])
        if schema is Review:
            return self.review
        if schema is Revision:
            return Revision(answer="Resposta revisada (dados MOCK).")
        return Summary(summary="Resumo: lojista perguntou sobre crediário.")

    def tool_loop(self, instructions: str, user_input: str, tools, execute):
        self._call("tools", user_input)
        calls = []
        for name, args in self.tool_plan:
            execute(name, args)
            calls.append(ToolCall(name=name, arguments=args))
        return "Resposta do suporte com dados simulados (MOCK).", calls

    def web_search(self, instructions: str, question: str):
        self._call("web", question)
        return self.web


CHUNKS = [
    ("c1", "https://site.getnet.com.br/crediario/", "Crediário", 0,
     "Crediário\nUma opção de pagamento com parcelamento em até 48x para seus clientes no crediário."),
    ("c2", "https://site.getnet.com.br/duvidas/", "Dúvidas", 0,
     "Dúvidas\nQuais modalidades de crédito parcelado existem? Parcelado Emissor admite até 24 parcelas do crediário emissor."),
    ("c3", "https://site.getnet.com.br/pix/", "Pix", 0, "Pix\nReceba vendas com Pix na maquininha pelo QR Code."),
]


def make_settings(**overrides: Any) -> Settings:
    base = dict(_env_file=None, anthropic_api_key="test", jwt_secret=SECRET, auto_ingest=False, enable_dev_tokens=True,
                rate_limit_per_minute=1000, rerank_enabled=True, rag_top_k=2)
    return Settings(**{**base, **overrides})


def seeded_store(path: str = ":memory:") -> Store:
    store = Store(path)
    store.add_chunks(CHUNKS)
    return store


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def store() -> Store:
    return seeded_store()


@pytest.fixture
def client(llm, store):
    with TestClient(create_app(make_settings(), llm=llm, store=store)) as c:
        yield c


@pytest.fixture
def auth():
    settings = make_settings()

    def headers(sub: str = "cliente1988", role: str = "merchant") -> dict[str, str]:
        return {"Authorization": f"Bearer {issue_token(settings, sub, role)}"}

    return headers


@pytest.fixture
def chat(client, auth):
    def send(message: str, cid: str | None = None, sub: str = "cliente1988"):
        body = {"message": message, **({"conversation_id": cid} if cid else {})}
        return client.post("/v1/chat", json=body, headers=auth(sub))

    return send
