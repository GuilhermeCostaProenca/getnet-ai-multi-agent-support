"""Escalation agent and operator workflow (HITL-01..03): persistent tickets with context, paused conversations,
claim → reply → resolve, then the agents resume with the human outcome in history."""

from typing import Any

from app.storage import Conflict, NotFound, Store


class HandoffService:
    def __init__(self, store: Store):
        self._store = store

    def escalate(self, cid: str, user_id: str, reason: str, language: str, details: dict[str, Any]) -> str:
        active = self._store.active_ticket(cid)
        if active:
            return active["id"]
        recent = [{"role": m["role"], "content": m["content"][:400]} for m in self._store.messages(cid)[-8:]]
        tid = self._store.create_ticket(cid, user_id, reason,
                                        {"reason": reason, "language": language, "recent_messages": recent, **details})
        self._store.update_conversation(cid, status="awaiting_human")
        return tid

    def list(self, status: str | None) -> list[dict[str, Any]]:
        return self._store.list_tickets(status)

    def _ticket(self, tid: str) -> dict[str, Any]:
        ticket = self._store.get_ticket(tid)
        if ticket is None:
            raise NotFound(tid)
        return ticket

    def claim(self, tid: str, operator: str) -> dict[str, Any]:
        ticket = self._ticket(tid)
        if ticket["status"] != "open":
            raise Conflict(f"ticket is {ticket['status']}")
        self._store.update_ticket(tid, status="claimed", operator_id=operator)
        return self._ticket(tid)

    def reply(self, tid: str, operator: str, message: str) -> dict[str, Any]:
        ticket = self._ticket(tid)
        if ticket["status"] != "claimed" or ticket["operator_id"] != operator:
            raise Conflict("claim the ticket first")
        self._store.add_message(ticket["conversation_id"], "operator", message, agent=operator)
        return ticket

    def resolve(self, tid: str, operator: str, resolution: str) -> dict[str, Any]:
        ticket = self._ticket(tid)
        if ticket["status"] not in ("open", "claimed"):
            raise Conflict(f"ticket is {ticket['status']}")
        self._store.update_ticket(tid, status="resolved", operator_id=operator, resolution=resolution)
        self._store.add_message(ticket["conversation_id"], "system",
                                f"Human support closed ticket {tid}: {resolution}", agent="escalation")
        self._store.update_conversation(ticket["conversation_id"], status="active", clarifications=0)
        return self._ticket(tid)
