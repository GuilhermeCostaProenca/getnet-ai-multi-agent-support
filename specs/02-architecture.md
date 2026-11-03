# 02 — Architecture

## Components

```
POST /chat ─► Orchestrator
               1. Input guardrails (deterministic)   GR-INJ-01, GR-PII-01
               2. Moderation (LLM classifier)        GR-MOD-01
               3. Router Agent (structured output)   REQ-RTR-01/02
               4. Dispatch by intent:
                    product_knowledge ─► Knowledge Agent (RAG)
                    general_info      ─► Knowledge Agent (web search)
                    customer_support  ─► [Knowledge retrieval] ─► Support Agent (tools)
                    out_of_scope      ─► policy refusal (no LLM)
                    unsafe            ─► policy refusal (no LLM)
                    low confidence / failure / no grounding ─► Escalation Agent
               5. Response + trace + structured log
```

| Module | Responsibility |
|---|---|
| `app/main.py` | FastAPI app, lifespan (wiring + auto-ingestion), endpoints. |
| `app/orchestrator.py` | Workflow and data flow between agents; builds the trace. |
| `app/schemas.py` | Contracts: `ChatRequest`, `ChatResponse`, `RouteDecision`, `AgentResult`, `Source`, `TraceStep`. |
| `app/guardrails.py` | Deterministic checks: injection heuristics, PII redaction (Luhn / CPF). |
| `app/llm.py` | `LLMClient` protocol + `OpenAIClient` (structured output, generation, web search, tool calling, embeddings, moderation). |
| `app/agents/router.py` | Intent classification prompt + decision. |
| `app/agents/knowledge.py` | RAG answer and web-search answer, with sources. |
| `app/agents/support.py` | Tool-calling loop over customer tools. |
| `app/agents/escalation.py` | Human handoff ticket (in-memory queue, mock). |
| `app/tools/customer_tools.py` | Mock customer data + tools. |
| `app/rag/*` | Sources list, ingestion pipeline, vector store wrapper. |

## Communication mechanism

Direct, synchronous function calls between agents, mediated by the Orchestrator. Agents never call each other directly; they receive typed inputs and return an `AgentResult` (answer, sources, tools used, handoff flag). This keeps each agent unit-testable and the data flow explicit in one place.

## Decisions (ADR-lite)

| ID | Decision | Rationale / trade-off |
|---|---|---|
| ADR-01 | Custom orchestrator instead of LangGraph | The workflow is a single routing step + optional sequence; plain Python with typed contracts is less dependency surface, easier to test and to explain. LangGraph would pay off with cycles, checkpoints or long-running state. |
| ADR-02 | OpenAI as the single provider (`gpt-5.4-mini`, `text-embedding-3-small`, native `web_search`, `omni-moderation-latest`) | One key covers chat, structured output, embeddings, web search and moderation. Swappable through `LLMClient`. |
| ADR-03 | ChromaDB (embedded, persistent) with embeddings computed by us | No extra service to run; avoids Chroma's default local embedding model download. |
| ADR-04 | Knowledge source: `site.getnet.com.br` (Brazilian product site, server-rendered) + `getnet.net/en` | The example questions (Get Clássica, Smart, crediário, Pix) live on the Brazilian site; `/duvidas/` is a dense FAQ. Its page sitemap is broken, so a curated URL list is used. |
| ADR-05 | Boilerplate removal by cross-page line frequency + dropping `header/footer/nav` | Menus repeated on every page would otherwise dominate retrieval. |
| ADR-06 | Scope as intent allowlist (GR-SCOPE-01) | Topic blocklists never end; a closed set of intents is finite and testable. |
| ADR-07 | Self-reported LLM confidence used only as a heuristic signal | It is not a calibrated probability; combined with retrieval score threshold and handoff. |
| ADR-08 | Moderation fails open (logged), router still classifies `unsafe` | Availability of the support channel vs. a second independent safety check. |

## RAG pipeline

1. **Ingestion** (`python -m app.rag.ingest`, or automatically at startup when the collection is empty): fetch each URL with `httpx`.
2. **Cleaning**: BeautifulSoup, drop `script/style/noscript/svg/header/footer/nav/form`, keep text lines; remove lines repeated in ≥ 50% of pages.
3. **Chunking**: paragraph-aware, ~1000 chars, 150 chars overlap, metadata `url`, `title`, `chunk_index`.
4. **Storage**: embeddings (`text-embedding-3-small`) in a persistent Chroma collection (cosine).
5. **Retrieval**: top-k (5) by cosine similarity, filtered by `RAG_MIN_SIMILARITY`.
6. **Generation**: grounded prompt — answer only from numbered context, cite `[n]`, say when the context does not contain the answer.
