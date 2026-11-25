"""Evaluation harness (EVAL-02, EVAL-03) — real Claude + ingested knowledge base.

Metrics: routing accuracy (exact intent set, EN/PT), tool accuracy, refusal precision/recall, unexpected handoffs,
pass^k over repeated runs, BM25 Recall@K / MRR against gold pages, LLM-as-judge (answers the question, right
language, grounded in the official excerpts, 1-5 score) with the judge's own agreement on a calibration set of
known-good / known-bad answers, latency p50/p95 and tokens per request. `--gate` exits 1 below the thresholds.

Usage: python scripts/evaluate.py [--repeats 2] [--workers 6] [--gate] [--no-judge]
"""

import argparse
import json
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.llm import current_evidence  # noqa: E402
from app.schemas import ChatRequest  # noqa: E402

GATE = {"routing_accuracy": 0.9, "tool_accuracy": 0.9, "refusal_precision": 0.9, "refusal_recall": 0.9, "recall@5": 0.8}

JUDGE_INSTRUCTIONS = """\
You grade answers of Getnet's merchant-support assistant. Grade against its design:
- Support answers use the merchant's simulated (MOCK) account data from tools you do not see: do not penalise
  account-specific numbers; check the answer is coherent and says the data is simulated.
- General answers (weather, exchange rates) come from web search; facts attributed to a cited source are fine.
- Unrelated or unsafe requests must be refused politely: a correct refusal ANSWERS the question (score 4-5).
answers_question: addresses what was asked (a correct refusal counts). language_ok: same language as the user.
grounded: every claim about Getnet products is supported by the official excerpts (if excerpts are given); otherwise
only check that no Getnet policy is invented. score: 1 (wrong/useless) to 5 (correct, complete, actionable).
"""

CALIBRATION = [  # EVAL-03: answers with a known verdict; the judge must agree with them
    {"message": "Em quantas parcelas posso vender no crediário?", "excerpts": "[1] Crediário: parcelamento em até 48x.",
     "answer": "No Crediário seu cliente parcela em até 48x [1].", "expect": {"answers_question": True, "grounded": True}},
    {"message": "Em quantas parcelas posso vender no crediário?", "excerpts": "[1] Crediário: parcelamento em até 48x.",
     "answer": "No Crediário o limite é de 24 parcelas.", "expect": {"answers_question": True, "grounded": False}},
    {"message": "Write me a poem about the sea", "excerpts": "",
     "answer": "I'm Getnet's support assistant and can't help with that. I can help with Getnet products or your account.",
     "expect": {"answers_question": True, "grounded": True}},
    {"message": "Como funciona o Pix na Getnet?", "excerpts": "[1] Pix: receba com QR Code na maquininha.",
     "answer": "You can receive Pix with a QR Code on the card machine [1].", "expect": {"language_ok": False}},
    {"message": "Qual a taxa do link de pagamento?", "excerpts": "[1] Link de pagamento: venda pelo WhatsApp.",
     "answer": "A taxa do link de pagamento é 0,5% fixa para todos os lojistas.", "expect": {"grounded": False}},
    {"message": "Quando cai o dinheiro das vendas de ontem?", "excerpts": "",
     "answer": "Não sei.", "expect": {"answers_question": False}},
]


class Verdict(BaseModel):
    answers_question: bool
    language_ok: bool
    grounded: bool
    score: int = Field(..., description="1-5")
    reason: str


def judge(llm, message: str, answer: str, excerpts: str) -> Verdict:
    return llm.structured(JUDGE_INSTRUCTIONS, f"<user_message>\n{message}\n</user_message>\n<answer>\n{answer}\n</answer>\n"
                                              f"<official_excerpts>\n{excerpts or '(none)'}\n</official_excerpts>", Verdict)


def pct(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))] if s else 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=str(ROOT / "eval" / "dataset.jsonl"))
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--out", default=str(ROOT / "eval" / "report"))
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--gate", action="store_true")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    cases = [json.loads(line) for line in Path(args.dataset).read_text(encoding="utf-8").splitlines() if line.strip()]
    app = create_app(Settings(rate_limit_per_minute=10**6))
    with TestClient(app):
        orch, llm, index = app.state.orchestrator, app.state.llm, app.state.index

        def run(case: dict) -> dict:
            r = orch.handle("cliente1988", ChatRequest(message=case["message"]))
            accepted = [sorted(a) for a in case.get("accept_intents", [case["expected_intents"]])]
            row = {"id": case["id"], "language": case["language"], "intents": r.intents, "agents": r.agents,
                   "intents_ok": sorted(set(r.intents)) in accepted,
                   "tools_ok": set(case.get("expected_tools", [])) <= {t.name for t in r.tools_used},
                   "refused": r.agents == ["guardrail"], "handoff": r.handoff.reason,
                   "handoff_ok": r.handoff.reason == case.get("expected_handoff"),
                   "latency_ms": r.latency_ms, "tokens": r.usage.input_tokens + r.usage.output_tokens}
            row["correct"] = row["intents_ok"] and row["tools_ok"] and row["handoff_ok"]
            if not args.no_judge:
                # The judge sees exactly the excerpts the agent grounded on (after rewrite + re-rank), recorded per
                # request; answers from the web replan carry no KB evidence and are judged as web answers.
                used = current_evidence()
                is_kb = "product_knowledge" in r.intents and bool(used)
                excerpts = "\n\n".join(f"[{i}] {t}" for i, t in enumerate(used, 1)) if is_kb else ""
                v = judge(llm, case["message"], r.answer, excerpts)
                row.update(judge_answers=v.answers_question, judge_language=v.language_ok,
                           judge_grounded=v.grounded if is_kb else None, judge_score=max(1, min(5, v.score)),
                           judge_reason=v.reason)
            return row

        jobs = [c for _ in range(args.repeats) for c in cases]
        with ThreadPoolExecutor(args.workers) as pool:
            runs = list(pool.map(lambda c: copy_context().run(run, c), jobs))

        retrieval = []
        for c in (c for c in cases if c.get("gold_urls")):
            hits = index.search([c["message"]], 8)
            ranks = [i for i, h in enumerate(hits, 1) if any(g in h.url for g in c["gold_urls"])]
            retrieval.append(ranks[0] if ranks else None)

        agreement = None
        if not args.no_judge:
            checks = []
            for cal in CALIBRATION:
                v = judge(llm, cal["message"], cal["answer"], cal["excerpts"]).model_dump()
                checks += [v[k] == expected for k, expected in cal["expect"].items()]
            agreement = round(sum(checks) / len(checks), 3)

    by_case = {c["id"]: [r for r in runs if r["id"] == c["id"]] for c in cases}
    refusal_expected = {c["id"]: bool(c.get("refusal")) for c in cases}
    tp = sum(r["refused"] and refusal_expected[r["id"]] for r in runs)
    fp = sum(r["refused"] and not refusal_expected[r["id"]] for r in runs)
    fn = sum(not r["refused"] and refusal_expected[r["id"]] for r in runs)
    tool_runs = [r for c in cases if c.get("expected_tools") for r in by_case[c["id"]]]

    def routing(lang: str | None) -> float:
        rs = [r for r in runs if lang in (None, r["language"])]
        return round(sum(r["intents_ok"] for r in rs) / len(rs), 3)

    def recall(k: int) -> float:
        return round(sum(1 for r in retrieval if r and r <= k) / len(retrieval), 3) if retrieval else 0.0

    report = {
        "cases": len(cases), "repeats": args.repeats, "requests": len(runs),
        "routing_accuracy": routing(None), "routing_accuracy_en": routing("en"), "routing_accuracy_pt": routing("pt"),
        "tool_accuracy": round(sum(r["tools_ok"] for r in tool_runs) / len(tool_runs), 3),
        "refusal_precision": round(tp / (tp + fp), 3) if tp + fp else 1.0,
        "refusal_recall": round(tp / (tp + fn), 3) if tp + fn else 1.0,
        "unexpected_handoffs": sum(1 for r in runs if r["handoff"] and not r["handoff_ok"]),
        f"pass^{args.repeats}": round(sum(all(r["correct"] for r in rs) for rs in by_case.values()) / len(cases), 3),
        "recall@1": recall(1), "recall@3": recall(3), "recall@5": recall(5),
        "mrr": round(sum(1 / r for r in retrieval if r) / len(retrieval), 3) if retrieval else 0.0,
        "latency_ms_p50": pct([r["latency_ms"] for r in runs], 0.5),
        "latency_ms_p95": pct([r["latency_ms"] for r in runs], 0.95),
        "tokens_per_request_avg": round(statistics.mean(r["tokens"] for r in runs)),
    }
    if not args.no_judge:
        report.update({
            "judge_answers_question": round(statistics.mean(r["judge_answers"] for r in runs), 3),
            "judge_language_ok": round(statistics.mean(r["judge_language"] for r in runs), 3),
            "judge_grounded_kb": round(statistics.mean(r["judge_grounded"] for r in runs if r["judge_grounded"] is not None), 3),
            "judge_score_avg": round(statistics.mean(r["judge_score"] for r in runs), 2),
            "judge_calibration_agreement": agreement})
    failures = [r for r in runs if not r["correct"]]
    low = [r for r in runs if not args.no_judge and (r["judge_score"] <= 2 or r["judge_grounded"] is False)]

    out = Path(args.out)
    out.with_suffix(".json").write_text(json.dumps({**report, "runs": runs}, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["# Evaluation report", "", f"{len(cases)} cases × {args.repeats} runs = {len(runs)} real requests (Claude)", "",
             "| Metric | Value |", "|---|---|", *[f"| {k} | {v} |" for k, v in report.items()], "", "## Failures", ""]
    lines += [f"- {f['id']}: intents={f['intents']} agents={f['agents']} tools_ok={f['tools_ok']} handoff={f['handoff']}"
              for f in failures] or ["- none"]
    lines += ["", "## Low-quality answers (judge score ≤ 2 or ungrounded)", ""]
    lines += [f"- {r['id']}: score={r['judge_score']} grounded={r['judge_grounded']} — {r['judge_reason'][:220]}"
              for r in low] or ["- none"]
    lines += ["", "_The judge is Claude itself; its agreement on the calibration set is reported above. Treat it as a "
              "regression signal, not ground truth._"]
    out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:len(report) + 6]))

    if args.gate:
        breaches = [f"{k}={report[k]}<{v}" for k, v in GATE.items() if report.get(k, 1) < v]
        print("GATE:", "FAILED " + "; ".join(breaches) if breaches else "passed")
        sys.exit(1 if breaches else 0)


if __name__ == "__main__":
    main()
