# 02 — Architecture (v3)

## Components
```
client ──JWT──▶ FastAPI (/v1/chat, /v1/conversations, /v1/operator/*, /metrics, /health)
                   │ auth · rate limit · idempotency
                   ▼
              Orchestrator (explicit state machine per conversation: active ⇄ awaiting_human)
   input guardrails → memory (window + summary) → Planner (structured Plan)
        ├─ unsafe / refusal ─────────────▶ policy refusal
        ├─ wants_human / ambiguity ──────▶ Escalation agent ──▶ ticket (SQLite) ──▶ Operator API
        └─ tasks (parallel) ─┬─ product_knowledge ▶ Knowledge agent: BM25 RAG → Getnet web search → handoff
                             ├─ general_info ─────▶ Knowledge agent: Claude web search (sources)
                             ├─ customer_support ─▶ Support agent: tool loop → reviewer → approval gate
                             └─ out_of_scope ─────▶ partial refusal
   compose → output guardrail → persist → metrics/logs → response (answer, sources, tools, handoff, trace, usage)
```

## Request lifecycle
1. Authenticate (JWT) and rate-limit; optional idempotent replay.
2. Open/validate the conversation (owner check → 404).
3. Input guardrails (injection block, PII redaction); only redacted text is stored or sent to the model.
4. If the conversation is `awaiting_human`: answer with the ticket status and the operator's latest reply, **no LLM call**.
5. Memory: last N messages + rolling summary.
6. Planner → plan; dispatch by policy; tasks in parallel; compose; escalate if any task needs a human.
7. Output guardrail; persist; record metrics; return.

## ADRs
| ID | Decision | Why / trade-off |
|---|---|---|
| ADR-01 | Custom orchestrator (no LangGraph/CrewAI) | Explicit, testable state machine; every branch covered with a fake LLM. Trade-off: no built-in graph tooling. |
| ADR-02 | Claude `claude-opus-5-5` via the Messages API; JSON-schema structured outputs for every agent contract; `effort: low` | One provider (CON-02); validated contracts; low effort keeps latency down for routing/extraction calls. Model configurable. |
| ADR-03 | Retrieval = BM25 implemented in-house over chunks stored in SQLite | No embeddings API under CON-02; BM25 is explainable and dependency-free (lean image). Mitigations: planner's Portuguese rewrite + original wording (multi-query), product-page boost. Trade-off: weaker on paraphrases than dense retrieval. |
| ADR-04 | Safety without a moderation API | Regex injection screen + PII redaction before the model; planner `unsafe` intent; provider refusal → policy text; output PII guard. |
| ADR-05 | JWT HS256 + roles; identity bound server-side into tools | The LLM cannot choose whose data it reads. |
| ADR-06 | SQLite for conversations, messages, tickets, idempotency and chunks | Zero-ops persistence that survives restarts; swap for Postgres in production. |
| ADR-07 | HITL as data: ticket + paused conversation + operator API; approval gate for money-moving actions | Humans own the conversation while a ticket is open; the model proposes, a human disposes. |
| ADR-08 | Web search via Claude's server tool; Getnet questions replan to a site-restricted search | Fresh, cited answers; bounded to one replan. |
| ADR-09 | Evaluation = labelled JSONL + harness + LLM judge + regression gate; deterministic suite in CI | Measures routing, tools, refusals, retrieval, consistency; the judge is a signal, not ground truth. |
| ADR-10 | Simulated customer data labelled `MOCK` | No real backend; never presented as real customer data. |
| ADR-11 | Console = one static HTML/JS page served by FastAPI | No build step or extra container; talks to the same public API (no backdoor). |
| ADR-12 | Re-ranking with Claude over BM25 candidates | Recovers paraphrase matches BM25 misses; costs one LLM call per KB question; falls back to BM25 order on error. |
| ADR-13 | OpenTelemetry API with optional OTLP exporter | Standard spans without forcing a collector; no-op when not configured. |
