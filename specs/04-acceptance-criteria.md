# 04 — Acceptance criteria (v3)

Test type: **U** = deterministic (fake LLM, CI) · **L** = live (real Claude, `pytest -m integration`) · **E** = eval harness.

## Challenge scenarios
| AC | Scenario | Req | Test |
|---|---|---|---|
| AC-01 | "Taxas da maquininha / diferença Get Clássica × Get Smart" → Knowledge (RAG) with Getnet sources | REQ-KNW-01 | L, E |
| AC-02 | Pix / payment link / crediário product questions answered from the KB with citations | REQ-KNW-01, KNW-03 | L, E |
| AC-03 | "Quando cai o dinheiro das vendas de ontem?" → Support calls `get_sales_and_settlements` | REQ-SUP-01 | L, E |
| AC-04 | "Minha maquininha não conecta" → Support calls `diagnose_terminal` | REQ-SUP-01 | L, E |
| AC-05 | Weather / exchange rate → web search with sources | REQ-KNW-02 | L, E |
| AC-06 | Out-of-scope (math, code, poem, competitor) → polite refusal | ORC-02 | U, E |

## Security
| AC | Criterion | Req | Test |
|---|---|---|---|
| AC-10 | No/invalid/expired token → 401 | SEC-01 | U |
| AC-11 | Body user_id ≠ token sub → 403; tools never receive an LLM-chosen user_id | SEC-02 | U |
| AC-12 | Merchant on operator API → 403; rate limit → 429 | SEC-03 | U |
| AC-13 | Injection blocked without an LLM call; card/CPF redacted in storage and prompts; PII redacted in answers | SEC-04 | U |
| AC-14 | Planner `unsafe` or provider refusal → policy refusal, no ticket | SEC-05 | U, E |

## Memory & HITL
| AC | Criterion | Req | Test |
|---|---|---|---|
| AC-20 | Follow-up in the same conversation resolved from history | MEM-02 | U, L |
| AC-21 | Another merchant's conversation → 404; history survives a new app instance | MEM-01 | U |
| AC-22 | Turns beyond the window are summarised and persisted | MEM-03 | U |
| AC-23 | Escalation → ticket with context; conversation paused; paused turns make no LLM call | HITL-01, HITL-02 | U |
| AC-24 | Operator claim → reply (visible to merchant) → resolve → agents resume | HITL-03 | U |
| AC-25 | "Quero falar com um atendente" → ticket `user_requested_human` | HITL-04 | U |
| AC-26 | Receivables advance → `approval_required` ticket, nothing executed | HITL-05 | U |

## Orchestration & knowledge
| AC | Criterion | Req | Test |
|---|---|---|---|
| AC-30 | Compound request: in-scope part answered + partial refusal | ORC-02 | U, L |
| AC-31 | Low confidence → clarifying question; second miss → escalation | ORC-03 | U |
| AC-32 | KB miss → Getnet web search; web miss → handoff `no_grounded_answer` | ORC-04 | U |
| AC-33 | Reviewer flags inconsistent Support answer → one revision | ORC-05 | U |
| AC-34 | Agent exception → handoff `agent_failure`, request still returns 200 | ORC-04 | U |
| AC-35 | BM25 ranks the named product's page first; multi-query merges results | KNW-03 | U, E |

## Engineering
| AC | Criterion | Req | Test |
|---|---|---|---|
| AC-40 | Trace order and token usage present in every response | OBS-01 | U |
| AC-41 | `/metrics` counts requests, handoffs by reason, flags; latency quantiles | OBS-02 | U |
| AC-42 | Idempotent replay; reused key with a different body → 422 | API-02 | U |
| AC-43 | Eval report with routing/tool/refusal/retrieval/judge metrics; gate exits non-zero below thresholds | EVAL-02 | E |

## Full rubric coverage
| AC | Criterion | Req | Test |
|---|---|---|---|
| AC-50 | `GET /` serves the console; it logs in with a dev token, chats, shows trace/sources/tools, and runs the operator loop | UI-01 | U (served), manual (demo) |
| AC-51 | A FAQ question and its answer land in the same chunk; every chunk starts with its page title | KNW-04 | U |
| AC-52 | Re-ranker reorders BM25 candidates; on re-ranker failure BM25 order is kept | KNW-05 | U |
| AC-53 | Re-ingesting unchanged pages stores nothing new; changed chunks replaced | KNW-06 | U |
| AC-54 | Slow tool → `tool_timeout`; outage on the primary model → one retry on the fallback | RES-01 | U |
| AC-55 | Spans emitted for planner/agents/tools/LLM when tracing is enabled | OBS-03 | U |
| AC-56 | Repeated query hits the retrieval cache | OBS-04 | U |
| AC-57 | Eval report includes judge agreement on the calibration set | EVAL-03 | E |
