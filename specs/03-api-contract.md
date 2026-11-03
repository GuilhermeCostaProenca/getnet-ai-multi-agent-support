# 03 — API contract

## `POST /chat`

Request (`ChatRequest`):

```json
{ "message": "When will the money from yesterday's sales be deposited?", "user_id": "cliente1988" }
```

| Field | Type | Rules |
|---|---|---|
| `message` | string | required, 1–2000 chars (trimmed) |
| `user_id` | string | required, 1–64 chars, `[A-Za-z0-9_.-]` |

Response 200 (`ChatResponse`):

```json
{
  "request_id": "9f1c…",
  "user_id": "cliente1988",
  "answer": "…",
  "agent": "support",
  "intent": "customer_support",
  "confidence": 0.97,
  "language": "en",
  "sources": [{ "title": "Dúvidas - Getnet", "url": "https://site.getnet.com.br/duvidas/", "score": 0.61 }],
  "tools_used": [{ "name": "get_sales_and_settlements", "arguments": { "days_back": 2 } }],
  "handoff": { "required": false, "ticket_id": null, "reason": null },
  "guardrails": { "pii_redacted": false, "flags": [] },
  "trace": [{ "step": "router", "detail": "intent=customer_support conf=0.97", "duration_ms": 812 }],
  "latency_ms": 2140
}
```

| Field | Values |
|---|---|
| `agent` | `knowledge` · `support` · `escalation` · `guardrail` |
| `intent` | `product_knowledge` · `general_info` · `customer_support` · `out_of_scope` · `unsafe` · `null` (blocked before routing) |
| `guardrails.flags` | `prompt_injection` · `pii_card` · `pii_cpf` · `pii_secret` · `moderation_flagged` · `moderation_unavailable` |

Errors: 422 for invalid payload (FastAPI validation). Upstream/LLM failures return 200 with `agent="escalation"` and `handoff.required=true` (the customer is never left without an answer).

## `GET /health`

```json
{ "status": "ok", "knowledge_base_chunks": 312 }
```

`status` is `degraded` when the knowledge base is empty (e.g. startup ingestion failed): the API still answers, but product questions hand off.
