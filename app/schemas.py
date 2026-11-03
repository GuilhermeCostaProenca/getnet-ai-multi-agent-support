"""Typed contracts shared by the API and the agents (specs/03-api-contract.md)."""

from typing import Any, Literal

from pydantic import BaseModel, Field

Intent = Literal["product_knowledge", "general_info", "customer_support", "out_of_scope", "unsafe"]
AgentName = Literal["knowledge", "support", "escalation", "guardrail"]


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    user_id: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")

    model_config = {"str_strip_whitespace": True}


class RouteDecision(BaseModel):
    """Structured output of the Router Agent (REQ-RTR-02). All fields required for strict JSON schema."""

    intent: Intent
    confidence: float = Field(..., description="0.0-1.0, self-assessed; heuristic signal only")
    language: str = Field(..., description="ISO 639-1 code of the user's message, e.g. 'en', 'pt'")
    consult_knowledge_base: bool = Field(
        ..., description="For customer_support: true when official troubleshooting/product docs would help"
    )
    retrieval_query: str = Field(
        ..., description="Search query in Brazilian Portuguese capturing the information need (query rewriting for the PT knowledge base)"
    )
    rationale: str = Field(..., description="One short sentence explaining the decision")


class KnowledgeAnswer(BaseModel):
    """Structured output of RAG generation: the answer plus a self-check used for handoff (GR-GROUND-01)."""

    answer: str
    answered_from_context: bool = Field(
        ..., description="false when the context does not contain the answer to the main question"
    )


class Source(BaseModel):
    title: str
    url: str
    score: float | None = None


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = {}


class Handoff(BaseModel):
    required: bool = False
    ticket_id: str | None = None
    reason: str | None = None


class GuardrailReport(BaseModel):
    pii_redacted: bool = False
    flags: list[str] = []


class TraceStep(BaseModel):
    step: str
    detail: str
    duration_ms: int = 0


class AgentResult(BaseModel):
    """What every specialist agent returns to the orchestrator."""

    agent: AgentName
    answer: str
    sources: list[Source] = []
    tools_used: list[ToolCall] = []
    needs_handoff: bool = False
    handoff_reason: str | None = None


class ChatResponse(BaseModel):
    request_id: str
    user_id: str
    answer: str
    agent: AgentName
    intent: Intent | None
    confidence: float | None
    language: str | None
    sources: list[Source]
    tools_used: list[ToolCall]
    handoff: Handoff
    guardrails: GuardrailReport
    trace: list[TraceStep]
    latency_ms: int
