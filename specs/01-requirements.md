# 01 — Requirements (v2)

v2 is a rewrite from scratch driven by (1) the challenge statement and (2) the evaluation of v1 (7.3/10), whose
gaps were: caller identity not authenticated, no conversation memory, escalation without a closed human loop,
plan-once orchestration, and limited semantic/RAG evaluation. Every requirement has an ID used by the
acceptance criteria (`04-acceptance-criteria.md`) and by test names.

## Challenge (must have)

| ID | Requirement |
|---|---|
| REQ-API-01 | HTTP `POST` endpoint accepting `{"message", "user_id"}` and returning meaningful JSON (`POST /v1/chat`). |
| REQ-ORC-01 | ≥ 3 agent types (Router/Planner, Knowledge, Customer Support) with an explicit, typed communication mechanism. |
| REQ-KNW-01 | Knowledge agent answers Getnet product questions with RAG over ingested Getnet web pages, citing sources. |
| REQ-KNW-02 | Knowledge agent answers general factual questions (weather, FX) with a web search tool, citing sources. |
| REQ-SUP-01 | Customer Support agent resolves inquiries with ≥ 2 tools over the caller's (mock-labelled) data. |
| REQ-OPS-01 | Dockerfile + docker-compose; README covering build, config, run, tests, architecture, RAG, LLM usage. |

## Identity & security (v1 gap: Security 5.5)

| ID | Requirement |
|---|---|
| SEC-01 | Every business endpoint requires a Bearer JWT (HS256, issuer + expiry validated). Missing/invalid/expired → 401. |
| SEC-02 | The acting user is the token `sub`. The body `user_id` is kept for contract compatibility but must equal `sub` (else 403). Tools never receive an LLM-chosen id. |
| SEC-03 | Roles: `merchant` (chat, own conversations) and `operator` (ticket queue). Wrong role → 403. A merchant cannot read another merchant's conversation (404, no existence leak). |
| SEC-04 | Per-identity rate limit (sliding window) → 429. |
| SEC-05 | Input guardrails before any LLM call: prompt-injection patterns blocked; card (Luhn), CPF (check digits) and explicit secrets redacted. |
| SEC-06 | Output guardrail: card/CPF patterns in the final answer are redacted before it is returned or stored. |
| SEC-07 | Moderation classifier on the redacted input (fails open, logged). |

## Memory & state (v1 gap: Memory 1.5)

| ID | Requirement |
|---|---|
| MEM-01 | Multi-turn conversations: `conversation_id` returned on the first message and accepted on the next ones. |
| MEM-02 | Conversations, messages and tickets persisted in SQLite (survive restarts; Docker volume). |
| MEM-03 | The planner and agents receive a bounded window of recent history; follow-ups ("and with crediário?") are resolved into self-contained queries. |

## Human oversight (v1 gap: Human Oversight 4.0)

| ID | Requirement |
|---|---|
| HITL-01 | Escalation creates a persistent ticket with operator context (reason, intents, recent history) and pauses the conversation (`awaiting_human`). |
| HITL-02 | While paused, merchant messages are recorded for the operator and no agent runs. |
| HITL-03 | Operator API: list, claim, reply (delivered into the merchant's conversation), resolve (conversation resumes with the agents, keeping the human exchange in history). |

## Orchestration (v1 gap: plan-once)

| ID | Requirement |
|---|---|
| ORC-02 | Planner returns a structured plan with 1–3 tasks: compound requests are split; in-scope parts are answered and only out-of-scope parts are refused. |
| ORC-03 | Ambiguity → one clarifying question before any handoff; still ambiguous after the allowed clarifications → handoff. |
| ORC-04 | Observe → replan: when the knowledge base has no grounded answer, the agent retries with a web search restricted to Getnet's site before handing off. |
| ORC-05 | Independent tasks run in parallel; every step is recorded in a per-request trace. |
| ORC-06 | Any agent/LLM failure degrades to a handoff answer (HTTP 200), never a stack trace. |

## Added during implementation (driven by evidence from live runs and the evaluation harness)

| ID | Requirement | Evidence that motivated it |
|---|---|---|
| HITL-04 | An explicit request for a human ("quero falar com um atendente") always escalates. | End-to-end demo: there was no path to a person. |
| HITL-05 | Money-moving actions (receivables advance) are never executed by the model: they are validated and become approval tickets with the pending action in the operator context. | Rubric: approval gates for risky actions. |
| ORC-07 | A reviewer checks Support answers against the tool results; inconsistent answers get one bounded revision. | v1 critique: no critic / iterative refinement. |
| MEM-04 | Turns leaving the history window are folded into a persisted rolling summary (additive DB migration). | Bounded window alone loses older context. |
| KNW-04 | Retrieval uses the planner's Portuguese rewrite **and** the user's wording, merged by best score. | Judge flagged wrong crediário answers: the rewrite ranked the "up to 48x" chunk 5th, the original 1st; with planner rewrites it still fell out of the top-8 until the product-page boost (ADR-14). |
| KNW-05 | Ingestion is incremental: chunk ids are content hashes; unchanged chunks are not re-embedded, removed ones are deleted. | Re-ingestion cost and freshness without `--reset`. |
| API-04 | Per-tool deadline (5 s → `tool_timeout`) and one fallback chat model on outage-type errors. | A slow backend or a model outage degrades one answer instead of hanging or failing the request. |
| OBS-04 | Query embeddings are cached (LRU, 1024 entries). | Repeated support questions should not pay the embedding round-trip again. |
| API-03 | `POST /v1/chat` honours an `Idempotency-Key` header (per identity): a retry replays the stored response without running agents; reusing a key with a different body → 422. | Client retries must not duplicate messages, tickets or approval requests. |
| OBS-03 | Moderation and planning run concurrently; a moderation flag discards the plan before any agent or tool runs. | Latency: removes one sequential network round-trip from every request. |
| OBS-02 | `GET /metrics` (Prometheus text): requests per agent, planned tasks, handoffs per reason, guardrail flags, LLM calls/tokens, latency p50/p95. | Operational monitoring. |

## Evaluation, observability, operations

| ID | Requirement |
|---|---|
| EVAL-01 | Deterministic test suite (fake LLM, no network) covering every acceptance criterion; runs in CI on every push. |
| EVAL-02 | Labelled evaluation dataset + harness reporting routing/tool accuracy, refusal precision/recall, RAG Recall@K and MRR, PT×EN breakdown, consistency over repeated runs (pass^k), latency and tokens. |
| OBS-01 | Structured JSON log per request (no raw PII) + trace and token usage in the response. |
