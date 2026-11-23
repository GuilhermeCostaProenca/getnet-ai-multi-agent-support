"""Human Escalation agent: persistent tickets, operator workflow and pause/resume of the conversation (HITL-01..03)."""

from typing import Any

from app.agents.policy import policy_text
from app.schemas import Handoff
from app.storage import NotFound, Store


class Conflict(Exception):
    pass


class HandoffService:
    CONTEXT_TURNS = 8

    def __init__(self, store: Store):
        self._store = store

    def escalate(self, conversation_id: str, user_id: str, reason: str, language: str | None,
                 details: dict[str, Any] | None = None) -> tuple[str, Handoff]:
        ticket = self._store.active_ticket(conversation_id)
        if ticket is None:
            recent = [{"role": m["role"], "content": m["content"]}
                      for m in self._store.messages(conversation_id, self.CONTEXT_TURNS)]
            context = {"reason": reason, "language": language, "recent_messages": recent, **(details or {})}
            ticket = self._store.create_ticket(conversation_id, user_id, reason, context)
        self._store.set_status(conversation_id, "awaiting_human")
        return (policy_text("escalated", language, ticket=ticket["id"]),
                Handoff(required=True, ticket_id=ticket["id"], reason=reason))

    def list(self, status: str | None = None) -> list[dict[str, Any]]:
        return self._store.list_tickets(status)

    def _ticket(self, ticket_id: str) -> dict[str, Any]:
        ticket = self._store.get_ticket(ticket_id)
        if ticket is None:
            raise NotFound("ticket")
        return ticket

    def claim(self, ticket_id: str, operator_id: str) -> dict[str, Any]:
        ticket = self._ticket(ticket_id)
        if ticket["status"] == "claimed" and ticket["operator_id"] == operator_id:
            return ticket
        if ticket["status"] != "open":
            raise Conflict(f"ticket is {ticket['status']}")
        self._store.update_ticket(ticket_id, status="claimed", operator_id=operator_id)
        return self._ticket(ticket_id)

    def _owned(self, ticket_id: str, operator_id: str) -> dict[str, Any]:
        ticket = self._ticket(ticket_id)
        if ticket["status"] != "claimed" or ticket["operator_id"] != operator_id:
            raise Conflict("claim the ticket before acting on it")
        return ticket

    def reply(self, ticket_id: str, operator_id: str, message: str) -> dict[str, Any]:
        ticket = self._owned(ticket_id, operator_id)
        self._store.add_message(ticket["conversation_id"], "operator", message, agent=f"operator:{operator_id}")
        return ticket

    def resolve(self, ticket_id: str, operator_id: str, resolution: str) -> dict[str, Any]:
        ticket = self._owned(ticket_id, operator_id)
        self._store.update_ticket(ticket_id, status="resolved", resolution=resolution)
        # The note stays in the history so the agents resume with the human outcome in context.
        self._store.add_message(ticket["conversation_id"], "system",
                                f"Human support closed ticket {ticket_id}: {resolution}", agent="handoff")
        self._store.set_status(ticket["conversation_id"], "active")
        return self._ticket(ticket_id)
