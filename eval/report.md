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
| latency_ms_p50 | 8056 |
| latency_ms_p95 | 14367 |
| tokens_per_request_avg | 10354 |
| judge_answers_question | 1 |
| judge_language_ok | 1 |
| judge_grounded_kb | 0.381 |
| judge_score_avg | 4.1 |
| judge_calibration_agreement | 1.0 |

## Failures

- none

## Low-quality answers (judge score ≤ 2 or ungrounded)

- EN-01: score=2 grounded=False — The answer addresses the question in English, but many of its claims are not in the cited excerpts. Excerpt [1] does not mention Android 12, NFC, a device security review or the Vender > Get Tap path. The sales threshold
- EN-03: score=3 grounded=False — The answer addresses the question and is in English, matching the user. Selling via social networks with the link is supported by the excerpts, but WhatsApp is never named; the answer infers it, which is reasonable. Exce
- EN-04: score=2 grounded=False — The answer is in English and addresses the question. However, many claims are not supported by the excerpts. Pix on TEF Auttar and e-commerce, payment by biometrics, sign-up from 16/11/20 through Portal Minha Conta, the 
- EN-07: score=3 grounded=False — The answer addresses code 51 clearly and is in English. It also carefully flags that the insufficient-funds reading is industry-general, not Getnet's. However, none of the official excerpts support the claimed Getnet tab
- PT-01: score=2 grounded=False — The answer is in Portuguese and addresses the question, but many claims are not supported by the cited excerpts. Excerpts [1] and [2] contain no mention of Android 12, NFC, a security analysis, or the path Vender > Get T
- PT-03: score=3 grounded=False — The answer addresses the question in Portuguese, but it includes unsupported details. The excerpts support 'venda de onde estiver', a customisable link, antifraud, payment by debit/credit/Pix/wallet, and selling through 
- MIX-02: score=3 grounded=False — Both parts of the question are answered in Portuguese, and the weather forecast is attributed to Climatempo with a sensible caveat about the date. However, many of the Getnet Pix claims are not supported by the excerpts 
- EN-01: score=2 grounded=False — The answer addresses the question in English, but many of its citations are wrong or unsupported. Excerpt [1] does not mention Android 12, NFC, a security review, or the Vender > Get Tap path. Excerpt [3] does not list t
- EN-03: score=3 grounded=False — The answer addresses the question and is in the user's language (English). The excerpts support selling from anywhere, selling through social networks, a customizable link, antifraud protection, and debit, credit, Pix an
- EN-04: score=2 grounded=False — The answer addresses the question in English, but many of its claims are not supported by the cited excerpts. Unsupported: Pix on TEF Auttar and E-commerce [1]; biometrics and Get Tap Pix [2]; receipt timing varying by u
- PT-01: score=2 grounded=False — The answer is in Portuguese and addresses the question, but several claims are not supported by the excerpts or are cited to the wrong source. Unsupported claims: Android 12 or higher, NFC, a security analysis of the dev
- PT-03: score=3 grounded=False — The answer is in Portuguese and responds to the question directly. However, the excerpts never mention WhatsApp specifically, only 'redes sociais' and 'venda de onde estiver'. They also do not list boleto among the Link 
- MIX-02: score=3 grounded=False — Answers both parts in Portuguese, and the weather is attributed to Climatempo with a caveat about the date. However, several Pix claims are unsupported or wrongly cited: TEF Auttar and e-commerce Pix [1], biometria, timi

_The judge is Claude itself; its agreement on the calibration set is reported above. Treat it as a regression signal, not ground truth._
