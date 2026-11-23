# Evaluation report

26 cases × 3 runs = 78 requests (real LLM)

| Metric | Value |
|---|---|
| cases | 26 |
| repeats | 3 |
| runs | 78 |
| routing_accuracy | 1.0 |
| routing_accuracy_en | 1.0 |
| routing_accuracy_pt | 1.0 |
| tool_accuracy | 1.0 |
| refusal_precision | 1.0 |
| refusal_recall | 1.0 |
| pass^3 | 1.0 |
| unexpected_handoffs | 0 |
| judge_answers_question | 0.987 |
| judge_language_ok | 1 |
| judge_grounded_kb_answers | 0.917 |
| judge_score_avg_1to5 | 4.49 |
| latency_ms_p50 | 3711 |
| latency_ms_p95 | 8944 |
| tokens_per_request_avg | 4876 |

## Retrieval (raw user message → top-8, gold = official page)

| Slice | Recall@1 | Recall@3 | Recall@5 | Recall@8 | MRR | n |
|---|---|---|---|---|---|---|
| all | 0.909 | 1.0 | 1.0 | 1.0 | 0.955 | 11 |
| en | 0.833 | 1.0 | 1.0 | 1.0 | 0.917 | 6 |
| pt | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 5 |

## Failures

- none

## Low-quality answers (judge score ≤ 2 or not grounded)

- PT-01: score=3 grounded=False — A resposta atende à pergunta e está em português, mas mistura informações não sustentadas pelos trechos oficiais fornecidos, como ‘bateria de até 12h’, ‘Pix e QR Code’ e os critérios de faturamento citados. Pelos excertos, a diferença explicitamente documentada entre Get Clássica e Get Smart é que ambas têm chip de dados + Wi‑Fi, e as faixas de faturamento/taxas de uso podem variar; não há suporte nos trechos para a lista de recursos apresentada.
- PT-05: score=2 grounded=None — A resposta está em português e tenta orientar o problema, então atende parcialmente. Porém inclui muitos dados específicos da conta/maquininha sem apoio verificável nas informações fornecidas, e afirmações como 'a versão mais recente é 3.4.0' e detalhes de recusas parecem inventados neste contexto. Também não há base nos excertos oficiais para essas políticas ou diagnósticos. O conselho genérico de verificar Wi‑Fi, aproximar do roteador, reconectar e reiniciar é coerente, mas o restante torna a resposta pouco confiável.
- AC-06: score=3 grounded=False — The answer addresses the question and is in Portuguese, but it includes an unsupported claim that the FAQ says there is no additional fee to use the app and cites source [3] incorrectly; excerpt [2] supports this, while [3] is unrelated and does not mention the app. It also overstates that exact rules, rates, or deadlines were not found, although some related details about Antecipação Automática and app-based anticipation are in the excerpts. The response is cautious and mostly coherent, but not fully grounded in the provided sources.
- PT-05: score=2 grounded=None — Responde ao problema com passos plausíveis e está em português, mas inventa diagnóstico específico da conta e detalhes do dispositivo sem apoio verificável nos excertos oficiais fornecidos. Como não há trechos oficiais, a resposta deveria ser mais cautelosa e não afirmar dados concretos de aparelho/offline/firmware como fatos.
- AC-05: score=2 grounded=None — The reply addresses the connectivity problem and gives sensible troubleshooting steps, so it is broadly useful. However, it invents account-specific machine details without evidence from the visible data, and it presents several product-specific facts (model/status/firmware/update path) as if verified. The mention that the account data is simulated helps, but grounding is still weak because no official excerpts are provided and the answer makes unsupported specific claims about the device and menu path.
- AC-06: score=3 grounded=False — The answer addresses the question and is in English, but it incorrectly cites a non-official source [3] for a claim about the Getnet Brazil app and adds a detail (“no additional fee”) without grounding it in the provided Getnet excerpts. It also says it could not find a detailed explanation, which is partially true, but the response is too sparse to be considered complete. The official excerpts do support that anticipation can be done in the Getnet Brasil app and that Automática is subject to individual approval, but the answer should have relied only on those excerpts and avoided the unsupported citation.

_The judge (same model family) is not yet calibrated against human labels; treat it as a regression signal, not ground truth._
