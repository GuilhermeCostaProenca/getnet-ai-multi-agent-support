# 04 — Acceptance criteria (v2)

**U** = deterministic test (fake LLM, runs in CI). **I** = real LLM (`pytest -m integration`). **E** = evaluation harness (`scripts/evaluate.py`).
Test names carry the AC id.

## Challenge scenarios (merchant `cliente1988`)

| AC | Message | Expected | Test |
|---|---|---|---|
| AC-01 | Difference between Get Clássica and Get Smart? | product_knowledge, Getnet sources | I, E |
| AC-02 | Weather in Porto Alegre tomorrow? | general_info, web source | I, E |
| AC-03 | When will yesterday's sales be deposited? | customer_support, `get_sales_and_settlements` | U, I, E |
| AC-04 | Bank account needed for Pix? | product_knowledge | I, E |
| AC-05 | Machine won't connect to the internet | customer_support (+KB), `diagnose_terminal` | U, I, E |
| AC-06 | How does antecipação work? | product_knowledge | I, E |
| AC-07 | Euro exchange rate today? | general_info | I, E |
| AC-08 | Transaction decline error | customer_support, `diagnose_terminal` | I, E |
| AC-09 | Installments with crediário? | product_knowledge | I, E |
| AC-10 | Sell on WhatsApp with Payment Link? | product_knowledge | I, E |

## v2 criteria

| AC | Given → Then | Req | Test |
|---|---|---|---|
| AC-30 | No token / invalid / expired token → 401 | SEC-01 | U |
| AC-31 | Body `user_id` ≠ token `sub` → 403; tools receive only `sub` even if the message names another id | SEC-02 | U |
| AC-32 | Merchant on operator endpoint → 403; merchant reading another merchant's conversation → 404 | SEC-03 | U |
| AC-33 | More requests than the per-minute limit → 429 | SEC-04 | U |
| AC-34 | Injection → blocked before any LLM call; card/CPF redacted before the LLM; card in the answer redacted on output | SEC-05/06 | U |
| AC-35 | Second message with `conversation_id` → planner receives the first turn in its history; history persisted | MEM-01..03 | U |
| AC-36 | Escalation → ticket `open` + conversation `awaiting_human`; next merchant message runs no agent | HITL-01/02 | U |
| AC-37 | Operator claim → reply (merchant sees it in the conversation) → resolve → next merchant message runs agents again | HITL-03 | U |
| AC-38 | Compound request (product question + poem) → product part answered, poem refused, no handoff | ORC-02 | U, E |
| AC-39 | Ambiguous message → clarifying question; ambiguous again → handoff | ORC-03 | U |
| AC-40 | KB has no grounded answer → site-restricted web search tried before handoff; still nothing → handoff | ORC-04 | U |
| AC-41 | Router/agent exception → 200 with handoff, trace in execution order | ORC-06 | U |
| AC-42 | Every tool output labelled MOCK; settlement dates computed in America/Sao_Paulo | REQ-SUP-01 | U |
| AC-43 | Ingestion cleaning: boilerplate removed, chunk size/overlap respected | REQ-KNW-01 | U |
| AC-44 | Response carries trace, usage and request id; one JSON log line without raw PII | OBS-01 | U |
| AC-45 | Evaluation report: routing accuracy, refusal P/R, Recall@K, MRR, pass^k, PT×EN, LLM-as-judge quality | EVAL-02 | E |
| AC-46 | Support answer inconsistent with tool results → revised once; consistent → unchanged | ORC-07 | U |
| AC-47 | "I want to talk to an attendant" → handoff `user_requested_human` | HITL-04 | U, demo |
| AC-48 | Advance request → approval ticket with the pending action; invalid amounts rejected by the tool | HITL-05 | U |
| AC-49 | Long conversation → older turns summarized and sent as `[summary of earlier turns]`; old DBs migrated | MEM-04 | U |
| AC-50 | `/metrics` counts requests, handoffs per reason and guardrail flags; exposes latency quantiles | OBS-02 | U |
| AC-51 | Retrieval embeds the rewrite and the original query in one call and merges results | KNW-04 | U, E |
