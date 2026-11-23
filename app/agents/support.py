"""Customer Support agent: tool calling over the authenticated merchant's (mock) data, followed by a reviewer that
checks the answer against the tool results and triggers one bounded revision (REQ-SUP-01, SEC-02, ORC-07)."""

import json

from pydantic import BaseModel

from app.llm import LLMClient
from app.schemas import AgentResult, Source, Task
from app.tools.customer_tools import TOOL_SCHEMAS, execute_tool

REVIEW_INSTRUCTIONS = """\
You review a support answer before it is sent. Compare it with the tool results (the only source of account facts).
consistent=false if the answer states any amount, date, status, code, terminal or account detail that is absent from or
contradicts the tool results, omits that the data is simulated, or claims an action was executed when the tool only
opened a request. List the concrete problems in issues ("" when consistent).
"""

REVISE_INSTRUCTIONS = """\
Rewrite the support answer fixing the listed issues. Use only facts from the tool results, keep it short and
step-by-step, keep the user's language ({language}), and keep the note that the account data is simulated.
"""


class Review(BaseModel):
    consistent: bool
    issues: str


class Revision(BaseModel):
    answer: str

SUPPORT_INSTRUCTIONS = """\
You are Getnet's Customer Support Agent for merchants. The tools return the CURRENT authenticated merchant's data;
you cannot access any other customer.
- Call the relevant tool(s) before answering; do not ask the user for details a tool can find:
  card machine problems (connectivity, offline, errors, declines) → diagnose_terminal (with terminal_id and
  error_code null it checks every machine and today's declines); when money from sales will be deposited (yesterday,
  today, this week) → ALWAYS get_sales_and_settlements, even if you also read the profile;
  plan, bank account or settlement rules → get_merchant_profile.
- You never execute actions that move money. When the merchant asks to advance (antecipar) receivables, call
  request_receivables_advance with the amount; it only opens a request. Tell the user it is pending approval by a
  human specialist and that nothing was executed yet.
- Use ONLY facts from tool results or the official documentation excerpts provided; never invent amounts, dates,
  codes or policies. Cite documentation excerpts as [n].
- Tool data is simulated (data_source = MOCK): say briefly that the account data is simulated.
- If the user asks about another customer or account id, refuse that part and do not call tools for it.
- If a tool returns customer_not_found, say the account was not found and suggest contacting support.
- Short, step-by-step guidance. Answer in the user's language ({language}).
"""


class SupportAgent:
    def __init__(self, llm: LLMClient, review: bool = True):
        self._llm = llm
        self._review = review

    def handle(self, user_id: str, task: Task, language: str, history: str, kb_context: str = "",
               kb_sources: list[Source] | None = None) -> AgentResult:
        user_input = (f"<conversation_history>\n{history or '(none)'}\n</conversation_history>\n\n"
                      f"<user_request>\n{task.query}\n</user_request>")
        if kb_context:
            user_input += f"\n\n<official_documentation>\n{kb_context}\n</official_documentation>"
        results: list[dict] = []

        def execute(name: str, args: dict) -> dict:
            result = execute_tool(user_id, name, args)
            results.append(result)
            return result

        answer, calls = self._llm.run_tools(SUPPORT_INSTRUCTIONS.format(language=language), user_input, TOOL_SCHEMAS,
                                            execute)
        review_note = ""
        if self._review and results:  # observe → critique → refine, bounded to one revision
            evidence = f"<tool_results>\n{json.dumps(results, default=str, ensure_ascii=False)[:12000]}\n</tool_results>"
            verdict = self._llm.parse(REVIEW_INSTRUCTIONS, f"{evidence}\n<answer>\n{answer}\n</answer>", Review)
            review_note = " review=consistent"
            if not verdict.consistent:
                answer = self._llm.parse(REVISE_INSTRUCTIONS.format(language=language),
                                         f"{evidence}\n<answer>\n{answer}\n</answer>\n<issues>\n{verdict.issues}\n</issues>",
                                         Revision).answer
                review_note = f" review=revised ({verdict.issues[:80]})"
        # Approval gate: money-moving requests become a human ticket instead of an execution.
        pending = [r for r in results if r.get("status") == "pending_human_approval"]
        return AgentResult(agent="support", intent=task.intent, answer=answer, tools_used=calls,
                           sources=kb_sources or [], needs_handoff=bool(pending),
                           handoff_reason="approval_required" if pending else None, pending_actions=pending,
                           detail=f"tools={[c.name for c in calls]}{review_note}" + (" approval_required" if pending else ""))
