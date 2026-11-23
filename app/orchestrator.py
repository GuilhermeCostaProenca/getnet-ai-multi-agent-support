"""Orchestrator: the per-request state machine and the data flow between agents.

Agents never call each other: the orchestrator calls them with typed inputs, collects typed `AgentResult`s and
records every step in the trace (REQ-ORC-01, ORC-02..06, HITL-02).
"""

import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
from dataclasses import dataclass, field
from typing import Any

from app import guardrails
from app.agents.handoff import HandoffService
from app.agents.knowledge import KnowledgeAgent
from app.agents.planner import Planner
from app.agents.policy import guess_language, policy_text
from app.agents.support import SupportAgent
from app.llm import LLMClient, start_meter
from app.memory import ConversationMemory
from app.observability import Metrics
from app.schemas import (IN_SCOPE, AgentResult, ChatRequest, ChatResponse, GuardrailReport, Handoff, Plan, Source,
                         Task, ToolCall, TraceStep)
from app.storage import Store

log = logging.getLogger("app.chat")

_TASK_STEP = {"product_knowledge": "knowledge_agent.rag", "general_info": "knowledge_agent.web",
              "customer_support": "support_agent"}


class _Trace:
    def __init__(self) -> None:
        self.steps: list[TraceStep] = []

    @contextmanager
    def step(self, name: str):
        started = time.perf_counter()
        holder = {"detail": ""}
        try:
            yield holder
        finally:
            self.steps.append(TraceStep(step=name, detail=holder["detail"],
                                        duration_ms=int((time.perf_counter() - started) * 1000)))

    def add(self, name: str, detail: str) -> None:
        self.steps.append(TraceStep(step=name, detail=detail))


@dataclass
class _Outcome:
    answer: str
    agents: list[str]
    plan: Plan | None = None
    sources: list[Source] = field(default_factory=list)
    tools: list[ToolCall] = field(default_factory=list)
    handoff: Handoff = field(default_factory=Handoff)


def _join(*parts: str) -> str:
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


class Orchestrator:
    def __init__(self, llm: LLMClient, store: Store, planner: Planner, knowledge: KnowledgeAgent,
                 support: SupportAgent, handoff: HandoffService, *, min_confidence: float = 0.55,
                 max_clarifications: int = 1, history_window: int = 10, moderation_enabled: bool = True,
                 metrics: Metrics | None = None):
        self._llm = llm
        self._store = store
        self._planner = planner
        self._knowledge = knowledge
        self._support = support
        self._handoff = handoff
        self._min_confidence = min_confidence
        self._max_clarifications = max_clarifications
        self._window = history_window
        self._moderation = moderation_enabled
        self._metrics = metrics or Metrics()
        self._memory = ConversationMemory(store, llm, history_window)

    # --- entry point -----------------------------------------------------------------------------------
    def handle(self, user_id: str, req: ChatRequest) -> ChatResponse:
        started = time.perf_counter()
        request_id = uuid.uuid4().hex
        meter = start_meter()
        trace = _Trace()
        conv = self._store.open_conversation(req.conversation_id, user_id)  # foreign id -> NotFound (404)
        cid = conv["id"]

        with trace.step("input_guardrails") as s:
            check = guardrails.check_input(req.message)
            s["detail"] = f"flags={check.flags or 'none'}"
        with trace.step("memory") as s:
            history, history_msgs = self._memory.context(conv)
            s["detail"] = f"window={len(history_msgs)} summary={'summary of earlier' in history}"
        self._store.add_message(cid, "user", check.text)  # only the redacted text is ever persisted
        input_flags = list(check.flags)

        if conv["status"] == "awaiting_human":  # HITL-02: the human owns the conversation; no agent runs
            ticket = self._store.active_ticket(cid)
            ticket_id = ticket["id"] if ticket else "-"
            lang = guess_language(check.text)
            replies = [m["content"] for m in history_msgs if m["role"] == "operator"]
            trace.add("conversation_paused", f"ticket={ticket_id} operator_replies={len(replies)}")
            outcome = _Outcome(_join(policy_text("awaiting_human", lang, ticket=ticket_id),
                                     policy_text("specialist_reply", lang, reply=replies[-1]) if replies else ""),
                               ["handoff"], handoff=Handoff(required=True, ticket_id=ticket_id, reason="awaiting_human"))
        elif check.blocked:
            outcome = _Outcome(policy_text("blocked", guess_language(check.text)), ["guardrail"])
        else:
            outcome = None
            if self._moderation:
                with trace.step("moderation") as s:
                    try:
                        flagged = self._llm.moderate(check.text)
                        s["detail"] = f"flagged={flagged}"
                        if flagged:
                            input_flags.append("moderation_flagged")
                            outcome = _Outcome(policy_text("unsafe", guess_language(check.text)), ["guardrail"])
                    except Exception as exc:  # noqa: BLE001 - fail open, the planner still screens `unsafe`
                        input_flags.append("moderation_unavailable")
                        s["detail"] = f"unavailable: {type(exc).__name__}"
            if outcome is None:
                outcome = self._plan_and_run(cid, user_id, check.text, history, history_msgs, trace)

        language = outcome.plan.language if outcome.plan else guess_language(check.text)
        answer, output_flags = guardrails.check_output(outcome.answer)
        if check.pii_redacted:
            answer += policy_text("pii_warning", language)
        self._store.add_message(cid, "assistant", answer, agent=",".join(outcome.agents))
        status = self._store.get_conversation(cid)["status"]

        response = ChatResponse(
            request_id=request_id, conversation_id=cid, user_id=user_id, status=status, answer=answer,
            agents=outcome.agents, intents=[t.intent for t in outcome.plan.tasks] if outcome.plan else [],
            confidence=outcome.plan.confidence if outcome.plan else None, language=language,
            sources=outcome.sources, tools_used=outcome.tools, handoff=outcome.handoff,
            guardrails=GuardrailReport(input_flags=input_flags, output_flags=output_flags), usage=meter.usage,
            trace=trace.steps, latency_ms=int((time.perf_counter() - started) * 1000))
        self._record_metrics(response)
        log.info("chat_completed", extra={
            "request_id": request_id, "conversation_id": cid, "user_id": user_id, "message_redacted": check.text[:200],
            "agents": response.agents, "intents": response.intents, "confidence": response.confidence,
            "tools": [t.name for t in response.tools_used], "sources": len(response.sources),
            "handoff": response.handoff.required, "status": status, "input_flags": input_flags,
            "output_flags": output_flags, "llm_calls": meter.usage.llm_calls, "input_tokens": meter.usage.input_tokens,
            "output_tokens": meter.usage.output_tokens, "latency_ms": response.latency_ms})
        return response

    # --- planning and dispatch -------------------------------------------------------------------------
    def _plan_and_run(self, cid: str, user_id: str, text: str, history: str, history_msgs: list[dict[str, Any]],
                      trace: _Trace) -> _Outcome:
        plan: Plan | None = None
        with trace.step("planner") as s:
            try:
                plan = self._planner.plan(text, history)
                s["detail"] = (f"tasks={[t.intent for t in plan.tasks]} conf={plan.confidence:.2f} "
                               f"clarify={plan.needs_clarification} lang={plan.language}")
            except Exception as exc:  # noqa: BLE001
                s["detail"] = f"failed: {type(exc).__name__}"
        if plan is None:  # escalate after the planner step is closed, so the trace stays in order
            return self._escalate(cid, user_id, "planner_failure", guess_language(text), trace, None)

        lang = plan.language
        if any(t.intent == "unsafe" for t in plan.tasks):
            return _Outcome(policy_text("unsafe", lang), ["guardrail"], plan)
        if plan.wants_human:  # an explicit request for a person always reaches one (HITL-01)
            return self._escalate(cid, user_id, "user_requested_human", lang, trace, plan,
                                  {"intents": [t.intent for t in plan.tasks]})

        if plan.needs_clarification or not plan.tasks or plan.confidence < self._min_confidence:  # ORC-03
            asked = self._trailing_clarifications(history_msgs)
            if asked >= self._max_clarifications:
                return self._escalate(cid, user_id, "unresolved_ambiguity", lang, trace, plan)
            trace.add("clarify", f"clarification {asked + 1}/{self._max_clarifications}")
            question = plan.clarification_question.strip() or policy_text("clarify", lang)
            return _Outcome(question, ["clarify"], plan)

        in_scope = [t for t in plan.tasks if t.intent in IN_SCOPE]
        out_of_scope = [t for t in plan.tasks if t.intent == "out_of_scope"]
        if not in_scope:
            return _Outcome(policy_text("out_of_scope", lang), ["guardrail"], plan)

        results = self._run_tasks(user_id, in_scope, lang, history, trace)  # ORC-05
        outcome = _Outcome(
            _join(*(r.answer for r in results), policy_text("partial_out_of_scope", lang) if out_of_scope else ""),
            list(dict.fromkeys(r.agent for r in results)), plan,
            sources=[s for r in results for s in r.sources], tools=[t for r in results for t in r.tools_used])
        failed = [r for r in results if r.needs_handoff]
        if failed:
            details = {"intents": [t.intent for t in plan.tasks], "tools_used": [t.name for t in outcome.tools],
                       "pending_actions": [a for r in results for a in r.pending_actions]}
            esc = self._escalate(cid, user_id, failed[0].handoff_reason or "agent_request", lang, trace, plan, details)
            outcome.answer = _join(outcome.answer, esc.answer)
            outcome.agents.append("escalation")
            outcome.handoff = esc.handoff
        return outcome

    def _run_tasks(self, user_id: str, tasks: list[Task], lang: str, history: str, trace: _Trace) -> list[AgentResult]:
        if len(tasks) == 1:
            return [self._run_task(user_id, tasks[0], lang, history, trace)]
        with ThreadPoolExecutor(max_workers=len(tasks)) as pool:  # contexts copied so token metering follows
            futures = [pool.submit(copy_context().run, self._run_task, user_id, t, lang, history, trace) for t in tasks]
            return [f.result() for f in futures]

    def _run_task(self, user_id: str, task: Task, lang: str, history: str, trace: _Trace) -> AgentResult:
        with trace.step(_TASK_STEP[task.intent]) as s:
            try:
                if task.intent == "product_knowledge":
                    result = self._knowledge.answer_product(task, lang, history)
                elif task.intent == "general_info":
                    result = self._knowledge.answer_general(task, lang)
                else:  # customer_support, optionally preceded by KB retrieval (agent sequence)
                    kb_context, kb_sources = "", []
                    if task.consult_knowledge_base:
                        hits = self._knowledge.retrieve(task.retrieval_query, task.query)
                        kb_context, kb_sources = self._knowledge.format_context(hits), self._knowledge.to_sources(hits)
                    result = self._support.handle(user_id, task, lang, history, kb_context, kb_sources)
                s["detail"] = result.detail
                return result
            except Exception as exc:  # noqa: BLE001 - ORC-06: degrade to a handoff, never a 500
                log.exception("agent_failure")
                s["detail"] = f"failed: {type(exc).__name__}"
                agent = "support" if task.intent == "customer_support" else "knowledge"
                return AgentResult(agent=agent, intent=task.intent, answer="", needs_handoff=True,
                                   handoff_reason=f"agent_failure:{type(exc).__name__}")

    def _escalate(self, cid: str, user_id: str, reason: str, lang: str | None, trace: _Trace, plan: Plan | None,
                  details: dict[str, Any] | None = None) -> _Outcome:
        with trace.step("escalation_agent") as s:
            text, handoff = self._handoff.escalate(cid, user_id, reason, lang, details)
            s["detail"] = f"ticket={handoff.ticket_id} reason={reason}"
        return _Outcome(text, ["escalation"], plan, handoff=handoff)

    def _record_metrics(self, r: ChatResponse) -> None:
        m = self._metrics
        m.inc("chat_requests_total", {"agents": "+".join(r.agents)})
        for intent in r.intents:
            m.inc("planned_tasks_total", {"intent": intent})
        if r.handoff.required:
            m.inc("handoffs_total", {"reason": r.handoff.reason or "unknown"})
        for flag in r.guardrails.input_flags + r.guardrails.output_flags:
            m.inc("guardrail_flags_total", {"flag": flag})
        m.inc("llm_calls_total", value=r.usage.llm_calls)
        m.inc("llm_tokens_total", {"type": "input"}, r.usage.input_tokens)
        m.inc("llm_tokens_total", {"type": "output"}, r.usage.output_tokens)
        m.observe_latency(r.latency_ms)

    @staticmethod
    def _trailing_clarifications(history_msgs: list[dict[str, Any]]) -> int:
        count = 0
        for m in reversed(history_msgs):
            if m["role"] != "assistant":
                continue
            if m["agent"] != "clarify":
                break
            count += 1
        return count
