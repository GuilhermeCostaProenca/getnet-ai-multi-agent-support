"""Units: RAG (KNW-03..06), resilience (RES-01), tracing (OBS-03), cache (OBS-04), guardrails, tools."""

import time
from datetime import date
from types import SimpleNamespace as NS

import httpx
import pytest

from app.agents.knowledge import KnowledgeAgent
from app.guardrails import check_input, cpf_valid, luhn_valid
from app.rag.index import KnowledgeIndex
from app.rag.ingest import Page, chunk_page, ingest
from app.storage import Store
from app.tools import customer
from tests.conftest import FakeLLM, seeded_store, task


def test_ac51_faq_question_stays_with_its_answer_and_title_prefixes_chunks():
    lines = ["Intro da página de dúvidas"] + [f"Pergunta {i}?" if i % 2 == 0 else "Resposta " + "x" * 300 for i in range(8)]
    chunks = chunk_page(Page("https://site.getnet.com.br/duvidas/", "Dúvidas", lines), max_chars=700)
    assert all(c.startswith("Dúvidas\n") for c in chunks)
    for c in chunks:
        body = c.split("\n")[1:]
        assert not body[-1].endswith("?")  # a chunk never ends on a question separated from its answer


def test_ac53_ingestion_is_incremental_by_content_hash():
    store = Store(":memory:")
    pages = [Page("https://site.getnet.com.br/pix/", "Pix", ["Pix na maquininha " * 10])]
    first = ingest(store, pages)
    assert first["added"] == first["chunks"] > 0 and ingest(store, pages)["added"] == 0
    changed = ingest(store, [Page(pages[0].url, "Pix", ["Pix com QR Code " * 10])])
    assert changed["added"] > 0 and changed["deleted"] == first["chunks"]
    assert ingest(store, [])["deleted"] == 0  # an empty fetch never wipes the index


def test_ac35_bm25_product_page_boost_and_multi_query():
    index = KnowledgeIndex(seeded_store())
    hits = index.search(["parcelas crediário"], 3)
    assert hits[0].url.endswith("/crediario/")
    assert index.search(["installments credit", "parcelamento crediário"], 3)[0].url.endswith("/crediario/")


def test_ac56_repeated_query_hits_cache():
    index = KnowledgeIndex(seeded_store())
    index.search(["pix qr code"], 3)
    index.search(["pix qr code"], 3)
    assert index.cache_hits == 1


def test_ac52_rerank_reorders_and_falls_back_on_failure():
    llm = FakeLLM()
    agent = KnowledgeAgent(llm, KnowledgeIndex(seeded_store()), top_k=1, candidates=3, rerank=True)
    t = task("product_knowledge", "crediário parcelas", "crediario parcelas pix")
    llm.rerank = [3, 1]
    hits, mode = agent.retrieve(t)
    assert mode == "bm25+rerank" and len(hits) == 1
    llm.fail_on["rerank"] = RuntimeError("down")
    hits, mode = agent.retrieve(t)
    assert mode.startswith("bm25 (rerank failed") and hits[0].url.endswith("/crediario/")


def test_ac54_slow_tool_times_out(monkeypatch):
    monkeypatch.setitem(customer.TOOLS, "slow", lambda user_id: time.sleep(0.5) or {"ok": True})
    assert customer.execute_tool("cliente1988", "slow", {}, timeout=0.05)["error"] == "tool_timeout"


def test_ac54_outage_on_primary_model_retries_once_on_fallback():
    import anthropic

    from app.config import Settings
    from app.llm import ClaudeLLM

    llm = ClaudeLLM(Settings(_env_file=None, anthropic_api_key="x", llm_model="primary", fallback_model="backup"))
    seen = []

    def create(model, **kw):
        seen.append(model)
        if model == "primary":
            raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
        return NS(usage=None, stop_reason="end_turn", content=[NS(type="text", text="ok")])

    llm._client = NS(messages=NS(create=create))
    assert llm._create("t", messages=[]).content[0].text == "ok" and seen == ["primary", "backup"]


def test_refusal_stop_reason_raises_model_refusal():
    from app.config import Settings
    from app.llm import ClaudeLLM, ModelRefusal

    llm = ClaudeLLM(Settings(_env_file=None, anthropic_api_key="x"))
    llm._client = NS(messages=NS(create=lambda **kw: NS(usage=None, stop_reason="refusal", content=[])))
    with pytest.raises(ModelRefusal):
        llm._create("t", messages=[])


def test_ac55_spans_are_emitted_when_tracing_enabled(chat):
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from app import telemetry

    exporter = InMemorySpanExporter()
    telemetry.setup_tracing(False, exporter=exporter)
    try:
        chat("Como funciona o crediário?")
        names = {s.name for s in exporter.get_finished_spans()}
        assert {"chat.request", "step.planner", "step.knowledge_agent.rag"} <= names
    finally:
        telemetry._provider = None


def test_guardrail_validators():
    assert luhn_valid("4111111111111111") and not luhn_valid("4111111111111112")
    assert cpf_valid("52998224725") and not cpf_valid("11111111111")
    assert "1234" not in check_input("minha senha: 1234").text
    assert not check_input("Como funciona o crediário?").flags


def test_settlements_use_business_days_in_merchant_timezone(monkeypatch):
    monkeypatch.setattr(customer, "business_today", lambda: date(2026, 11, 23))  # Monday
    out = customer.get_sales_and_settlements("cliente1988")
    by_method = {s["method"]: s for s in out["sales"]}
    assert by_method["pix"]["expected_deposit"] == "2026-11-22" and by_method["pix"]["status"] == "deposited"
    assert by_method["debit"]["expected_deposit"] == "2026-11-23" and out["data_source"].startswith("MOCK")
    assert customer.get_merchant_profile("desconhecido")["error"] == "customer_not_found"
