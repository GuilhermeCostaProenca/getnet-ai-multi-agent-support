"""Orchestrator: owns the workflow and the data flow between agents (REQ-ORC-01/02).

Agents never call each other; the orchestrator calls them with typed inputs and collects
typed `AgentResult`s, recording every step in the trace.
"""

import logging
import time
import uuid
from contextlib import contextmanager

from app import guardrails
from app.agents.escalation import EscalationAgent
from app.agents.knowledge import KnowledgeAgent
from app.agents.policy import policy_text
from app.agents.router import RouterAgent
from app.agents.support import SupportAgent
from app.llm import LLMClient
from app.schemas import AgentResult, ChatRequest, ChatResponse, GuardrailReport, Handoff, RouteDecision, TraceStep

log = logging.getLogger("app.chat")


class Orchestrator:
    def __init__(self, llm: LLMClient, router: RouterAgent, knowledge: KnowledgeAgent, support: SupportAgent,
                 escalation: EscalationAgent, min_confidence: float = 0.55, moderation_enabled: bool = True):
        self._llm = llm
        self.router = router
        self.knowledge = knowledge
        self.support = support
        self.escalation = escalation
        self._min_confidence = min_confidence
        self._moderation_enabled = moderation_enabled

    def handle(self, req: ChatRequest) -> ChatResponse:
        started = time.perf_counter()
        request_id = uuid.uuid4().hex
        trace: list[TraceStep] = []

        @contextmanager
        def step(name: str):
            t = time.perf_counter()
            holder = {"detail": ""}
            try:
                yield holder
            finally:
                trace.append(TraceStep(step=name, detail=holder["detail"], duration_ms=int((time.perf_counter() - t) * 1000)))

        # 1. Deterministic guardrails (no LLM)
        with step("input_guardrails") as s:
            check = guardrails.check_input(req.message)
            s["detail"] = f"flags={check.flags or 'none'}"
        flags = list(check.flags)
        decision: RouteDecision | None = None
        handoff = Handoff()

        if check.blocked:
            result = AgentResult(agent="guardrail", answer=policy_text("blocked", None))
        else:
            result = None
            # 2. Moderation classifier (fails open, ADR-08)
            if self._moderation_enabled:
                with step("moderation") as s:
                    try:
                        if self._llm.moderate(check.text):
                            flags.append("moderation_flagged")
                            result = AgentResult(agent="guardrail", answer=policy_text("unsafe", None))
                        s["detail"] = f"flagged={'moderation_flagged' in flags}"
                    except Exception as exc:  # noqa: BLE001
                        flags.append("moderation_unavailable")
                        s["detail"] = f"unavailable: {type(exc).__name__}"

            if result is None:
                result, decision, handoff = self._route_and_dispatch(req.user_id, check.text, step)

        if check.pii_redacted:
            result.answer += policy_text("pii_warning", decision.language if decision else None)

        response = ChatResponse(
            request_id=request_id, user_id=req.user_id, answer=result.answer, agent=result.agent,
            intent=decision.intent if decision else None, confidence=decision.confidence if decision else None,
            language=decision.language if decision else None, sources=result.sources, tools_used=result.tools_used,
            handoff=handoff, guardrails=GuardrailReport(pii_redacted=check.pii_redacted, flags=flags),
            trace=trace, latency_ms=int((time.perf_counter() - started) * 1000),
        )
        log.info("chat_completed", extra={
            "request_id": request_id, "user_id": req.user_id, "message_redacted": check.text[:200],
            "intent": response.intent, "confidence": response.confidence, "agent": response.agent,
            "tools": [t.name for t in response.tools_used], "sources": len(response.sources),
            "handoff": handoff.required, "flags": flags, "latency_ms": response.latency_ms,
        })
        return response

    def _route_and_dispatch(self, user_id: str, text: str, step):
        # 3. Router
        decision = None
        with step("router") as s:
            try:
                decision = self.router.route(text)
                s["detail"] = f"intent={decision.intent} conf={decision.confidence:.2f} lang={decision.language}"
            except Exception as exc:  # noqa: BLE001
                s["detail"] = f"failed: {type(exc).__name__}"
        if decision is None:  # escalate after closing the router step so the trace stays in order
            return self._escalate(user_id, text, "router_failure", None, step)

        lang = decision.language
        if decision.intent in ("out_of_scope", "unsafe"):
            return AgentResult(agent="guardrail", answer=policy_text(decision.intent, lang)), decision, Handoff()
        if decision.confidence < self._min_confidence:  # GR-CONF-01
            result, _, handoff = self._escalate(user_id, text, "low_confidence", lang, step)
            return result, decision, handoff

        # 4. Specialist agent(s)
        try:
            if decision.intent == "product_knowledge":
                with step("knowledge_agent.rag") as s:
                    result = self.knowledge.answer_from_kb(text, decision.retrieval_query, lang)
                    s["detail"] = f"sources={len(result.sources)}"
            elif decision.intent == "general_info":
                with step("knowledge_agent.web_search") as s:
                    result = self.knowledge.answer_from_web(text, lang)
                    s["detail"] = f"sources={len(result.sources)}"
            else:  # customer_support, optionally preceded by KB retrieval (agent sequence)
                kb_context, kb_sources = "", []
                if decision.consult_knowledge_base:
                    with step("knowledge_agent.retrieve") as s:
                        hits = self.knowledge.retrieve(decision.retrieval_query or text)
                        kb_context, kb_sources = self.knowledge.format_context(hits), self.knowledge.to_sources(hits)
                        s["detail"] = f"hits={len(hits)}"
                with step("support_agent") as s:
                    result = self.support.handle(user_id, text, lang, kb_context, kb_sources)
                    s["detail"] = f"tools={[t.name for t in result.tools_used]}"
        except Exception as exc:  # noqa: BLE001
            log.exception("agent_failure")
            result, _, handoff = self._escalate(user_id, text, f"agent_failure:{type(exc).__name__}", lang, step)
            return result, decision, handoff

        if result.needs_handoff:
            esc, _, handoff = self._escalate(user_id, text, result.handoff_reason or "agent_request", lang, step)
            result.answer = f"{result.answer}\n\n{esc.answer}"
            return result, decision, handoff
        return result, decision, Handoff()

    def _escalate(self, user_id: str, text: str, reason: str, language: str | None, step):
        with step("escalation_agent") as s:
            result, handoff = self.escalation.escalate(user_id, text, reason, language)
            s["detail"] = f"ticket={handoff.ticket_id} reason={reason}"
        return result, None, handoff
