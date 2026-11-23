"""FastAPI entry point: /v1 API with JWT authentication, roles and rate limiting (specs/03-api-contract.md)."""

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.agents.handoff import Conflict, HandoffService
from app.agents.knowledge import KnowledgeAgent
from app.agents.planner import Planner
from app.agents.support import SupportAgent
from app.config import Settings, get_settings
from app.llm import LLMClient, OpenAIClient
from app.observability import Metrics, configure_logging
from app.orchestrator import Orchestrator
from app.rag.ingest import ingest
from app.rag.store import ChromaStore, KnowledgeStore
from app.schemas import (ChatRequest, ChatResponse, ConversationView, DevTokenRequest, MessageView, OperatorReply,
                         ResolveRequest, TicketView, TokenResponse)
from app.security import AuthError, Principal, RateLimiter, issue_token, verify_token
from app.storage import NotFound, Store

log = logging.getLogger(__name__)


def build_services(settings: Settings, llm: LLMClient, kb: KnowledgeStore, store: Store,
                   metrics: Metrics) -> tuple[Orchestrator, HandoffService]:
    handoff = HandoffService(store)
    orchestrator = Orchestrator(
        llm, store, Planner(llm), KnowledgeAgent(llm, kb, settings.rag_top_k, settings.rag_min_similarity),
        SupportAgent(llm), handoff, min_confidence=settings.planner_min_confidence,
        max_clarifications=settings.max_clarifications, history_window=settings.history_window,
        moderation_enabled=settings.moderation_enabled, metrics=metrics)
    return orchestrator, handoff


def _ticket_view(ticket: dict[str, Any]) -> TicketView:
    return TicketView(ticket_id=ticket["id"], **{k: v for k, v in ticket.items() if k != "id"})


def create_app(settings: Settings | None = None, llm: LLMClient | None = None, kb: KnowledgeStore | None = None,
               store: Store | None = None) -> FastAPI:
    settings = settings or get_settings()
    bearer = HTTPBearer(auto_error=False)
    limiter = RateLimiter(settings.rate_limit_per_minute)
    metrics = Metrics()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(settings.log_level)
        if not settings.jwt_secret:
            raise RuntimeError("JWT_SECRET is not set")
        app.state.llm = llm or OpenAIClient(settings)
        app.state.kb = kb or ChromaStore(settings.chroma_path, settings.chroma_collection)
        app.state.store = store or Store(settings.database_path)
        if settings.auto_ingest and app.state.kb.count() == 0:
            try:
                await run_in_threadpool(ingest, app.state.llm, app.state.kb)
            except Exception:  # noqa: BLE001 - the API still starts; product answers will hand off
                log.exception("startup_ingest_failed")
        app.state.orchestrator, app.state.handoff = build_services(settings, app.state.llm, app.state.kb,
                                                                   app.state.store, metrics)
        yield

    app = FastAPI(title="Getnet Multi-Agent Support", version="2.0.0", lifespan=lifespan)

    def principal(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Principal:
        if creds is None:
            raise HTTPException(401, "missing bearer token", headers={"WWW-Authenticate": "Bearer"})
        try:
            return verify_token(settings, creds.credentials)
        except AuthError as exc:
            raise HTTPException(exc.status, exc.detail, headers={"WWW-Authenticate": "Bearer"}) from exc

    def require(role: str) -> Callable[..., Principal]:
        def dependency(p: Principal = Depends(principal)) -> Principal:
            if p.role != role:
                raise HTTPException(403, f"requires role '{role}'")
            return p
        return dependency

    merchant, operator = require("merchant"), require("operator")

    # --- merchant ------------------------------------------------------------------------------------
    @app.post("/v1/chat", response_model=ChatResponse, tags=["merchant"])
    def chat(req: ChatRequest, p: Principal = Depends(merchant)) -> ChatResponse:
        if req.user_id and req.user_id != p.sub:  # SEC-02: identity comes from the token, never the body
            raise HTTPException(403, "user_id does not match the authenticated identity")
        if not limiter.allow(p.sub):
            raise HTTPException(429, "rate limit exceeded")
        try:
            return app.state.orchestrator.handle(p.sub, req)
        except NotFound as exc:
            raise HTTPException(404, "conversation not found") from exc

    @app.get("/v1/conversations/{conversation_id}", response_model=ConversationView, tags=["merchant"])
    def conversation(conversation_id: str, p: Principal = Depends(merchant)) -> ConversationView:
        conv = app.state.store.get_conversation(conversation_id)
        if conv is None or conv["user_id"] != p.sub:  # foreign conversations are indistinguishable from missing
            raise HTTPException(404, "conversation not found")
        messages = [MessageView(role=m["role"], content=m["content"], agent=m["agent"], created_at=m["created_at"])
                    for m in app.state.store.messages(conversation_id)]
        return ConversationView(conversation_id=conversation_id, status=conv["status"], messages=messages)

    # --- operator ------------------------------------------------------------------------------------
    def ticket_action(action: Callable[[], dict[str, Any]]) -> TicketView:
        try:
            return _ticket_view(action())
        except NotFound as exc:
            raise HTTPException(404, "ticket not found") from exc
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/v1/operator/tickets", response_model=list[TicketView], tags=["operator"])
    def list_tickets(status: Literal["open", "claimed", "resolved"] | None = Query(None),
                     p: Principal = Depends(operator)) -> list[TicketView]:
        return [_ticket_view(t) for t in app.state.handoff.list(status)]

    @app.post("/v1/operator/tickets/{ticket_id}/claim", response_model=TicketView, tags=["operator"])
    def claim(ticket_id: str, p: Principal = Depends(operator)) -> TicketView:
        return ticket_action(lambda: app.state.handoff.claim(ticket_id, p.sub))

    @app.post("/v1/operator/tickets/{ticket_id}/reply", response_model=TicketView, tags=["operator"])
    def reply(ticket_id: str, body: OperatorReply, p: Principal = Depends(operator)) -> TicketView:
        return ticket_action(lambda: app.state.handoff.reply(ticket_id, p.sub, body.message))

    @app.post("/v1/operator/tickets/{ticket_id}/resolve", response_model=TicketView, tags=["operator"])
    def resolve(ticket_id: str, body: ResolveRequest, p: Principal = Depends(operator)) -> TicketView:
        return ticket_action(lambda: app.state.handoff.resolve(ticket_id, p.sub, body.resolution))

    # --- auth (demo only) and health -----------------------------------------------------------------
    if settings.enable_dev_tokens:
        @app.post("/v1/auth/dev-token", response_model=TokenResponse, tags=["auth (dev only)"])
        def dev_token(req: DevTokenRequest) -> TokenResponse:
            """Demo/testing only (ENABLE_DEV_TOKENS). Production tokens come from the company identity provider."""
            return TokenResponse(access_token=issue_token(settings, req.user_id, req.role))

    @app.get("/metrics", tags=["ops"], response_class=PlainTextResponse)
    def metrics_endpoint() -> str:
        """Prometheus text format (scraped from the internal network in production)."""
        return metrics.render()

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, Any]:
        chunks = app.state.kb.count()
        return {"status": "ok" if chunks else "degraded", "knowledge_base_chunks": chunks}

    return app


app = create_app()
