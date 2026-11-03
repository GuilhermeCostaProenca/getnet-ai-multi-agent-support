# 01 — Requirements

Source: challenge statement "AI Hardcore Engineer — Multi-Agent Support System".
Every requirement has an ID; acceptance criteria (`04-acceptance-criteria.md`) and tests reference these IDs.

## Functional

| ID | Requirement |
|---|---|
| REQ-API-01 | Expose `POST /chat` accepting `{"message": str, "user_id": str}` and returning a meaningful JSON response. |
| REQ-API-02 | Invalid payloads are rejected with HTTP 422; internal failures never leak stack traces (graceful JSON answer). |
| REQ-API-03 | Expose `GET /health` with knowledge-base status. |
| REQ-ORC-01 | At least three distinct agents (Router, Knowledge, Customer Support) cooperate through an explicit, typed communication mechanism (direct function calls with Pydantic contracts). |
| REQ-ORC-02 | Every response carries a trace of the steps/agents involved (agent, decision, duration). |
| REQ-RTR-01 | The Router is the single entry point: it classifies each message into a closed set of intents and selects the agent (or sequence of agents). |
| REQ-RTR-02 | Router output is structured (schema-validated), including intent, confidence, language and whether the knowledge base should complement a support answer. |
| REQ-KNW-01 | The Knowledge Agent answers Getnet product/service questions using RAG over content ingested from Getnet's websites. |
| REQ-KNW-02 | The Knowledge Agent answers general-purpose factual questions (e.g. weather, exchange rates) using a web search tool. |
| REQ-KNW-03 | Answers cite their sources (URL) and are grounded only on retrieved context. |
| REQ-RAG-01 | Ingestion pipeline: fetch → clean (remove navigation/boilerplate) → chunk → embed → persist in a vector store. |
| REQ-RAG-02 | Retrieval uses vector similarity with a minimum relevance threshold. |
| REQ-SUP-01 | The Customer Support Agent resolves inquiries by calling tools that retrieve the user's data (≥ 2 tools). |
| REQ-SUP-02 | Tool data is simulated and explicitly labeled as MOCK in every tool output. |
| REQ-ESC-01 | (Bonus) A Human Escalation Agent opens a handoff ticket when the system cannot confidently resolve a request. |

## Guardrails (bonus, designed in from the start)

| ID | Requirement |
|---|---|
| GR-SCOPE-01 | Scope is an **allowlist of intents**, not a blocklist of topics. Generic task execution (math homework, writing code, essays) is refused politely; factual lookups are allowed. |
| GR-INJ-01 | Prompt-injection attempts are blocked deterministically, before any LLM call. |
| GR-PII-01 | Card numbers (Luhn-valid) and CPFs (check-digit-valid) and secrets are redacted before reaching the LLM or the logs; the user is warned. |
| GR-MOD-01 | Unsafe content is detected by a moderation model and refused. |
| GR-AUTHZ-01 | Support tools are bound to the `user_id` of the request payload; IDs written in the message text are never used to fetch data. |
| GR-GROUND-01 | If retrieval finds no relevant context, the agent must not invent an answer; it says so and hands off. |
| GR-CONF-01 | Router confidence below a configurable threshold triggers human handoff. |

## Non-functional

| ID | Requirement |
|---|---|
| NFR-DOCKER-01 | `docker compose up --build` builds and runs the service. |
| NFR-CONF-01 | All configuration via environment variables; no secrets in the repository. |
| NFR-OBS-01 | Structured JSON logs per request (request_id, intent, agent, latency, tools, guardrail flags), with PII already redacted. |
| NFR-TEST-01 | Deterministic unit/API tests run without network or API keys (fake LLM); real-LLM scenario tests are marked `integration`. |
| NFR-PORT-01 | LLM provider behind an interface (`LLMClient`) so it can be swapped. |
