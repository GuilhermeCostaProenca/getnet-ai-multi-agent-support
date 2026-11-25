"""FastAPI app (03-api-contract): JWT-protected chat, conversations, operator workflow, metrics, health and the
web console at `/` (UI-01)."""

import hashlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.agents.escalation import HandoffService
from app.agents.knowledge import KnowledgeAgent
from app.agents.planner import Planner
from app.agents.support import SupportAgent
from app.config import Settings, get_settings
from app.llm import LLM, ClaudeLLM
from app.memory import ConversationMemory
from app.observability import Metrics, configure_logging
from app.orchestrator import Orchestrator
from app.rag.index import KnowledgeIndex
from app.rag.ingest import ingest
from app.schemas import (ChatRequest, ChatResponse, ConversationView, DevTokenRequest, MessageView, OperatorReply,
                         ResolveRequest, TicketView, TokenResponse)
from app.security import AuthError, Principal, RateLimiter, issue_token, verify_token
from app.storage import Conflict, NotFound, Store
from app.telemetry import setup_tracing

log = logging.getLogger("app.main")
STATIC = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None, llm: LLM | None = None, store: Store | None = None) -> FastAPI:
    settings = settings or get_settings()
    metrics = Metrics()
    limiter = RateLimiter(settings.rate_limit_per_minute)
    bearer = HTTPBearer(auto_error=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(settings.log_level)
        if not settings.jwt_secret:
            raise RuntimeError("JWT_SECRET is not set")
        setup_tracing(settings.otel_enabled, settings.otel_endpoint)
        app.state.store = store or Store(settings.database_path)
        app.state.llm = llm or ClaudeLLM(settings)
        if settings.auto_ingest and not app.state.store.chunk_ids():
            try:
                await run_in_threadpool(ingest, app.state.store)
            except Exception:  # noqa: BLE001 - the API still starts; product answers will hand off
                log.exception("startup_ingest_failed")
        app.state.index = KnowledgeIndex(app.state.store)
        app.state.handoff = HandoffService(app.state.store)
        app.state.orchestrator = Orchestrator(
            app.state.store, Planner(app.state.llm),
            KnowledgeAgent(app.state.llm, app.state.index, settings.rag_top_k, settings.rag_candidates,
                           settings.rerank_enabled),
            SupportAgent(app.state.llm, settings.tool_timeout_seconds), app.state.handoff,
            ConversationMemory(app.state.store, app.state.llm, settings.history_window), metrics,
            min_confidence=settings.planner_min_confidence, max_clarifications=settings.max_clarifications)
        yield

    app = FastAPI(title="Getnet Multi-Agent Support (v3)", version="3.0.0", lifespan=lifespan)
    app.state.metrics = metrics

    def principal(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Principal:
        if creds is None:
            raise HTTPException(401, "missing bearer token")
        try:
            return verify_token(settings, creds.credentials)
        except AuthError as exc:
            raise HTTPException(exc.status, exc.detail) from exc

    def merchant(p: Principal = Depends(principal)) -> Principal:
        if p.role != "merchant":
            raise HTTPException(403, "merchant role required")
        return p

    def operator(p: Principal = Depends(principal)) -> Principal:
        if p.role != "operator":
            raise HTTPException(403, "operator role required")
        return p

    @app.get("/", include_in_schema=False)
    def console() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.post("/v1/chat", response_model=ChatResponse, tags=["merchant"])
    def chat(req: ChatRequest, p: Principal = Depends(merchant),
             idempotency_key: str | None = Header(None, max_length=64)) -> ChatResponse:
        if req.user_id and req.user_id != p.sub:  # SEC-02
            raise HTTPException(403, "user_id does not match the authenticated identity")
        body_hash = hashlib.sha256(req.model_dump_json().encode()).hexdigest()
        if idempotency_key:  # API-02
            stored = app.state.store.get_idempotent(p.sub, idempotency_key)
            if stored:
                if stored["request_hash"] != body_hash:
                    raise HTTPException(422, "Idempotency-Key already used with a different request")
                return ChatResponse.model_validate_json(stored["response"])
        if not limiter.allow(p.sub):
            raise HTTPException(429, "rate limit exceeded")
        try:
            response = app.state.orchestrator.handle(p.sub, req)
        except NotFound as exc:
            raise HTTPException(404, "conversation not found") from exc
        if idempotency_key:
            app.state.store.save_idempotent(p.sub, idempotency_key, body_hash, response.model_dump_json())
        return response

    @app.get("/v1/conversations/{conversation_id}", response_model=ConversationView, tags=["merchant"])
    def conversation(conversation_id: str, p: Principal = Depends(merchant)) -> ConversationView:
        conv = app.state.store.get_conversation(conversation_id)
        if conv is None or conv["user_id"] != p.sub:
            raise HTTPException(404, "conversation not found")
        msgs = [MessageView(role=m["role"], content=m["content"], agent=m["agent"], created_at=m["created_at"])
                for m in app.state.store.messages(conversation_id)]
        return ConversationView(conversation_id=conversation_id, status=conv["status"], messages=msgs)

    def ticket_view(t: dict) -> TicketView:
        return TicketView(ticket_id=t["id"], conversation_id=t["conversation_id"], user_id=t["user_id"],
                          status=t["status"], reason=t["reason"], context=t["context"], operator_id=t["operator_id"],
                          created_at=t["created_at"], updated_at=t["updated_at"])

    def operate(fn, *args) -> TicketView:
        try:
            return ticket_view(fn(*args))
        except NotFound as exc:
            raise HTTPException(404, "ticket not found") from exc
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/v1/operator/tickets", response_model=list[TicketView], tags=["operator"])
    def tickets(status: str | None = Query(None), p: Principal = Depends(operator)) -> list[TicketView]:
        return [ticket_view(t) for t in app.state.handoff.list(status)]

    @app.post("/v1/operator/tickets/{tid}/claim", response_model=TicketView, tags=["operator"])
    def claim(tid: str, p: Principal = Depends(operator)) -> TicketView:
        return operate(app.state.handoff.claim, tid, p.sub)

    @app.post("/v1/operator/tickets/{tid}/reply", response_model=TicketView, tags=["operator"])
    def reply(tid: str, body: OperatorReply, p: Principal = Depends(operator)) -> TicketView:
        return operate(app.state.handoff.reply, tid, p.sub, body.message)

    @app.post("/v1/operator/tickets/{tid}/resolve", response_model=TicketView, tags=["operator"])
    def resolve(tid: str, body: ResolveRequest, p: Principal = Depends(operator)) -> TicketView:
        return operate(app.state.handoff.resolve, tid, p.sub, body.resolution)

    @app.post("/v1/auth/dev-token", response_model=TokenResponse, tags=["dev"])
    def dev_token(body: DevTokenRequest) -> TokenResponse:
        if not settings.enable_dev_tokens:
            raise HTTPException(404, "not found")
        return TokenResponse(access_token=issue_token(settings, body.user_id, body.role))

    @app.get("/metrics", response_class=PlainTextResponse, tags=["ops"])
    def metrics_endpoint() -> str:
        return metrics.render()

    @app.get("/health", tags=["ops"])
    def health() -> dict:
        chunks = app.state.index.size
        return {"status": "ok" if chunks else "degraded", "knowledge_chunks": chunks}

    return app


app = create_app()
