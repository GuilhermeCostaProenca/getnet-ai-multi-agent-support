"""API models (03-api-contract) and the structured contracts every agent returns (ORC-01)."""

from typing import Any, Literal

from pydantic import BaseModel, Field

Intent = Literal["product_knowledge", "general_info", "customer_support", "out_of_scope", "unsafe"]
IN_SCOPE = ("product_knowledge", "general_info", "customer_support")
Role = Literal["merchant", "operator"]


# --- API --------------------------------------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(None, max_length=64)
    conversation_id: str | None = Field(None, pattern=r"^c_[a-f0-9]{16}$")


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
    status: Literal["active", "awaiting_human"]
    answer: str
    agents: list[str]
    intents: list[str]
    confidence: float | None
    language: str
    sources: list[Source] = []
    tools_used: list[ToolCall] = []
    handoff: Handoff = Handoff()
    guardrails: GuardrailReport = GuardrailReport()
    usage: Usage = Usage()
    trace: list[TraceStep] = []
    latency_ms: int = 0


class MessageView(BaseModel):
    role: str
    content: str
    agent: str | None = None
    created_at: str


class ConversationView(BaseModel):
    conversation_id: str
    status: str
    messages: list[MessageView]


class TicketView(BaseModel):
    ticket_id: str
    conversation_id: str
    user_id: str
    status: str
    reason: str
    context: dict[str, Any]
    operator_id: str | None = None
    created_at: str
    updated_at: str


class OperatorReply(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)


class ResolveRequest(BaseModel):
    resolution: str = Field("resolved", max_length=500)


class DevTokenRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")
    role: Role = "merchant"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# --- agent contracts (validated JSON-schema outputs, ADR-02) ------------------------------------------
class Task(BaseModel):
    intent: Intent
    query: str = Field(..., description="Self-contained version of this part of the request (resolve references from history)")
    retrieval_query: str = Field(..., description="Portuguese keyword query for Getnet's Portuguese pages; '' if not product_knowledge")


class Plan(BaseModel):
    language: str = Field(..., description="ISO 639-1 code of the user's message, e.g. pt or en")
    confidence: float = Field(..., description="0.0-1.0 confidence that the tasks capture the request")
    needs_clarification: bool
    clarification_question: str = Field(..., description="'' unless needs_clarification")
    wants_human: bool = Field(..., description="true only when the user explicitly asks for a human attendant")
    tasks: list[Task]
    rationale: str


class KnowledgeAnswer(BaseModel):
    answer: str
    answered_from_context: bool


class Rerank(BaseModel):
    order: list[int] = Field(..., description="Excerpt numbers, most relevant first; omit irrelevant ones")


class Review(BaseModel):
    consistent: bool
    issues: str


class Revision(BaseModel):
    answer: str


class Summary(BaseModel):
    summary: str
