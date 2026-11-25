# Getnet Multi-Agent Support System — v3

A multi-agent assistant for Getnet merchants: it answers product questions from Getnet's official pages (RAG),
general questions from the web, and account questions with tools over the authenticated merchant's (simulated) data —
with identity, memory, human oversight, evaluation and observability built in. v3 was rebuilt from an empty workspace
in a 60-minute window, driven by the v1 evaluation (7.3/10), using **Claude** as the only LLM provider.


## What changed since v1 (evaluation gap → v3 → evidence)

| v1 gap (score) | v3 | Evidence |
|---|---|---|
| **Security & Identity (5.5)**: `user_id` from the payload | JWT HS256 (issuer + expiry) on every endpoint; identity = token `sub`; body `user_id` must match (403); roles merchant/operator; per-identity rate limit; tools get the identity from the server, never from the LLM; prompt-injection screen; PII (card/Luhn, CPF, secrets) redacted before storage/LLM and in answers | AC-10..14 |
| **Memory (1.5)**: stateless | `conversation_id`, SQLite persistence (survives restarts), history window + persisted rolling summary, follow-ups resolved from history, owner check (404) | AC-20..22 |
| **Human oversight (4.0)**: in-memory ticket | Persistent tickets with context; conversation **paused** (no LLM call) while a human owns it; operator API claim → reply → resolve; agents resume with the outcome; explicit "talk to a human"; **approval gate**: receivables advance is only a request for a human | AC-23..26 |
| **Orchestration ("plan-once")** | Structured multi-task plan; compound requests answered in part (partial refusal); clarify before escalating; observe → replan (KB → Getnet-restricted web → human); reviewer → one bounded revision; parallel tasks; agent failure → handoff, never a 500 | AC-30..34 |
| **RAG (8.2)** | FAQ-aware chunking (question + answer together, page title on every chunk); content-hash incremental ingestion; BM25 multi-query (Portuguese rewrite + original) with product-page boost; **Claude re-ranking**; abstention; citations | AC-35, AC-51..53 |
| **Evaluation (7.7)** | 38 deterministic tests in CI; live acceptance suite; labelled dataset + harness (routing EN/PT, tools, refusal P/R, pass^k, Recall@K/MRR, LLM judge **with calibration**, latency, tokens) and regression gate | AC-43, AC-57 |
| **Observability & cost (6.5)** | Per-request trace and token usage; JSON logs; `/metrics` (Prometheus); **OpenTelemetry** spans; retrieval **cache**; per-tool timeout; fallback model on outages | AC-40, 41, 54..56 |
| **Production (7.5)** | Versioned API (`/v1`), idempotency keys, lean non-root image with healthcheck, CI | AC-42 |
| **Demo** | **Web console at `/`**: chat, per-answer "x-ray" (agents, intents, tools, sources, flags, tokens, latency, trace) and the operator view | AC-50 |

<!-- RESULTS -->

## Quick start

```bash
cp .env.example .env          # set ANTHROPIC_API_KEY and JWT_SECRET (long random string)
docker compose up --build     # first start ingests the Getnet pages (~30 s)
open http://localhost:8000    # web console (dev tokens are enabled in compose for the demo)
```

API docs at `/docs`. Without the console:

```bash
TOKEN=$(curl -s -X POST localhost:8000/v1/auth/dev-token -H 'content-type: application/json' \
  -d '{"user_id":"cliente1988"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -s localhost:8000/v1/chat -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"message":"Em quantas parcelas posso vender no crediário?"}'
```

Local development: `python -m venv .venv && pip install -r requirements-dev.txt && python -m app.rag.ingest && uvicorn app.main:app`.

## Architecture

```
client ──JWT──▶ FastAPI  (/v1/chat · /v1/conversations · /v1/operator/* · /metrics · /health · console at /)
                  │ auth · rate limit · idempotency
                  ▼
             Orchestrator — per-conversation state machine: active ⇄ awaiting_human
  input guardrails → memory (window + summary) → Planner (structured Plan)
     ├─ unsafe / model refusal ──────▶ policy refusal
     ├─ wants_human / ambiguity ─────▶ Escalation ──▶ ticket (SQLite) ──▶ Operator API ──▶ resume
     └─ tasks in parallel ─┬─ product_knowledge ▶ Knowledge: BM25 → Claude re-rank → grounded answer
                           │                       → (miss) Getnet-only web search → (miss) human
                           ├─ general_info ─────▶ Knowledge: Claude web search with sources
                           ├─ customer_support ─▶ Support: tool loop → reviewer → approval gate
                           └─ out_of_scope ─────▶ partial refusal
  compose → output guardrail → persist → metrics · logs · spans → response (answer, sources, tools, handoff, trace, usage)
```

| Agent | Role | Output contract |
|---|---|---|
| **Planner (router)** | Splits the message into tasks with intents from an allow-list, resolves references from history, writes a Portuguese retrieval query, decides clarification / human request | `Plan` (JSON schema) |
| **Knowledge** | RAG over Getnet's pages, Claude re-ranking, grounded answer with citations; Getnet-restricted web replan; general web search | `KnowledgeAnswer`, sources |
| **Customer Support** | Tool loop over the merchant's data, reviewer + one revision, approval gate | answer, tool calls, pending actions |
| **Escalation** | Ticket with context, pauses the conversation, operator workflow | ticket |

### Tools (simulated data, labelled `MOCK`)
`get_merchant_profile` · `get_sales_and_settlements` (business days in America/Sao_Paulo) · `diagnose_terminal`
(connectivity + decline-code meaning) · `request_receivables_advance` (validates, **never executes**: opens a human
approval). The identity is bound server-side and every call runs under a 5 s deadline.

### Key decisions (full list in [specs/02-architecture.md](specs/02-architecture.md))
- **Claude only** (`claude-opus-5-5`, JSON-schema structured outputs, `effort: low`): one provider, validated contracts.
  A provider refusal (`stop_reason=refusal`) becomes the `unsafe` policy, never an escalation.
- **BM25 instead of embeddings**: the provider has no embeddings API; BM25 is explainable and dependency-free (image
  ~300 MB). Its weakness on paraphrases is mitigated by the planner's Portuguese rewrite, multi-query merge, the
  product-page boost and Claude re-ranking.
- **Safety without a moderation endpoint**: regex injection screen + PII redaction before the model, `unsafe` intent,
  refusal mapping, output PII guard.
- **Custom orchestrator** instead of a framework: explicit state machine, every branch tested with a fake LLM.
- **SQLite** for conversations, tickets, idempotency and chunks: zero-ops; Postgres in production.

## Configuration
See [.env.example](.env.example): `ANTHROPIC_API_KEY`, `LLM_MODEL`, `FALLBACK_MODEL`, `JWT_SECRET`,
`ENABLE_DEV_TOKENS`, `RATE_LIMIT_PER_MINUTE`, `RERANK_ENABLED`, `OTEL_ENABLED` / `OTEL_ENDPOINT`, `DATABASE_PATH`.

## Testing and evaluation
```bash
pytest                                   # deterministic suite (fake LLM), runs in CI on every push
pytest -m integration                    # live acceptance tests against Claude
python scripts/evaluate.py --repeats 2 --gate   # labelled eval → eval/report.md, exits 1 below thresholds
```
Specs → code → tests are traceable: requirement IDs in [specs/01-requirements.md](specs/01-requirements.md),
acceptance criteria in [specs/04-acceptance-criteria.md](specs/04-acceptance-criteria.md), and test names cite them.

## Observability
Every response carries `trace` (steps with durations), `usage` (LLM calls and tokens) and guardrail flags; logs are
JSON; `/metrics` exposes requests per agent, planned tasks, handoffs by reason, guardrail flags, tokens and latency
p50/p95; OpenTelemetry spans (request → steps → LLM calls → tools) are exported over OTLP when `OTEL_ENABLED=true`.

## Limitations
- Customer data is simulated (no Getnet backend); the advance is never executed.
- BM25 is weaker than dense retrieval on paraphrases; re-ranking and query rewriting mitigate it.
- The LLM judge is Claude itself; its agreement on a small calibration set is reported, but it is not calibrated
  against a large human-labelled set.
- Dev tokens exist only for the demo console; production would use the company's identity provider.

## Timebox and AI usage
Window 15:33:23 → 16:33:23 on 2026-11-25, from an empty workspace (`bbad6b1`); specs were committed before any code
(`01916b3`, `a91b6ed`). Development used **Claude Code** as a pair programmer under my direction: I set the goals,
priorities and constraints, reviewed and verified every step; inside the product, Claude does planning, re-ranking,
grounded generation, tool calling, review and web search.
