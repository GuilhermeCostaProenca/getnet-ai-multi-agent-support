"""FastAPI entry point (REQ-API-01..03)."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool

from app.agents.escalation import EscalationAgent
from app.agents.knowledge import KnowledgeAgent
from app.agents.router import RouterAgent
from app.agents.support import SupportAgent
from app.config import Settings, get_settings
from app.llm import LLMClient, OpenAIClient
from app.observability import configure_logging
from app.orchestrator import Orchestrator
from app.rag.ingest import ingest
from app.rag.store import ChromaStore, KnowledgeStore
from app.schemas import ChatRequest, ChatResponse

log = logging.getLogger(__name__)


def build_orchestrator(settings: Settings, llm: LLMClient, store: KnowledgeStore) -> Orchestrator:
    return Orchestrator(
        llm=llm,
        router=RouterAgent(llm),
        knowledge=KnowledgeAgent(llm, store, settings.rag_top_k, settings.rag_min_similarity),
        support=SupportAgent(llm),
        escalation=EscalationAgent(),
        min_confidence=settings.router_min_confidence,
        moderation_enabled=settings.moderation_enabled,
    )


def create_app(llm: LLMClient | None = None, store: KnowledgeStore | None = None,
               settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(settings.log_level)
        app.state.llm = llm or OpenAIClient(settings)
        app.state.store = store or ChromaStore(settings.chroma_path, settings.chroma_collection)
        if settings.auto_ingest and app.state.store.count() == 0:
            try:
                await run_in_threadpool(ingest, app.state.llm, app.state.store)
            except Exception:  # noqa: BLE001 - service still starts; KB answers will hand off
                log.exception("startup_ingest_failed")
        app.state.orchestrator = build_orchestrator(settings, app.state.llm, app.state.store)
        yield

    app = FastAPI(title="Getnet Multi-Agent Support", version="1.0.0", lifespan=lifespan)

    @app.post("/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest) -> ChatResponse:
        return await run_in_threadpool(app.state.orchestrator.handle, req)

    @app.get("/health")
    async def health() -> dict:
        chunks = app.state.store.count()
        # degraded: API is up but product answers will hand off until the KB is ingested
        return {"status": "ok" if chunks else "degraded", "knowledge_base_chunks": chunks}

    return app


app = create_app()
