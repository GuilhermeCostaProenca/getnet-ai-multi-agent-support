"""Typed contracts for the API and between agents (specs/03-api-contract.md)."""

from typing import Any, Literal

from pydantic import BaseModel, Field

Intent = Literal["product_knowledge", "general_info", "customer_support", "out_of_scope", "unsafe"]
Role = Literal["merchant", "operator"]
ConversationStatus = Literal["active", "awaiting_human"]
IN_SCOPE: tuple[Intent, ...] = ("product_knowledge", "general_info", "customer_support")


# --- API -------------------------------------------------------------------------------------------

class ChatRequest(BaseModel):
    model_config = {"str_strip_whitespace": True}

    message: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(None, max_length=64, description="Optional; must equal the token subject (SEC-02)")
    conversation_id: str | None = Field(None, pattern=r"^c_[a-f0-9]{16}$", description="Omit to start a conversation")


class Source(BaseModel):
    title: str
    url: str
    score: float | None = None


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = {}


class TraceStep(BaseModel):
    step: str
    detail: str = ""
    duration_ms: int = 0


class Handoff(BaseModel):
    required: bool = False
    ticket_id: str | None = None
    reason: str | None = None


class GuardrailReport(BaseModel):
    input_flags: list[str] = []
    output_flags: list[str] = []


class Usage(BaseModel):
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class ChatResponse(BaseModel):
    request_id: str
    conversation_id: str
    user_id: str
    status: ConversationStatus
    answer: str
    agents: list[str]
    intents: list[Intent]
    confidence: float | None
    language: str | None
    sources: list[Source]
    tools_used: list[ToolCall]
    handoff: Handoff
    guardrails: GuardrailReport
    usage: Usage
    trace: list[TraceStep]
    latency_ms: int


class MessageView(BaseModel):
    role: str
    content: str
    agent: str | None = None
    created_at: str


class ConversationView(BaseModel):
    conversation_id: str
    status: ConversationStatus
    messages: list[MessageView]


class TicketView(BaseModel):
    ticket_id: str
    conversation_id: str
    user_id: str
    status: Literal["open", "claimed", "resolved"]
    reason: str
    context: dict[str, Any]
    operator_id: str | None
    resolution: str | None
    created_at: str
    updated_at: str


class OperatorReply(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)


class ResolveRequest(BaseModel):
    resolution: str = Field(..., min_length=1, max_length=2000)


class DevTokenRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")
    role: Role = "merchant"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# --- Agent contracts (structured LLM outputs: every field required for strict JSON schema) ----------

class Task(BaseModel):
    intent: Intent
    query: str = Field(..., description="Self-contained rewrite of this part of the request, resolving references to earlier turns")
    retrieval_query: str = Field(..., description="Short Brazilian Portuguese search query for the Getnet knowledge base")
    consult_knowledge_base: bool = Field(..., description="customer_support only: official docs would complement the user's data")


class Plan(BaseModel):
    language: str = Field(..., description="ISO 639-1 code of the user's message")
    confidence: float = Field(..., description="0.0-1.0, heuristic self-assessment")
    needs_clarification: bool
    clarification_question: str
    tasks: list[Task]
    rationale: str


class KnowledgeAnswer(BaseModel):
    answer: str
    answered_from_context: bool = Field(..., description="false only when the context gives no concrete fact answering the question")


class AgentResult(BaseModel):
    agent: str
    intent: Intent | None = None
    answer: str
    sources: list[Source] = []
    tools_used: list[ToolCall] = []
    needs_handoff: bool = False
    handoff_reason: str | None = None
    detail: str = ""
