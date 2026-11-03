"""Human Escalation Agent (REQ-ESC-01, bonus).

Opens a handoff ticket in an in-memory queue. In production this would publish to a
ticketing system / contact-center queue; here the queue is a MOCK, stated in the answer.
"""

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.schemas import AgentResult, Handoff

_MESSAGES = {
    "pt": "Não consegui resolver isso com segurança por aqui, então encaminhei sua solicitação para um "
    "especialista humano (protocolo {ticket}, fila simulada). Se preferir, fale com a Getnet pelo "
    "4002-4000 ou 4003-4000 (capitais e regiões metropolitanas).",
    "en": "I couldn't resolve this confidently, so I forwarded your request to a human specialist "
    "(ticket {ticket}, simulated queue). You can also reach Getnet at 4002-4000 or 4003-4000 (capitals and metro areas).",
}


@dataclass
class Ticket:
    ticket_id: str
    user_id: str
    reason: str
    summary: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class EscalationAgent:
    def __init__(self) -> None:
        self._queue: list[Ticket] = []
        self._lock = threading.Lock()

    @property
    def queue(self) -> list[Ticket]:
        return list(self._queue)

    def escalate(self, user_id: str, message: str, reason: str, language: str | None) -> tuple[AgentResult, Handoff]:
        ticket = Ticket(ticket_id=f"HO-{uuid.uuid4().hex[:8].upper()}", user_id=user_id, reason=reason, summary=message[:300])
        with self._lock:
            self._queue.append(ticket)
        template = _MESSAGES["pt" if (language or "").startswith("pt") else "en"]
        result = AgentResult(agent="escalation", answer=template.format(ticket=ticket.ticket_id))
        return result, Handoff(required=True, ticket_id=ticket.ticket_id, reason=reason)
