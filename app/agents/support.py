"""Customer Support agent: tool calling over the authenticated merchant's (mock) data (REQ-SUP-01, SEC-02)."""

from app.llm import LLMClient
from app.schemas import AgentResult, Source, Task
from app.tools.customer_tools import TOOL_SCHEMAS, execute_tool

SUPPORT_INSTRUCTIONS = """\
You are Getnet's Customer Support Agent for merchants. The tools return the CURRENT authenticated merchant's data;
you cannot access any other customer.
- Call the relevant tool(s) before answering about sales, deposits, terminals or errors.
- Use ONLY facts from tool results or the official documentation excerpts provided; never invent amounts, dates,
  codes or policies. Cite documentation excerpts as [n].
- Tool data is simulated (data_source = MOCK): say briefly that the account data is simulated.
- If the user asks about another customer or account id, refuse that part and do not call tools for it.
- If a tool returns customer_not_found, say the account was not found and suggest contacting support.
- Short, step-by-step guidance. Answer in the user's language ({language}).
"""


class SupportAgent:
    def __init__(self, llm: LLMClient):
        self._llm = llm

    def handle(self, user_id: str, task: Task, language: str, history: str, kb_context: str = "",
               kb_sources: list[Source] | None = None) -> AgentResult:
        user_input = (f"<conversation_history>\n{history or '(none)'}\n</conversation_history>\n\n"
                      f"<user_request>\n{task.query}\n</user_request>")
        if kb_context:
            user_input += f"\n\n<official_documentation>\n{kb_context}\n</official_documentation>"
        answer, calls = self._llm.run_tools(SUPPORT_INSTRUCTIONS.format(language=language), user_input, TOOL_SCHEMAS,
                                            lambda name, args: execute_tool(user_id, name, args))
        return AgentResult(agent="support", intent=task.intent, answer=answer, tools_used=calls,
                           sources=kb_sources or [], detail=f"tools={[c.name for c in calls]}")
