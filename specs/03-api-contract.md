# 03 — API contract (v2)

All business endpoints require `Authorization: Bearer <JWT>` (claims: `sub`, `role` ∈ {merchant, operator}, `iss`, `exp`).

## Merchant

`POST /v1/chat`
```json
{ "message": "When will yesterday's sales be deposited?", "user_id": "cliente1988", "conversation_id": null }
```
- `user_id` optional; when present it must equal the token `sub` (403 otherwise).
- `conversation_id` optional; omitted → new conversation. Someone else's id → 404.

Response 200:
```json
{
  "request_id": "…", "conversation_id": "c_…", "user_id": "cliente1988",
  "status": "active | awaiting_human",
  "answer": "…",
  "agents": ["support"], "intents": ["customer_support"], "confidence": 0.97, "language": "en",
  "sources": [{"title": "…", "url": "…", "score": 0.61}],
  "tools_used": [{"name": "get_sales_and_settlements", "arguments": {"days_back": 1}}],
  "handoff": {"required": false, "ticket_id": null, "reason": null},
  "guardrails": {"input_flags": [], "output_flags": []},
  "usage": {"llm_calls": 3, "input_tokens": 2100, "output_tokens": 350},
  "trace": [{"step": "planner", "detail": "…", "duration_ms": 900}],
  "latency_ms": 4200
}
```

`GET /v1/conversations/{conversation_id}` → `{conversation_id, status, messages: [{role, content, agent, created_at}]}` (owner only).

## Operator (role `operator`)

| Method | Path | Effect |
|---|---|---|
| GET | `/v1/operator/tickets?status=open` | List tickets with context |
| POST | `/v1/operator/tickets/{id}/claim` | `open` → `claimed` by this operator |
| POST | `/v1/operator/tickets/{id}/reply` `{"message"}` | Adds an `operator` message to the conversation (claimer only) |
| POST | `/v1/operator/tickets/{id}/resolve` `{"resolution"}` | Ticket `resolved`; conversation back to `active` |

## Other
- `POST /v1/auth/dev-token` `{"user_id", "role"}` → `{"access_token"}` — only when `ENABLE_DEV_TOKENS=true` (demo/testing).
- `GET /health` → `{"status": "ok"|"degraded", "knowledge_base_chunks": n}`.
- `GET /metrics` → Prometheus text (internal scrape; not exposed publicly in production).

Errors: 401 (no/invalid/expired token), 403 (role or `user_id` mismatch), 404 (unknown or foreign conversation/ticket),
409 (ticket state conflict), 422 (validation), 429 (rate limit). LLM failures → 200 with a handoff answer.
