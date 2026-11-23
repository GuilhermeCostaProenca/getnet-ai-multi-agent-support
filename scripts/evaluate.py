"""Evaluation harness (EVAL-02) — real LLM + ingested knowledge base.

Metrics: routing accuracy (exact intent set), tool accuracy, refusal precision/recall, consistency over repeated runs
(pass^k: a case passes only if every run is correct), RAG retrieval Recall@K and MRR against gold source pages,
PT×EN breakdown, latency p50/p95 and tokens per request.

Usage: python scripts/evaluate.py [--repeats 2] [--workers 6] [--out eval/report]
"""

import argparse
import json
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.schemas import ChatRequest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

JUDGE_INSTRUCTIONS = """\
You grade answers of a Getnet merchant-support assistant. Be strict and literal, but grade against THIS assistant's
design:
- Support answers use the merchant's account data returned by tools (simulated MOCK data that you do not see). Do not
  penalize account-specific numbers, dates, terminals or codes; check that the answer addresses the problem coherently
  and says the account data is simulated.
- General-information answers (weather, exchange rates) come from web search; facts attributed to a cited web source
  are acceptable.
- The assistant must refuse unrelated work (math, code, translations, poems, competitor comparisons). A polite refusal
  that redirects to what it can do is the CORRECT answer (score 4-5); answering such a request would be wrong.
- answers_question: the answer addresses what the user asked (a correct refusal of an out-of-scope or unsafe request
  counts as addressing it; asking a clarifying question does not).
- language_ok: the answer is in the language of the user's message.
- grounded: every factual claim about Getnet products is supported by the provided official excerpts; claims that come
  from tool data or a web source are acceptable when the answer says so. If no excerpts are provided, judge only
  whether the answer avoids inventing Getnet policies.
- score: 1 (useless or wrong) to 5 (correct, complete, actionable).
"""


class Verdict(BaseModel):
    answers_question: bool
    language_ok: bool
    grounded: bool
    score: int = Field(..., description="1-5")
    reason: str


def judge_answer(llm, kb_agent, case: dict, resp: dict) -> dict:
    """Grounding is judged only for knowledge-base answers, where official excerpts exist to compare against;
    support answers are checked against tool results by the in-product reviewer, web answers cite their sources."""
    is_kb = "product_knowledge" in resp["intents"] and "knowledge" in resp["agents"]
    excerpts = kb_agent.format_context(kb_agent.retrieve(case["message"])[:6]) if is_kb else ""
    user_input = (f"<user_message>\n{case['message']}\n</user_message>\n<answer>\n{resp['answer']}\n</answer>\n"
                  f"<sources>{[s['url'] for s in resp['sources']]}</sources>\n<official_excerpts>\n{excerpts}\n</official_excerpts>")
    v = llm.parse(JUDGE_INSTRUCTIONS, user_input, Verdict)
    return {"judge_answers": v.answers_question, "judge_language": v.language_ok,
            "judge_grounded": v.grounded if is_kb else None, "judge_score": max(1, min(5, v.score)),
            "judge_reason": v.reason}


def load_cases(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def judge(case: dict, resp: dict) -> dict:
    accepted = [sorted(a) for a in case.get("accept_intents", [case["expected_intents"]])]
    intents_ok = sorted(set(resp["intents"])) in accepted
    tools_ok = set(case.get("expected_tools", [])) <= {t["name"] for t in resp["tools_used"]}
    return {"intents_ok": intents_ok, "tools_ok": tools_ok, "correct": intents_ok and tools_ok,
            "refused": resp["agents"] == ["guardrail"], "handoff": resp["handoff"]["required"],
            "intents": resp["intents"], "agents": resp["agents"], "latency_ms": resp["latency_ms"],
            "tokens": resp["usage"]["input_tokens"] + resp["usage"]["output_tokens"]}


def retrieval_metrics(kb, llm, cases: list[dict], ks=(1, 3, 5, 8)) -> dict:
    rows = []
    for c in (c for c in cases if c.get("gold_urls")):
        hits = kb.query(llm.embed([c["message"]])[0], max(ks))
        ranks = [i for i, h in enumerate(hits, 1) if any(g in h.url for g in c["gold_urls"])]
        rows.append({"id": c["id"], "language": c["language"], "first_rank": ranks[0] if ranks else None,
                     "top_score": hits[0].score if hits else None})

    def agg(rs: list[dict]) -> dict:
        if not rs:
            return {}
        n = len(rs)
        out = {f"recall@{k}": round(sum(1 for r in rs if r["first_rank"] and r["first_rank"] <= k) / n, 3) for k in ks}
        out["mrr"] = round(sum(1 / r["first_rank"] for r in rs if r["first_rank"]) / n, 3)
        out["n"] = n
        return out

    return {"all": agg(rows), "en": agg([r for r in rows if r["language"] == "en"]),
            "pt": agg([r for r in rows if r["language"] == "pt"]), "per_case": rows}


def pct(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=str(ROOT / "eval" / "dataset.jsonl"))
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--out", default=str(ROOT / "eval" / "report"))
    parser.add_argument("--no-judge", action="store_true", help="skip the LLM-as-judge quality grading")
    args = parser.parse_args()

    cases = load_cases(Path(args.dataset))
    settings = Settings(rate_limit_per_minute=1_000_000)
    app = create_app(settings=settings)
    with TestClient(app):  # runs startup: stores, knowledge base (auto-ingest if empty)
        orch = app.state.orchestrator

        kb_agent = orch._knowledge

        def run(case: dict) -> dict:
            resp = orch.handle("cliente1988", ChatRequest(message=case["message"])).model_dump()
            row = {"id": case["id"], **judge(case, resp)}
            if not args.no_judge:
                row.update(judge_answer(app.state.llm, kb_agent, case, resp))
            return row

        runs: list[dict] = []
        for _ in range(args.repeats):
            with ThreadPoolExecutor(args.workers) as pool:
                runs += list(pool.map(lambda c: copy_context().run(run, c), cases))
        retrieval = retrieval_metrics(app.state.kb, app.state.llm, cases)

    by_case = {c["id"]: [r for r in runs if r["id"] == c["id"]] for c in cases}
    tool_runs = [r for c in cases if c.get("expected_tools") for r in by_case[c["id"]]]
    expected_refusal = {c["id"]: bool(c.get("refusal")) for c in cases}
    tp = sum(1 for r in runs if r["refused"] and expected_refusal[r["id"]])
    fp = sum(1 for r in runs if r["refused"] and not expected_refusal[r["id"]])
    fn = sum(1 for r in runs if not r["refused"] and expected_refusal[r["id"]])

    def routing(lang: str | None) -> float:
        rs = [r for c in cases if lang in (None, c["language"]) for r in by_case[c["id"]]]
        return round(sum(r["intents_ok"] for r in rs) / len(rs), 3)

    report = {
        "cases": len(cases), "repeats": args.repeats, "runs": len(runs),
        "routing_accuracy": routing(None), "routing_accuracy_en": routing("en"), "routing_accuracy_pt": routing("pt"),
        "tool_accuracy": round(sum(r["tools_ok"] for r in tool_runs) / len(tool_runs), 3) if tool_runs else None,
        "refusal_precision": round(tp / (tp + fp), 3) if tp + fp else None,
        "refusal_recall": round(tp / (tp + fn), 3) if tp + fn else None,
        f"pass^{args.repeats}": round(sum(all(r["correct"] for r in rs) for rs in by_case.values()) / len(cases), 3),
        "unexpected_handoffs": sum(1 for r in runs if r["handoff"]),
        **({} if args.no_judge else {
            "judge_answers_question": round(statistics.mean(r["judge_answers"] for r in runs), 3),
            "judge_language_ok": round(statistics.mean(r["judge_language"] for r in runs), 3),
            "judge_grounded_kb_answers": round(statistics.mean(
                r["judge_grounded"] for r in runs if r["judge_grounded"] is not None), 3),
            "judge_score_avg_1to5": round(statistics.mean(r["judge_score"] for r in runs), 2),
        }),
        "latency_ms_p50": pct([r["latency_ms"] for r in runs], 0.5),
        "latency_ms_p95": pct([r["latency_ms"] for r in runs], 0.95),
        "tokens_per_request_avg": round(statistics.mean(r["tokens"] for r in runs)),
        "retrieval": {k: v for k, v in retrieval.items() if k != "per_case"},
        "failures": [r for r in runs if not r["correct"]],
        "low_quality": [r for r in runs if not args.no_judge and (r["judge_score"] <= 2 or r["judge_grounded"] is False)],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps({**report, "retrieval_per_case": retrieval["per_case"]},
                                                   indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [f"# Evaluation report", "", f"{len(cases)} cases × {args.repeats} runs = {len(runs)} requests (real LLM)", "",
             "| Metric | Value |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in report.items() if k not in ("retrieval", "failures", "low_quality")]
    lines += ["", "## Retrieval (raw user message → top-8, gold = official page)", "", "| Slice | Recall@1 | Recall@3 | Recall@5 | Recall@8 | MRR | n |",
              "|---|---|---|---|---|---|---|"]
    for name in ("all", "en", "pt"):
        m = retrieval[name]
        if m:
            lines.append(f"| {name} | {m['recall@1']} | {m['recall@3']} | {m['recall@5']} | {m['recall@8']} | {m['mrr']} | {m['n']} |")
    lines += ["", "## Failures", ""] + ([f"- {f['id']}: intents={f['intents']} agents={f['agents']} tools_ok={f['tools_ok']}"
                                       for f in report["failures"]] or ["- none"])
    lines += ["", "## Low-quality answers (judge score ≤ 2 or not grounded)", ""] + (
        [f"- {r['id']}: score={r['judge_score']} grounded={r['judge_grounded']} — {r['judge_reason']}" for r in report["low_quality"]]
        or ["- none"])
    lines += ["", "_The judge (same model family) is not yet calibrated against human labels; treat it as a regression "
              "signal, not ground truth._"]
    out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
