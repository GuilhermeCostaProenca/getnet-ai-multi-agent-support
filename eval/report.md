# Evaluation report

25 cases × 2 runs = 50 real requests (Claude)

| Metric | Value |
|---|---|
| cases | 25 |
| repeats | 2 |
| requests | 50 |
| routing_accuracy | 1.0 |
| routing_accuracy_en | 1.0 |
| routing_accuracy_pt | 1.0 |
| tool_accuracy | 1.0 |
| refusal_precision | 1.0 |
| refusal_recall | 1.0 |
| unexpected_handoffs | 0 |
| pass^2 | 1.0 |
| recall@1 | 0.556 |
| recall@3 | 0.889 |
| recall@5 | 1.0 |
| mrr | 0.713 |
| latency_ms_p50 | 7574 |
| latency_ms_p95 | 13491 |
| tokens_per_request_avg | 10379 |
| judge_answers_question | 1 |
| judge_language_ok | 1 |
| judge_grounded_kb | 0.95 |
| judge_score_avg | 4.84 |
| judge_calibration_agreement | 1.0 |

## Failures

- none

## Low-quality answers (judge score ≤ 2 or ungrounded)

- EN-01: score=4 grounded=False — The answer is a clear, well-cited comparison in English: Get Tap is a phone app feature, and it covers requirements, monthly costs, payments accepted and rates. One overreach: the excerpts say selling the listed monthly 

_The judge is Claude itself; its agreement on the calibration set is reported above. Treat it as a regression signal, not ground truth._
