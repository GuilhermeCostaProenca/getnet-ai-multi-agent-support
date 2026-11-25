# 01 — Requirements (v3)

Source: the challenge statement + the v1 evaluation (7.3/10). IDs are referenced by the acceptance criteria
(`04-acceptance-criteria.md`) and by test names.

## Constraints
| ID | Constraint |
|---|---|
| CON-01 | 60-minute development window; this attempt starts from an empty workspace (no reuse of earlier attempts). |
| CON-02 | LLM provider: **Anthropic Claude only** (Messages API). No OpenAI: therefore no embeddings API and no moderation API. |
| CON-03 | No real Getnet customer backend: customer data is **simulated** and every tool output is labelled `MOCK`. |

## Challenge (functional)
| ID | Requirement |
|---|---|
| REQ-API-01 | `POST /v1/chat` accepts `{"message", "user_id"}` and returns meaningful JSON (answer, agents, sources, tools, trace). |
| REQ-AGT-01 | At least three agents with an explicit communication mechanism: **Planner/Router**, **Knowledge**, **Customer Support** (+ **Escalation**). |
| REQ-KNW-01 | Knowledge agent answers Getnet product questions with **RAG over Getnet's official pages**, citing sources. |
| REQ-KNW-02 | General questions (weather, exchange rate, news) are answered with **web search**, citing sources. |
| REQ-SUP-01 | Support agent uses **≥ 2 tools** over the authenticated merchant's data (profile/settlements, terminal diagnosis). |
| REQ-OPS-01 | Docker image + compose, automated tests, comprehensive README. |

## From the v1 evaluation (non-functional)
| ID | Requirement | v1 gap |
|---|---|---|
| SEC-01 | Every endpoint requires a JWT (HS256, issuer, expiry); identity = token `sub`. | no caller auth |
| SEC-02 | Body `user_id`, if sent, must equal the token `sub` (403); tools receive the identity from the server, never from the LLM. | user_id from payload |
| SEC-03 | Roles `merchant` / `operator`; operator API rejects merchants (403). Per-identity rate limit (429). | no roles |
| SEC-04 | Input guardrails: prompt-injection patterns blocked; card numbers (Luhn) and CPF (check digits) redacted before storage/LLM. Output guardrail: PII redacted from answers. | input only |
| SEC-05 | Unsafe requests refused: planner `unsafe` intent **and** provider-level refusal (`stop_reason=refusal`) map to a policy refusal (no moderation API under CON-02). | — |
| MEM-01 | Multi-turn conversations by `conversation_id`, persisted in SQLite; another user's conversation is reported as not found (404). | stateless |
| MEM-02 | Bounded history window fed to planner/agents; follow-ups ("and how long does it take?") resolved from history. | — |
| MEM-03 | Older turns folded into a persisted rolling summary. | — |
| HITL-01 | Escalation creates a persistent ticket with context (reason, intents, tools used, recent messages). | in-memory |
| HITL-02 | While a ticket is open the conversation is **paused**: no agent runs, the user sees status + the operator's latest reply. | — |
| HITL-03 | Operator API: list / claim / reply / resolve; after resolve the agents resume with the human outcome in history. | — |
| HITL-04 | Explicit "talk to a human" always escalates. | — |
| HITL-05 | **Approval gate**: money-moving requests (receivables advance) only open a request for human approval; nothing is executed. | — |
| ORC-01 | Planner returns a structured plan (language, confidence, tasks, clarification, wants_human); intents from an allow-list. | plan-once |
| ORC-02 | Compound requests are split; in-scope parts answered, out-of-scope parts refused (partial refusal). | whole refusal |
| ORC-03 | Low confidence / ambiguity → clarifying question first; escalate only after the clarification budget. | direct handoff |
| ORC-04 | Observe → replan: KB miss → Getnet-restricted web search → human. Bounded loops (tool rounds, one replan). | — |
| ORC-05 | Reviewer checks Support answers against tool results; one bounded revision when inconsistent. | no critic |
| ORC-06 | Independent tasks run in parallel. | — |
| KNW-03 | Retrieval: **BM25** (no embeddings under CON-02) over cleaned, chunked pages, multi-query (planner's Portuguese rewrite + original wording), product-page boost, top-k with abstention. | — |
| EVAL-01 | Deterministic test suite with a fake LLM, run in CI on every push. | no CI |
| EVAL-02 | Labelled dataset + harness: routing accuracy (EN/PT), tool accuracy, refusal precision/recall, pass^k, Recall@K/MRR, LLM-as-judge, latency, tokens; regression gate. | no metrics |
| OBS-01 | Per-request trace (steps with durations), token usage, structured JSON logs. | partial |
| OBS-02 | `GET /metrics` (Prometheus text): requests by agent, handoffs by reason, guardrail flags, tokens, latency p50/p95. | none |
| API-02 | `Idempotency-Key` on `POST /v1/chat` replays the stored response (24 h); same key + different body → 422. | — |
