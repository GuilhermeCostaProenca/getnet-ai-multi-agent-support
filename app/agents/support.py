"""Customer Support agent (REQ-SUP-01, ORC-05, HITL-05): tool loop over the authenticated merchant's MOCK data,
a reviewer that checks the answer against the tool results (one bounded revision) and an approval gate."""

import json

from app.agents.base import AgentResult
from app.llm import LLM
from app.schemas import Review, Revision, Task
from app.tools.customer import TOOL_SPECS, execute_tool

SUPPORT_INSTRUCTIONS = """\
You are Getnet's Customer Support Agent. The tools return the CURRENT authenticated merchant's data only.
- Call the right tool before answering; never ask for data a tool can find:
  machine offline / not connecting / declining cards → diagnose_terminal (omit terminal_id to check all);
  when money from sales will be deposited → get_sales_and_settlements;
  plan, bank account, settlement rules → get_merchant_profile;
  advance (antecipar) an amount → request_receivables_advance: it only OPENS a request for human approval — say it is
  pending approval and nothing was executed.
- Use only facts from tool results. Never invent amounts, dates, codes or policies.
- The data is simulated (data_source MOCK): say so briefly.
- Refuse requests about other customers. If a tool returns customer_not_found or an error, say so and suggest support.
- Short, step-by-step answer in the user's language ({language}).
"""

REVIEW_INSTRUCTIONS = """\
Check a support answer against the tool results (the only source of account facts). consistent=false if it states an
amount, date, status, code or terminal that is missing from or contradicts the tool results, omits that the data is
simulated, or claims an action was executed when it was only requested. List the problems in issues ('' if none).
"""

REVISE_INSTRUCTIONS = """\
Rewrite the support answer fixing the listed issues, using only facts from the tool results. Keep it short, keep the
note that the data is simulated, answer in {language}.
"""


class SupportAgent:
    def __init__(self, llm: LLM, tool_timeout: float = 5.0, review: bool = True):
        self._llm = llm
        self._timeout = tool_timeout
        self._review = review

    def handle(self, user_id: str, task: Task, language: str, history: str) -> AgentResult:
        results: list[dict] = []

        def execute(name: str, args: dict) -> dict:
            out = execute_tool(user_id, name, args, self._timeout)  # identity bound server-side (SEC-02)
            results.append(out)
            return out

        user_input = (f"<conversation_history>\n{history or '(none)'}\n</conversation_history>\n\n"
                      f"<request>\n{task.query}\n</request>")
        answer, calls = self._llm.tool_loop(SUPPORT_INSTRUCTIONS.format(language=language), user_input, TOOL_SPECS,
                                            execute)
        note = ""
        if self._review and results:
            evidence = f"<tool_results>\n{json.dumps(results, default=str, ensure_ascii=False)[:12000]}\n</tool_results>"
            verdict = self._llm.structured(REVIEW_INSTRUCTIONS, f"{evidence}\n<answer>\n{answer}\n</answer>", Review)
            note = " review=consistent"
            if not verdict.consistent:
                answer = self._llm.structured(
                    REVISE_INSTRUCTIONS.format(language=language),
                    f"{evidence}\n<answer>\n{answer}\n</answer>\n<issues>\n{verdict.issues}\n</issues>", Revision).answer
                note = f" review=revised ({verdict.issues[:60]})"
        pending = [r for r in results if r.get("status") == "pending_human_approval"]
        return AgentResult("support", task.intent, answer, tools=calls, needs_handoff=bool(pending),
                           handoff_reason="approval_required" if pending else None, pending_actions=pending,
                           detail=f"tools={[c.name for c in calls]}{note}")
