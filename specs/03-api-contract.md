# 03 — API contract (v3)

All endpoints except `/health` and `/metrics` require `Authorization: Bearer <JWT>` (HS256, `iss`, `exp`, `sub`, `role`).

| Method & path | Role | Purpose | Errors |
|---|---|---|---|
| `POST /v1/chat` | merchant | Send a message. Body: `{"message": str(1..2000), "user_id"?: str, "conversation_id"?: "c_<16 hex>"}`. Header `Idempotency-Key?` | 401 no/invalid token · 403 user_id ≠ sub · 404 foreign/unknown conversation · 422 invalid body or reused key · 429 rate limit |
| `GET /v1/conversations/{id}` | merchant (owner) | Status + persisted messages (user, assistant, operator, system) | 404 not owner |
| `GET /v1/operator/tickets?status=open` | operator | Queue with context | 403 merchant |
| `POST /v1/operator/tickets/{id}/claim` | operator | open → claimed | 409 wrong state |
| `POST /v1/operator/tickets/{id}/reply` | operator | Message delivered to the merchant | 409 not claimed |
| `POST /v1/operator/tickets/{id}/resolve` | operator | Close ticket, conversation back to `active` | 409 |
| `POST /v1/auth/dev-token` | — | Demo token minting, only when `ENABLE_DEV_TOKENS=true` | 404 when disabled |
| `GET /metrics` | — | Prometheus text | — |
| `GET /health` | — | `{"status": "ok"|"degraded", "knowledge_chunks": n}` | — |

## `POST /v1/chat` response
```json
{
  "request_id": "…", "conversation_id": "c_…", "user_id": "cliente1988", "status": "active|awaiting_human",
  "answer": "…", "agents": ["knowledge"], "intents": ["product_knowledge"], "confidence": 0.95, "language": "pt",
  "sources": [{"title": "…", "url": "…", "score": 1.0}],
  "tools_used": [{"name": "get_sales_and_settlements", "arguments": {}}],
  "handoff": {"required": false, "ticket_id": null, "reason": null},
  "guardrails": {"input_flags": [], "output_flags": []},
  "usage": {"llm_calls": 3, "input_tokens": 0, "output_tokens": 0},
  "trace": [{"step": "planner", "detail": "…", "duration_ms": 0}],
  "latency_ms": 0
}
```
