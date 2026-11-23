# Getnet Multi-Agent Support — v2

A containerized multi-agent assistant for Getnet merchants. A **Planner** turns each message (plus conversation
history) into a structured plan and orchestrates a **Knowledge** agent (RAG over Getnet's site + web search), a
**Customer Support** agent (tool calling over the caller's data) and a **Human Escalation** agent with a real operator
workflow — behind JWT authentication, layered guardrails and persistent state.

v2 was rewritten from scratch with **Spec-Driven Development** ([`specs/`](specs/) committed before any code) to close the
gaps found in the v1 evaluation (7.3/10). v1 remains on `main` history / tags `v1.0.0`–`v1.1.0`.

## What changed from v1 — and why

| v1 evaluation gap | v2 change | Evidence |
|---|---|---|
| **Security & Identity 5.5** — `user_id` came from the payload | JWT (HS256, issuer + expiry) on every endpoint; identity = token `sub`; body `user_id` must match (403); roles `merchant`/`operator`; per-identity rate limit; output PII guardrail | AC-30..34 |
| **Memory 1.5** — stateless requests | `conversation_id`, SQLite-persisted messages, bounded history window fed to planner and agents; follow-ups resolved into self-contained queries | AC-35, `test_history_survives_a_new_app_instance` |
| **Memory 1.5** (cont.) | Rolling summary of turns that leave the window, persisted, with additive DB migration | AC-49 |
| **Human Oversight 4.0** — in-memory ticket only | Persistent tickets with operator context; conversation paused (`awaiting_human`); operator API claim → reply → resolve; agents resume with the human outcome in history; explicit "talk to a human" always escalates; **money-moving actions need human approval** | AC-36, AC-37, AC-47, AC-48 |
| **Orchestration "plan-once"** | Multi-task plans (compound requests answered in part instead of refused); clarify before handoff; observe→replan (RAG → Getnet-restricted web → human); **reviewer → bounded revision** of Support answers; parallel tasks | AC-38..41, AC-46 |
| **Evaluation 7.7** — no metrics | Labelled dataset + harness: routing/tool accuracy, refusal P/R, Recall@K, MRR, PT×EN, pass^k, **LLM-as-judge**, latency, tokens; CI on every push | AC-45, [`eval/report.md`](eval/report.md) |
| RAG 8.2 | **Multi-query retrieval** (PT rewrite + original wording) — fixed wrong crediário answers found by the judge | AC-51 |
| Observability 6.5 | Token usage per request (metered across parallel agents), trace, structured JSON logs, **`/metrics`** (requests, handoffs by reason, guardrail flags, tokens, latency p50/p95) | AC-44, AC-50 |

<!-- RESULTS -->

## Quick start

```bash
cp .env.example .env          # set OPENAI_API_KEY and JWT_SECRET (long random string)
docker compose up --build     # first start ingests the Getnet pages (~20 s)
```

Open **http://127.0.0.1:8000/docs**:
1. `POST /v1/auth/dev-token` with `{"user_id": "cliente1988", "role": "merchant"}` (demo-only endpoint, enabled by
   `ENABLE_DEV_TOKENS=true` in docker-compose) → copy `access_token` → **Authorize**.
2. `POST /v1/chat` with `{"message": "When will the money from yesterday's sales be deposited?", "user_id": "cliente1988"}`.
3. Send the returned `conversation_id` with the next message to continue the conversation.

Operator flow: get a token with `"role": "operator"`, then `GET /v1/operator/tickets?status=open` →
`POST …/{id}/claim` → `POST …/{id}/reply` → `POST …/{id}/resolve`. The merchant sees the reply in
`GET /v1/conversations/{conversation_id}`.

CLI alternative for tokens: `python scripts/mint_token.py cliente1988 merchant`.

### Run locally without Docker

```bash
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python -m app.rag.ingest --reset                    # optional; startup ingests when the index is empty
uvicorn app.main:app --reload
```

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | — (required) | LLM, embeddings, web search, moderation |
| `JWT_SECRET` | — (required) | Signs/verifies tokens; the API refuses to start without it |
| `ENABLE_DEV_TOKENS` | `false` | Exposes `POST /v1/auth/dev-token` (demo only) |
| `RATE_LIMIT_PER_MINUTE` | `30` | Per-identity sliding window |
| `CHAT_MODEL` / `EMBEDDING_MODEL` / `MODERATION_MODEL` | `gpt-5.4-mini` / `text-embedding-3-small` / `omni-moderation-latest` | Models |
| `DATABASE_PATH` / `CHROMA_PATH` | `./data/app.db` / `./data/chroma` | State and vector index (Docker volume `app-data`) |
| `RAG_TOP_K` / `RAG_MIN_SIMILARITY` | `8` / `0.30` | Retrieval depth and grounding threshold |
| `PLANNER_MIN_CONFIDENCE` / `MAX_CLARIFICATIONS` / `HISTORY_WINDOW` | `0.55` / `1` / `10` | Orchestration policy |

## Architecture

```
Client ──Bearer JWT──► FastAPI /v1 ── auth (401/403) ── rate limit (429)
                          ▼
                     Orchestrator ─────────────── SQLite: conversations · messages · tickets
  1 input guardrails (injection → block; card/CPF/secret → redact, before any LLM)
  2 conversation awaiting_human? → record for the operator, no agent runs
  3 moderation (fail-open)
  4 Planner → plan {tasks[1..3], needs_clarification, confidence, language}
  5 unsafe → refuse │ ambiguous → clarify (then handoff) │ tasks in parallel:
        product_knowledge → Knowledge RAG ─no grounding─► web (site:getnet.com.br) ─none─► handoff
        general_info      → Knowledge web search
        customer_support  → [KB excerpts] → Support tool loop (identity bound by the server)
        out_of_scope part → partial refusal (template, no LLM)
  6 compose → handoff? (ticket + pause) → output guardrail → persist → response (trace, usage)

Operator ──JWT role=operator──► /v1/operator/tickets → claim → reply → resolve (conversation resumes)
```

| Agent | Responsibility | How |
|---|---|---|
| **Planner** (`app/agents/planner.py`) | Entry point: intent allowlist, task split, reference resolution, clarification | One structured-output call (strict JSON schema) with the history window |
| **Knowledge** (`app/agents/knowledge.py`) | Getnet product answers; general facts | RAG with `[n]` citations and an `answered_from_context` self-check; bounded replan to a Getnet-restricted web search; OpenAI `web_search` for general questions |
| **Customer Support** (`app/agents/support.py`) | Merchant's own sales, deposits, machines, advance requests | Tool loop (≤ 4 rounds) over 4 tools; optional KB excerpts first (agent sequence); **reviewer** compares the answer with the tool results and triggers one revision when they disagree |
| **Human Escalation** (`app/agents/handoff.py`) | Tickets and the operator loop | Persistent tickets with context, pause/resume of the conversation |

**Communication:** direct function calls through the orchestrator with Pydantic contracts (`Plan`, `Task`,
`AgentResult`). Agents never call each other. The conversation state machine (`active` ↔ `awaiting_human`) is explicit
and persisted (ADR-01 explains why not LangGraph yet, and when it would pay off).

### Tools (mock data, clearly labelled)
`get_sales_and_settlements` (computes each installment's deposit date in America/Sao_Paulo), `diagnose_terminal`
(connectivity/signal/firmware + decline codes), `get_merchant_profile`, and `request_receivables_advance` — a
**money-moving action the model can never execute**: the tool validates the amount against scheduled receivables and the
orchestrator turns it into an approval ticket for a human. Customer records are simulated and every tool output carries
`data_source: MOCK`. LLM-facing schemas have **no user id**: the server binds the token subject.

## RAG pipeline
Curated sources ([`app/rag/sources.py`](app/rag/sources.py)) — the example questions live on **site.getnet.com.br**, whose FAQ
(`/duvidas/`) is the densest page; getnet.net/en adds the global view → clean (`head/nav/header/footer/script/form`
removed; lines repeated in ≥ 50 % of pages dropped) → ~1000-char chunks, 150 overlap → `text-embedding-3-small` →
Chroma (cosine) → **multi-query retrieval**: the planner's Portuguese rewrite and the user's own wording are embedded in
one call and merged by best score (the rewrite bridges EN→PT; the original keeps specific terms — the judge caught
"crediário up to 48x" ranked 5th by the rewrite alone) → top-8, similarity ≥ 0.30 → grounded generation with citations
+ self-check → replan → handoff.

## Guardrails (defense in depth)
| Layer | Catches |
|---|---|
| JWT + roles + ownership checks | anonymous calls, cross-tenant reads (404, no existence leak) |
| Deterministic input checks | prompt injection (EN/PT) blocked before any LLM call; card (Luhn), CPF (check digits), explicit secrets redacted before LLM, storage and logs |
| Moderation model | harmful content (fails open; the planner also classifies `unsafe`) |
| Planner allowlist | out-of-scope work refused — only that part of a compound request |
| Tool binding | the model cannot choose whose data it reads |
| Grounding threshold + self-check + replan | unsupported answers |
| Output guardrail | card/CPF numbers in the answer |
| Rate limit | abuse per identity |

## Testing and evaluation

```bash
pytest                      # deterministic: fake LLM, no network, no secrets (also in CI)
pytest -m integration       # real OpenAI + ingested KB: challenge scenarios, memory follow-up, compound request
python scripts/evaluate.py  # labelled dataset → eval/report.md
```

- **Deterministic suite** — every acceptance criterion (`specs/04-acceptance-criteria.md`) has a test named after it;
  the fake LLM lets tests assert *what reached the model* (e.g. no card number) and *which agents/tools ran*.
- **Integration** — the 10 challenge scenarios through the real API with a real token, plus multi-turn and compound requests.
- **Evaluation harness** — [`eval/dataset.jsonl`](eval/dataset.jsonl) (EN + PT, adversarial, compound). Metrics: exact
  intent-set accuracy, tool accuracy, refusal precision/recall (over-blocking counts as failure), pass^k across repeated
  runs, Recall@K/MRR against gold source pages, latency p50/p95, tokens per request.

## Observability
Each response carries `trace` (step, detail, duration), `usage` (LLM calls and tokens, metered across parallel agents)
and `request_id`; each request emits one JSON log line with redacted text only; `GET /metrics` exposes Prometheus-style
counters (requests per agent, planned tasks per intent, handoffs per reason, guardrail flags, LLM calls and tokens) and
latency p50/p95. `python scripts/demo_flow.py` replays the full flow (auth, memory, compound request, human request,
operator loop, resume) against a running server. Production next steps: OpenTelemetry
spans per agent/tool, dashboards for handoff rate, no-grounding rate, refusal rate, p95 latency and cost per
conversation, alerts on spikes, and nightly runs of the evaluation harness as a regression gate.

## Limitations and next steps
- Customer data is mock; the operator UI is the API (no front end).
- HS256 with a shared secret for the demo; production: RS256/JWKS from the company IdP, token revocation.
- Rate limiter and the tool loop are in-process; multi-replica deployments need Redis and a shared DB (Postgres).
- The LLM-as-judge is the same model family and is not yet calibrated against human labels — a regression signal, not
  ground truth; next: a human-labelled sample to measure judge agreement, and online sampling of conversations.
- Some product answers are limited by the public content itself (e.g. the site has little detail on Get Clássica vs
  Get Smart and on antecipação mechanics); the system says so and offers a specialist instead of inventing.
- Latency p50 ≈ 4–5 s (planner, moderation and agent calls; reviewer adds one call on support answers). Next: run
  moderation in parallel with the planner, cache embeddings of frequent queries, stream responses.

## Spec-Driven Development, timebox and AI usage
Specs → code → tests, traceable by IDs (REQ/SEC/MEM/HITL/ORC/EVAL → AC → test names). The commit history shows the
specs committed before the code. Before the 60-minute window I analysed the v1 evaluation and listed the priorities;
specs, code, tests, Docker and evaluation were produced inside the window. Development used **Claude Code** as a pair
programmer under my direction (I defined the approach, reviewed, ran and verified everything); inside the product, LLMs
do structured planning, tool calling, grounded generation, web search and moderation.
