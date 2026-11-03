"""Customer Support Agent: tool-calling over the user's (mock) data (REQ-SUP-01)."""

from app.llm import LLMClient
from app.schemas import AgentResult, Source
from app.tools.customer_tools import TOOL_SCHEMAS, execute_tool

SUPPORT_INSTRUCTIONS = """\
You are Getnet's Customer Support Agent for merchants. Resolve the merchant's issue using the tools,
which return the CURRENT merchant's data (you cannot and must not access other customers).

Rules:
- Always call the relevant tool(s) before answering questions about sales, deposits, terminals or errors.
- Use ONLY facts returned by tools or the official documentation excerpts provided. Never invent amounts,
  dates, codes or policies.
- Tool data is simulated (data_source = MOCK): say briefly that the account data is simulated.
- You can only access the authenticated merchant's own account. If the user asks about another
  customer or account id, refuse that part and do not call tools for it.
- If a tool returns customer_not_found, say you could not find the account and suggest contacting support.
- Give concrete, short, step-by-step guidance. Answer in the user's language ({language}).
- If official documentation excerpts are provided, use them and cite them as [n].
"""


class SupportAgent:
    def __init__(self, llm: LLMClient):
        self._llm = llm

    def handle(self, user_id: str, message: str, language: str, kb_context: str = "",
               kb_sources: list[Source] | None = None) -> AgentResult:
        user_input = f"<user_message>\n{message}\n</user_message>"
        if kb_context:
            user_input += f"\n\n<official_documentation>\n{kb_context}\n</official_documentation>"
        answer, calls = self._llm.run_tools(
            SUPPORT_INSTRUCTIONS.format(language=language),
            user_input,
            TOOL_SCHEMAS,
            lambda name, args: execute_tool(user_id, name, args),
        )
        return AgentResult(agent="support", answer=answer, tools_used=calls, sources=kb_sources or [])
