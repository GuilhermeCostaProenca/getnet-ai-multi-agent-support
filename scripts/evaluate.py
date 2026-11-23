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

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.schemas import ChatRequest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


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
    args = parser.parse_args()

    cases = load_cases(Path(args.dataset))
    settings = Settings(rate_limit_per_minute=1_000_000)
    app = create_app(settings=settings)
    with TestClient(app):  # runs startup: stores, knowledge base (auto-ingest if empty)
        orch = app.state.orchestrator

        def run(case: dict) -> dict:
            resp = orch.handle("cliente1988", ChatRequest(message=case["message"])).model_dump()
            return {"id": case["id"], **judge(case, resp)}

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
        "latency_ms_p50": pct([r["latency_ms"] for r in runs], 0.5),
        "latency_ms_p95": pct([r["latency_ms"] for r in runs], 0.95),
        "tokens_per_request_avg": round(statistics.mean(r["tokens"] for r in runs)),
        "retrieval": {k: v for k, v in retrieval.items() if k != "per_case"},
        "failures": [r for r in runs if not r["correct"]],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps({**report, "retrieval_per_case": retrieval["per_case"]},
                                                   indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [f"# Evaluation report", "", f"{len(cases)} cases × {args.repeats} runs = {len(runs)} requests (real LLM)", "",
             "| Metric | Value |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in report.items() if k not in ("retrieval", "failures")]
    lines += ["", "## Retrieval (raw user message → top-8, gold = official page)", "", "| Slice | Recall@1 | Recall@3 | Recall@5 | Recall@8 | MRR | n |",
              "|---|---|---|---|---|---|---|"]
    for name in ("all", "en", "pt"):
        m = retrieval[name]
        if m:
            lines.append(f"| {name} | {m['recall@1']} | {m['recall@3']} | {m['recall@5']} | {m['recall@8']} | {m['mrr']} | {m['n']} |")
    lines += ["", "## Failures", ""] + ([f"- {f['id']}: intents={f['intents']} agents={f['agents']} tools_ok={f['tools_ok']}"
                                       for f in report["failures"]] or ["- none"])
    out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
