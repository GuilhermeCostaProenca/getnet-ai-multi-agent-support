# Getnet Multi-Agent Support System

A containerized multi-agent customer-support service for Getnet merchants. A **Router Agent** classifies every message and orchestrates a **Knowledge Agent** (RAG over Getnet's websites + web search), a **Customer Support Agent** (tool calling over customer data) and a **Human Escalation Agent**, behind layered guardrails.

Built with **Spec-Driven Development**: the specs in [`specs/`](specs/) were written and committed before any code, and every acceptance criterion maps to a test.

| | |
|---|---|
| Stack | Python 3.12 · FastAPI · Pydantic · OpenAI (`gpt-5.4-mini`, `text-embedding-3-small`, `web_search`, `omni-moderation-latest`) · ChromaDB · BeautifulSoup · Pytest · Docker |
| Agents | Router · Knowledge · Customer Support · Human Escalation (bonus) |
| Guardrails | intent allowlist · prompt-injection block · PII redaction (Luhn/CPF) · moderation · tool authorization · grounding threshold · confidence-based handoff |

---

## 1. Quick start

**Requirements:** Docker (with Compose) and an OpenAI API key.

```bash
cp .env.example .env          # then set OPENAI_API_KEY
docker compose up --build
```

On first start the service ingests the Getnet pages into Chroma (≈ 20–40 s, persisted in the `kb-data` volume). Then:

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "When will the money from yesterday'"'"'s sales be deposited?", "user_id": "cliente1988"}'
```

- Health: `GET http://localhost:8000/health` → `{"status":"ok","knowledge_base_chunks":N}`
- Interactive docs (OpenAPI): `http://localhost:8000/docs`
- Run all challenge scenarios + adversarial cases: `python scripts/run_scenarios.py`

### Run locally without Docker

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python -m app.rag.ingest --reset                  # optional: startup ingests automatically when empty
uvicorn app.main:app --reload
```

### Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | — (required) | OpenAI credentials |
| `CHAT_MODEL` | `gpt-5.4-mini` | Router / agents model |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | RAG embeddings |
| `MODERATION_MODEL` | `omni-moderation-latest` | Safety classifier |
| `CHROMA_PATH` | `./data/chroma` | Vector store location |
| `AUTO_INGEST` | `true` | Ingest on startup when the collection is empty |
| `RAG_TOP_K` / `RAG_MIN_SIMILARITY` | `8` / `0.30` | Retrieval depth and grounding threshold |
| `ROUTER_MIN_CONFIDENCE` | `0.55` | Below this → human handoff |
| `MODERATION_ENABLED` | `true` | Toggle the moderation layer |

---

## 2. Tests

```bash
pytest                    # 36 deterministic tests: fake LLM, in-memory store, no network, no key
pytest -m integration     # real OpenAI + ingested KB: the 10 challenge scenarios + an out-of-scope case
```

| Layer | What it proves | Files |
|---|---|---|
| Unit — guardrails | Luhn / CPF validation, redaction, injection patterns (EN/PT), no false positives on plain numbers | `tests/test_guardrails.py` |
| Unit — tools | settlement-date rules (Pix D+0, debit D+1 business day, credit D+30 per installment), decline codes, terminal diagnosis, MOCK labeling, authorization binding | `tests/test_customer_tools.py` |
| Unit — RAG | HTML cleaning, cross-page boilerplate removal, chunk size/overlap | `tests/test_ingest.py` |
| Orchestration | routing per intent, agent sequence (KB → Support), refusals without specialist calls, PII never reaches the LLM, low confidence / no grounding / LLM failure → handoff, moderation fail-open, trace | `tests/test_orchestrator.py` |
| API contract | response schema, 422 validation, health | `tests/test_api.py` |
| Integration | real routing, tool use and sources for the challenge scenarios | `tests/integration/test_scenarios.py` |

Test names carry acceptance-criteria ids (`test_ac15_tools_bound_to_payload_user`), giving traceability **requirement → spec → code → test** (see [`specs/04-acceptance-criteria.md`](specs/04-acceptance-criteria.md)).

**Strategy for comprehensive integration testing of the orchestration.** The orchestrator depends only on the `LLMClient` and `KnowledgeStore` protocols, so the same workflow is tested at three levels: (1) deterministic contract tests with a scripted fake LLM that asserts *which* agents/tools were called and *what* reached the LLM; (2) live tests against the real model and KB that assert routing, tool selection and grounding signals (not exact wording, since output is non-deterministic); (3) an evaluation dataset (`scripts/run_scenarios.py`, extensible to a labeled JSONL) to track intent accuracy, tool-selection accuracy, grounding rate, refusal precision/recall and latency across prompt/model changes, run in CI as a regression gate.

---

## 3. Architecture

```
            POST /chat {message, user_id}
                        │
                ┌───────▼────────┐
                │  Orchestrator  │  request_id · trace · structured log
                └───────┬────────┘
   1 input guardrails (deterministic: injection, PII redaction)
   2 moderation (omni-moderation, fails open)
                        │
                ┌───────▼────────┐
                │  Router Agent  │  structured output: intent, confidence,
                └───────┬────────┘  language, consult_kb, retrieval_query
     ┌──────────────┬───┴────────────┬──────────────────┬───────────────┐
product_knowledge general_info  customer_support   out_of_scope/unsafe  low confidence
     │              │                │                   │               │
 Knowledge (RAG) Knowledge (web)  [Knowledge.retrieve]  policy refusal  Escalation
     │              │                ▼                   (no LLM)        Agent
     │              │           Support Agent ── tools ── mock customer data
     └──────────────┴────────┬───────┘
          no grounding / failure ──► Escalation Agent (handoff ticket)
                             ▼
               ChatResponse (answer, agent, sources, tools_used, handoff, guardrails, trace)
```

### Agents

| Agent | Responsibility | How |
|---|---|---|
| **Router** (`app/agents/router.py`) | Entry point; decides the agent or sequence | One LLM call with **strict JSON-schema output** (`RouteDecision`). Also rewrites the query into Portuguese (`retrieval_query`) because the knowledge base is in Portuguese and users may write in English. |
| **Knowledge** (`app/agents/knowledge.py`) | Product questions via RAG; general facts via web search | Retrieval + grounded generation with numbered citations; OpenAI native `web_search` tool with URL citations. |
| **Customer Support** (`app/agents/support.py`) | Resolve merchant issues with their data | Tool-calling loop (max 4 rounds) over 3 tools; can receive KB excerpts from the Knowledge Agent (agent sequence). |
| **Human Escalation** (`app/agents/escalation.py`) | Handoff when the system can't resolve confidently | Creates a ticket in an in-memory queue (**mock**; production would publish to a ticketing/contact-center system). |

**Communication mechanism:** direct, synchronous function calls mediated by the orchestrator, with Pydantic contracts (`RouteDecision`, `AgentResult`). Agents never call each other, so each is unit-testable and the data flow lives in one place (`app/orchestrator.py`).

### Support tools (`app/tools/customer_tools.py`)

| Tool | Operation |
|---|---|
| `get_sales_and_settlements(days_back)` | Lists sales and **computes** each installment's expected deposit date (Pix D+0, debit D+1 business day, credit D+30 per installment, or D+1 with automatic advance) and status. |
| `diagnose_terminal(terminal_id, error_code)` | Diagnoses connectivity / signal / firmware and explains decline codes (05, 14, 51, 54, 55, 57, 91) with recommended actions. |
| `get_merchant_profile()` | Plan, masked bank account, settlement rules, terminals. |

> **Data disclosure:** there is no Getnet backend integration. Customer records are **simulated** (`cliente1988`, `cliente2000`) and every tool output carries `data_source: "MOCK - simulated data..."`; the agent tells the user the data is simulated.

**Authorization (GR-AUTHZ-01):** the LLM-facing tool schemas have **no `user_id` parameter**; the server binds the `user_id` of the request payload when executing a tool and drops any `user_id` the model tries to pass. Asking "show the sales of cliente2000" can never return another merchant's data (defense against IDOR via prompt).

---

## 4. RAG pipeline

| Stage | Implementation |
|---|---|
| **Sources** | `app/rag/sources.py`: 18 curated pages. The example questions (Get Clássica/Smart, crediário, Pix) live on the Brazilian site **site.getnet.com.br** (the site whose title is the link text in the challenge), with `/duvidas/` (≈ 50k chars FAQ) as the densest source; `getnet.net/en` adds the global corporate view. The BR page sitemap returns an error, hence the curated list. |
| **Ingestion** | `httpx` fetch (pages are server-rendered WordPress, no headless browser needed). |
| **Cleaning** | BeautifulSoup drops `head/script/style/nav/header/footer/form`; lines repeated in ≥ 50 % of pages (menus, "Já é cliente?" modal) are removed so they don't dominate retrieval. |
| **Chunking** | Paragraph-aware, ~1000 chars with 150-char overlap; metadata `url`, `title`, `chunk_index`. |
| **Storage** | Embeddings via `text-embedding-3-small` stored in a persistent Chroma collection (cosine). Chroma's default local embedding model is never used. |
| **Retrieval** | Query rewriting to Portuguese by the router → top-k (8) → filter by `RAG_MIN_SIMILARITY`. |
| **Generation** | Grounded prompt: answer only from numbered context, cite `[n]`, say when the answer is not in the context. **No relevant chunk → no generation call**: the agent says it didn't find it and hands off (anti-hallucination). |

---

## 5. Guardrails (defense in depth)

The key design choice: scope is an **allowlist of intents**, not a blocklist of topics. Two official scenarios (weather, euro rate) are off-topic but legitimate, so the criterion is the *type* of request: **answering questions is allowed; executing unrelated work (math homework, writing code, essays) is not** — the "use the support bot as a free coding assistant" problem.

| Layer | Cost | Catches |
|---|---|---|
| Pydantic validation | 0 | malformed / oversized input (422) |
| Deterministic checks (`app/guardrails.py`) | ~0 ms, no LLM | prompt injection (EN/PT patterns) → blocked before any LLM call; card numbers (Luhn-valid), CPF (check digits), passwords → redacted **before** LLM and logs, user warned |
| Moderation model | ~0.4 s | fraud / harmful content; fails open (logged) because the router also classifies `unsafe` |
| Router allowlist | part of routing call | `out_of_scope` and `unsafe` → deterministic policy refusal (no generation) |
| Tool authorization | 0 | cross-customer data access |
| Grounding threshold | 0 | answers not supported by Getnet content |
| Confidence threshold | 0 | ambiguous requests → human handoff |
| Failure handling | 0 | any LLM/agent exception → handoff answer, never a stack trace |

Note: the router's self-reported confidence is **not a calibrated probability**; it is used only as a heuristic signal, combined with the retrieval threshold and handoff.

---

## 6. Observability, evaluation and production monitoring

**Implemented:** every response includes `request_id` and a `trace` (step, decision, duration); every request emits one structured JSON log line (`intent`, `agent`, `confidence`, `tools`, `sources`, `handoff`, guardrail `flags`, `latency_ms`, redacted message).

**Production plan (not implemented):**
- **Traces:** OpenTelemetry spans per agent/tool/LLM call (tokens, cost, latency) exported to Datadog/Grafana Tempo or LangSmith/Langfuse.
- **Dashboards:** intent distribution, handoff rate, refusal rate, p50/p95 latency per agent, tool error rate, no-grounding rate, LLM cost per conversation.
- **Alerts:** handoff or no-grounding rate spikes (KB drift or site change), moderation-unavailable, p95 latency, LLM error rate.
- **Evaluation:** labeled dataset (challenge scenarios + adversarial set) run in CI on every prompt/model change: intent accuracy, tool-selection accuracy, citation presence, refusal precision/recall (over-blocking is also a failure), plus LLM-as-judge for faithfulness to retrieved context; online sampling of production conversations for human review.
- **KB freshness:** scheduled re-ingestion with content hashing to only re-embed changed pages.

---

## 7. Design decisions and trade-offs

See [`specs/02-architecture.md`](specs/02-architecture.md) (ADR-01…08). Highlights:
- **Custom orchestrator over LangGraph:** one routing step plus an optional sequence doesn't need a graph runtime; plain typed functions are easier to test and explain. LangGraph would pay off with cycles, checkpoints or long-running conversational state.
- **Single provider (OpenAI) behind `LLMClient`:** one key covers chat, structured output, embeddings, web search and moderation; swapping providers is an implementation of one protocol.
- **Deterministic refusals:** policy messages are templates, not generations: predictable, free, and impossible to jailbreak into a different answer.

## 8. Limitations and next steps

- Customer data and the handoff queue are **mock / in-memory** (no Getnet APIs available).
- No conversation memory: each request is stateless (next step: session store keyed by `user_id`).
- `user_id` is trusted from the payload; production must derive it from authentication (JWT) instead.
- Web-search citations depend on the provider returning URL annotations (some answers, e.g. weather widgets, may come without them).
- KB coverage is limited to the curated pages; answers outside them correctly trigger handoff.
- Ingestion upserts chunks by `url#index`: if a page shrinks, stale chunks remain until `python -m app.rag.ingest --reset` (or `docker compose down -v`). Next step: content hashing and per-URL replacement.
- Next: streaming responses, rate limiting per user, OpenTelemetry, re-ranking (cross-encoder) and hybrid search (BM25 + vectors), evaluation in CI.

## 9. How AI tools were used

Development used **Claude Code** as a pair programmer under the candidate's direction: SDD specs were written first and committed (`docs(specs)` commit precedes all code), then the implementation was generated module by module against those specs, executed and verified (unit tests, real ingestion, real API calls for every scenario). Inside the product, LLM tools are used for structured routing, tool calling, grounded generation, query rewriting, web search and moderation.

## Project layout

```
app/
  main.py            FastAPI app, wiring, startup ingestion
  orchestrator.py    workflow, data flow, trace, structured log
  schemas.py         API + agent contracts
  guardrails.py      deterministic input guardrails
  llm.py             LLMClient protocol + OpenAI implementation
  observability.py   JSON logging
  agents/            router · knowledge · support · escalation · policy
  tools/             customer support tools (mock data)
  rag/               sources · ingest · store
specs/               requirements · architecture · API contract · acceptance criteria
tests/               unit/API (fake LLM) + integration (real LLM)
scripts/             run_scenarios.py
```
