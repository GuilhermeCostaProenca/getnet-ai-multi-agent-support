# 02 — Architecture (v2)

```
Client ──Bearer JWT──► FastAPI /v1
                         │  auth (SEC-01..04) → rate limit
                         ▼
                    Orchestrator ── SQLite (conversations, messages, tickets)
   1 input guardrails (injection, PII)        │
   2 conversation paused? → record for operator, return (HITL-02)
   3 moderation (fail-open)
   4 Planner (structured plan: tasks[], clarification, language, confidence)
   5 unsafe → refuse │ ambiguous → clarify (then handoff) │ tasks → dispatch in parallel:
        product_knowledge → Knowledge.rag ──no grounding──► Knowledge.web(site:getnet) ──none──► handoff
        general_info      → Knowledge.web
        customer_support  → [Knowledge.retrieve] → Support (tool loop, user bound by server)
        out_of_scope      → partial refusal (no LLM)
   6 compose → handoff? (ticket + pause) → output guardrail → persist → response (+trace, usage)

Operator ──Bearer JWT (role=operator)──► /v1/operator/tickets … claim / reply / resolve
```

## Decisions

| ID | Decision | Rationale / trade-off |
|---|---|---|
| ADR-01 | Explicit orchestrator state machine (`active` ↔ `awaiting_human`) persisted in SQLite, not LangGraph | v1 noted LangGraph pays off with checkpoints and interrupt/resume. v2 needs exactly that, but the state machine has two states and one pause point; an explicit implementation is ~100 lines, fully testable with fakes and built inside the 60-min box. LangGraph (checkpointer + `interrupt`) is the migration path if the graph grows (more loops, long-running tools). |
| ADR-02 | JWT HS256 with roles; identity from `sub` only | Removes the v1 gap where `user_id` came from the payload. HS256 keeps the demo self-contained; production would verify RS256 tokens from the company IdP (JWKS). A dev-only token endpoint exists behind `ENABLE_DEV_TOKENS`. |
| ADR-03 | SQLite for conversation state | Durable, zero-ops, transactional; one file on a Docker volume. Swappable for Postgres behind the `Store` class. |
| ADR-04 | Planner emits a task list, not a single intent | Compound requests ("fee of Get Smart + write a poem") get the in-scope part answered instead of a blanket refusal (v1 limitation). |
| ADR-05 | Clarify before escalating | Low confidence used to go straight to a human; one clarifying question resolves most ambiguity cheaply. |
| ADR-06 | Replan on missing grounding (RAG → site-restricted web → human) | Observe→replan without an open-ended loop; bounded to one retry. |
| ADR-07 | OpenAI single provider (chat `gpt-5.4-mini`, `text-embedding-3-small`, `web_search`, `omni-moderation-latest`) behind `LLMClient` | One key covers all capabilities; the protocol keeps the provider swappable and lets tests run with a fake. |
| ADR-08 | ChromaDB embedded + curated Getnet sources (site.getnet.com.br FAQ first) with boilerplate removal | Same reasoning as v1 (validated: relevant 0.46–0.69 vs off-topic 0.25–0.27 cosine). |
| ADR-09 | Token usage metered per request via a context variable | Cost/latency visibility per conversation turn without threading a meter through every call. |

| ADR-10 | Multi-query retrieval (rewrite + original), one embedding call | Cross-lingual recall from the rewrite, lexical specificity from the original; cost: one extra vector query. |
| ADR-11 | Reviewer → at most one revision for Support answers | Catches numbers/dates not present in tool output; bounded so latency and cost stay predictable. |
| ADR-12 | Approval gate for money-moving tools | The model proposes, a human disposes; the tool validates limits before any ticket is opened. |
| ADR-13 | Rolling summary in batches of 4 turns | Prompt size stays bounded; the summary is persisted, so it survives restarts. |

## RAG pipeline
fetch curated pages → strip `head/nav/header/footer/script/style/form` → drop lines repeated in ≥ 50% of pages →
paragraph-aware chunks (~1000 chars, 150 overlap) → `text-embedding-3-small` → Chroma (cosine) →
planner rewrites the query in Portuguese → top-8 → similarity ≥ 0.30 → grounded generation with `[n]` citations and an
`answered_from_context` self-check → replan (site-restricted web) → handoff.
