"""Orchestrator (ADR-01): explicit per-conversation state machine (active ⇄ awaiting_human), guardrails, memory,
planning, parallel dispatch, partial refusals, escalation, trace, metrics and spans for every request."""

import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
from dataclasses import dataclass, field
from typing import Any

from app import guardrails
from app.agents.base import AgentResult, guess_language, policy
from app.agents.escalation import HandoffService
from app.agents.knowledge import KnowledgeAgent
from app.agents.planner import Planner
from app.agents.support import SupportAgent
from app.llm import ModelRefusal, start_meter
from app.memory import ConversationMemory
from app.observability import Metrics
from app.schemas import (IN_SCOPE, ChatRequest, ChatResponse, GuardrailReport, Handoff, Plan, Source, Task, ToolCall,
                         TraceStep)
from app.storage import Store
from app.telemetry import span

log = logging.getLogger("app.chat")


class Trace:
    def __init__(self) -> None:
        self.steps: list[TraceStep] = []

    @contextmanager
    def step(self, name: str):
        started = time.perf_counter()
        holder = {"detail": ""}
        with span(f"step.{name}"):
            try:
                yield holder
            finally:
                self.steps.append(TraceStep(step=name, detail=holder["detail"],
                                            duration_ms=int((time.perf_counter() - started) * 1000)))

    def add(self, name: str, detail: str) -> None:
        self.steps.append(TraceStep(step=name, detail=detail))


@dataclass
class Outcome:
    answer: str
    agents: list[str]
    plan: Plan | None = None
    sources: list[Source] = field(default_factory=list)
    tools: list[ToolCall] = field(default_factory=list)
    handoff: Handoff = field(default_factory=Handoff)


def _join(*parts: str) -> str:
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


class Orchestrator:
    def __init__(self, store: Store, planner: Planner, knowledge: KnowledgeAgent, support: SupportAgent,
                 handoff: HandoffService, memory: ConversationMemory, metrics: Metrics, *,
                 min_confidence: float = 0.55, max_clarifications: int = 1):
        self._store = store
        self._planner = planner
        self._knowledge = knowledge
        self._support = support
        self._handoff = handoff
        self._memory = memory
        self._metrics = metrics
        self._min_confidence = min_confidence
        self._max_clarifications = max_clarifications

    def handle(self, user_id: str, req: ChatRequest) -> ChatResponse:
        started = time.perf_counter()
        request_id = uuid.uuid4().hex
        meter = start_meter()
        trace = Trace()
        with span("chat.request", user_id=user_id):
            conv = self._store.open_conversation(req.conversation_id, user_id)  # NotFound → 404 (MEM-01)
            cid = conv["id"]
            with trace.step("input_guardrails") as s:
                check = guardrails.check_input(req.message)
                s["detail"] = f"flags={check.flags or 'none'}"
            with trace.step("memory") as s:
                history, recent = self._memory.context(conv)
                s["detail"] = f"window={len(recent)} summary={'yes' if conv['summary'] else 'no'}"
            self._store.add_message(cid, "user", check.text)  # only redacted text is stored (SEC-04)

            if conv["status"] == "awaiting_human":  # HITL-02: the human owns the conversation, no agent runs
                ticket = self._store.active_ticket(cid)
                tid = ticket["id"] if ticket else "-"
                lang = guess_language(check.text)
                replies = [m["content"] for m in recent if m["role"] == "operator"]
                trace.add("conversation_paused", f"ticket={tid} operator_replies={len(replies)}")
                outcome = Outcome(_join(policy("awaiting_human", lang, ticket=tid),
                                        policy("specialist_reply", lang, reply=replies[-1]) if replies else ""),
                                  ["handoff"], handoff=Handoff(required=True, ticket_id=tid, reason="awaiting_human"))
            elif check.blocked:
                outcome = Outcome(policy("blocked", guess_language(check.text)), ["guardrail"])
            else:
                outcome = self._plan_and_run(conv, user_id, check.text, history, trace)

            language = outcome.plan.language if outcome.plan else guess_language(check.text)
            with trace.step("output_guardrails") as s:
                answer, output_flags = guardrails.check_output(outcome.answer)
                s["detail"] = f"flags={output_flags or 'none'}"
            if check.pii_redacted:
                answer += policy("pii_warning", language)
            self._store.add_message(cid, "assistant", answer, agent=",".join(outcome.agents))
            status = self._store.get_conversation(cid)["status"]
            response = ChatResponse(
                request_id=request_id, conversation_id=cid, user_id=user_id, status=status, answer=answer,
                agents=outcome.agents, intents=[t.intent for t in outcome.plan.tasks] if outcome.plan else [],
                confidence=outcome.plan.confidence if outcome.plan else None, language=language,
                sources=outcome.sources, tools_used=outcome.tools, handoff=outcome.handoff,
                guardrails=GuardrailReport(input_flags=check.flags, output_flags=output_flags), usage=meter.usage,
                trace=trace.steps, latency_ms=int((time.perf_counter() - started) * 1000))
        self._record(response)
        return response

    # --- planning & dispatch ---------------------------------------------------------------------------
    def _plan_and_run(self, conv: dict[str, Any], user_id: str, text: str, history: str, trace: Trace) -> Outcome:
        cid = conv["id"]
        plan: Plan | None = None
        refused = False
        with trace.step("planner") as s:
            try:
                plan = self._planner.plan(text, history)
                s["detail"] = (f"tasks={[t.intent for t in plan.tasks]} conf={plan.confidence:.2f} "
                               f"clarify={plan.needs_clarification} human={plan.wants_human} lang={plan.language}")
            except ModelRefusal:  # SEC-05: a provider refusal is a policy refusal, not a failure
                refused = True
                s["detail"] = "model_refusal → unsafe"
            except Exception as exc:  # noqa: BLE001
                s["detail"] = f"failed: {type(exc).__name__}"
        lang = plan.language if plan else guess_language(text)
        if refused or (plan and any(t.intent == "unsafe" for t in plan.tasks)):
            if plan is None:
                plan = Plan(language=lang, confidence=1.0, needs_clarification=False, clarification_question="",
                            wants_human=False, rationale="model refusal",
                            tasks=[Task(intent="unsafe", query=text, retrieval_query="")])
            return Outcome(policy("unsafe", lang), ["guardrail"], plan=plan)
        if plan is None:
            return self._escalate(cid, user_id, "planner_failure", lang, trace, None, {})
        if plan.wants_human:  # HITL-04
            return self._escalate(cid, user_id, "user_requested_human", lang, trace, plan, {})

        in_scope = [t for t in plan.tasks if t.intent in IN_SCOPE]
        out_scope = [t for t in plan.tasks if t.intent == "out_of_scope"]
        if plan.needs_clarification or plan.confidence < self._min_confidence or not plan.tasks:  # ORC-03
            asked = conv["clarifications"]
            if asked >= self._max_clarifications:
                return self._escalate(cid, user_id, "unresolved_ambiguity", lang, trace, plan, {})
            self._store.update_conversation(cid, clarifications=asked + 1)
            trace.add("clarify", f"clarification {asked + 1}/{self._max_clarifications}")
            question = plan.clarification_question or ("Pode dar mais detalhes?" if lang == "pt" else "Could you give more details?")
            return Outcome(question, ["planner"], plan=plan)
        if not in_scope:
            return Outcome(policy("out_of_scope", lang), ["guardrail"], plan=plan)
        if conv["clarifications"]:
            self._store.update_conversation(cid, clarifications=0)

        results = self._run_tasks(user_id, in_scope, lang, history, trace)  # ORC-06
        answer = _join(*(r.answer for r in results), policy("partial_out_of_scope", lang) if out_scope else "")
        sources = [s for r in results for s in r.sources]
        tools = [t for r in results for t in r.tools]
        agents = list(dict.fromkeys(r.agent for r in results))
        outcome = Outcome(answer, agents, plan=plan, sources=sources, tools=tools)
        failed = [r for r in results if r.needs_handoff]
        if failed:
            details = {"intents": [t.intent for t in plan.tasks], "tools_used": [t.name for t in tools],
                       "pending_actions": [a for r in failed for a in r.pending_actions]}
            esc = self._escalate(cid, user_id, failed[0].handoff_reason or "agent_request", lang, trace, plan, details)
            outcome.answer = _join(answer, esc.answer)
            outcome.agents = agents + ["escalation"]
            outcome.handoff = esc.handoff
        return outcome

    def _run_tasks(self, user_id: str, tasks: list[Task], lang: str, history: str, trace: Trace) -> list[AgentResult]:
        if len(tasks) == 1:
            return [self._run_task(user_id, tasks[0], lang, history, trace)]
        with ThreadPoolExecutor(len(tasks)) as pool:
            futures = [pool.submit(copy_context().run, self._run_task, user_id, t, lang, history, trace) for t in tasks]
            return [f.result() for f in futures]

    def _run_task(self, user_id: str, task: Task, lang: str, history: str, trace: Trace) -> AgentResult:
        name = {"product_knowledge": "knowledge_agent.rag", "general_info": "knowledge_agent.web",
                "customer_support": "support_agent"}[task.intent]
        with trace.step(name) as s:
            try:
                if task.intent == "product_knowledge":
                    result = self._knowledge.answer_product(task, lang, history)
                elif task.intent == "general_info":
                    result = self._knowledge.answer_general(task, lang)
                else:
                    result = self._support.handle(user_id, task, lang, history)
            except Exception as exc:  # noqa: BLE001 - AC-34: an agent failure becomes a handoff, not a 500
                log.exception("agent_failed", extra={"intent": task.intent})
                result = AgentResult(name.split(".")[0], task.intent, "", needs_handoff=True,
                                     handoff_reason=f"agent_failure:{type(exc).__name__}")
            s["detail"] = result.detail or (result.handoff_reason or "")
        return result

    def _escalate(self, cid: str, user_id: str, reason: str, lang: str, trace: Trace, plan: Plan | None,
                  details: dict[str, Any]) -> Outcome:
        with trace.step("escalation_agent") as s:
            tid = self._handoff.escalate(cid, user_id, reason, lang, details)
            s["detail"] = f"ticket={tid} reason={reason}"
        return Outcome(policy("escalated", lang, ticket=tid), ["escalation"], plan=plan,
                       handoff=Handoff(required=True, ticket_id=tid, reason=reason))

    def _record(self, r: ChatResponse) -> None:
        self._metrics.inc("chat_requests_total", agents=",".join(r.agents))
        for intent in r.intents:
            self._metrics.inc("planned_tasks_total", intent=intent)
        if r.handoff.required:
            self._metrics.inc("handoffs_total", reason=r.handoff.reason or "-")
        for flag in r.guardrails.input_flags + r.guardrails.output_flags:
            self._metrics.inc("guardrail_flags_total", flag=flag)
        self._metrics.inc("llm_calls_total", r.usage.llm_calls)
        self._metrics.inc("llm_tokens_total", r.usage.input_tokens, type="input")
        self._metrics.inc("llm_tokens_total", r.usage.output_tokens, type="output")
        self._metrics.observe_latency(r.latency_ms)
        log.info("chat_completed", extra={
            "request_id": r.request_id, "conversation_id": r.conversation_id, "user_id": r.user_id,
            "agents": r.agents, "intents": r.intents, "tools": [t.name for t in r.tools_used],
            "sources": len(r.sources), "handoff": r.handoff.reason, "status": r.status,
            "flags": r.guardrails.input_flags + r.guardrails.output_flags, "llm_calls": r.usage.llm_calls,
            "input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens, "latency_ms": r.latency_ms})
