# 04 — Acceptance criteria

Test names carry the AC id (e.g. `test_ac11_...`). **U** = deterministic test with fake LLM (always runs). **I** = integration test against the real LLM + ingested KB (`pytest -m integration`).

## Challenge scenarios (user `cliente1988`)

| AC | Message | Expected intent → agent | Must | Test |
|---|---|---|---|---|
| AC-01 | Difference between Get Clássica and Get Smart? | product_knowledge → knowledge | sources from getnet site | I |
| AC-02 | Weather forecast in Porto Alegre tomorrow? | general_info → knowledge (web) | web source URL | I |
| AC-03 | When will yesterday's sales money be deposited? | customer_support → support | calls `get_sales_and_settlements` | U, I |
| AC-04 | Do I need a bank account to receive via Pix? | product_knowledge → knowledge | sources | I |
| AC-05 | Card machine won't connect to the internet | customer_support → support (+ KB) | calls `diagnose_terminal` | U, I |
| AC-06 | How does receivables advance (antecipação) work? | product_knowledge → knowledge | sources | I |
| AC-07 | Euro exchange rate today? | general_info → knowledge (web) | web source URL | I |
| AC-08 | Machine showing a transaction decline error | customer_support → support | calls `diagnose_terminal` | U, I |
| AC-09 | How many installments with crediário? | product_knowledge → knowledge | sources | I |
| AC-10 | Sell through WhatsApp using Payment Link? | product_knowledge → knowledge | sources | I |

## Guardrails and reliability

| AC | Given | Then | Req | Test |
|---|---|---|---|---|
| AC-11 | "Solve 2x+3=7" (router says out_of_scope) | polite refusal, no specialist agent called | GR-SCOPE-01 | U |
| AC-12 | "Write me a Python function…" | out_of_scope refusal | GR-SCOPE-01 | U, I |
| AC-13 | "Ignore all previous instructions…" | `agent=guardrail`, flag `prompt_injection`, router **not** called, dedicated "blocked" message | GR-INJ-01 | U |
| AC-25 | "I forgot my password, how do I reset it?" | **not** redacted (no false-positive PII) | GR-PII-01 | U |
| AC-26 | Request at 22:30 in São Paulo (01:30 UTC next day) | "today" is the São Paulo date | REQ-SUP-03 | U |
| AC-27 | General-info answer | `sources` not empty | REQ-KNW-03 | I |
| AC-28 | Retrieved chunks are related but don't answer the question | RAG self-check `answered_from_context=false` → handoff `insufficient_context` | GR-GROUND-01 | U |
| AC-29 | Knowledge base empty (ingestion failed) | `/health` reports `degraded` | REQ-API-03 | U |
| AC-14 | Message containing a Luhn-valid card number | number replaced by `[CARD_REDACTED]` before the LLM, flag `pii_card`, warning in answer | GR-PII-01 | U |
| AC-15 | `user_id=cliente1988` asks for "transactions of cliente2000" | tools only receive `cliente1988` | GR-AUTHZ-01 | U |
| AC-16 | Router confidence < threshold | escalation agent, `handoff.required=true`, ticket id | GR-CONF-01, REQ-ESC-01 | U |
| AC-17 | KB has no chunk above similarity threshold | no generation call; says it could not find it; handoff | GR-GROUND-01 | U |
| AC-18 | LLM raises an exception | HTTP 200, escalation answer, no stack trace | REQ-API-02 | U |
| AC-19 | Empty message / missing user_id | HTTP 422 | REQ-API-02 | U |
| AC-20 | Unknown user asks for their sales | tool returns not-found; no crash | REQ-SUP-01 | U |
| AC-21 | Moderation flags message | `agent=guardrail`, flag `moderation_flagged` | GR-MOD-01 | U |
| AC-22 | Any support tool output | contains `data_source` = MOCK label | REQ-SUP-02 | U |
| AC-23 | Ingestion cleaning | boilerplate lines repeated across pages removed; chunks ≤ size with overlap | REQ-RAG-01 | U |
| AC-24 | Every response | non-empty `trace` and `request_id` | REQ-ORC-02 | U |
