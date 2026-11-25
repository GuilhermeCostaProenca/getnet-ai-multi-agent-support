"""Shared agent types and policy texts (fixed wording, never generated)."""

import re
from dataclasses import dataclass, field
from typing import Any

from app.schemas import Source, ToolCall


@dataclass
class AgentResult:
    agent: str
    intent: str
    answer: str
    sources: list[Source] = field(default_factory=list)
    tools: list[ToolCall] = field(default_factory=list)
    needs_handoff: bool = False
    handoff_reason: str | None = None
    pending_actions: list[dict[str, Any]] = field(default_factory=list)
    detail: str = ""


POLICY = {
    "out_of_scope": {
        "pt": "Sou o assistente de atendimento da Getnet e não consigo ajudar com isso. Posso ajudar com produtos Getnet, "
              "sua conta (vendas, repasses, maquininhas) ou informações gerais como clima e câmbio.",
        "en": "I'm Getnet's support assistant and can't help with that. I can help with Getnet products, your account "
              "(sales, deposits, card machines) or general information such as weather and exchange rates."},
    "partial_out_of_scope": {
        "pt": "Sobre o restante do seu pedido: isso está fora do que posso fazer como assistente da Getnet.",
        "en": "About the rest of your request: that is outside what I can do as Getnet's assistant."},
    "unsafe": {
        "pt": "Não posso ajudar com isso. Se você suspeita de fraude ou uso indevido de cartão, fale com a Central de "
              "Atendimento Getnet.",
        "en": "I can't help with that. If you suspect fraud or card misuse, please contact Getnet's support center."},
    "blocked": {
        "pt": "Não posso processar essa mensagem porque ela tenta alterar meu funcionamento. Pergunte sobre produtos "
              "Getnet ou sobre a sua conta.",
        "en": "I can't process that message because it tries to change how I work. Ask me about Getnet products or "
              "your account."},
    "escalated": {
        "pt": "Encaminhei seu atendimento a um especialista humano (protocolo {ticket}). A resposta aparecerá aqui "
              "nesta conversa.",
        "en": "I've forwarded your request to a human specialist (ticket {ticket}). Their reply will appear here in "
              "this conversation."},
    "awaiting_human": {
        "pt": "Seu atendimento está com um especialista humano (protocolo {ticket}). Assim que ele responder, a "
              "mensagem aparecerá aqui.",
        "en": "Your request is with a human specialist (ticket {ticket}). Their reply will appear here."},
    "specialist_reply": {"pt": "Resposta do especialista: {reply}", "en": "Specialist's reply: {reply}"},
    "not_found": {
        "pt": "Não encontrei essa informação nos conteúdos oficiais da Getnet.",
        "en": "I couldn't find this in Getnet's official content."},
    "pii_warning": {
        "pt": "\n\nPor segurança, removi dados sensíveis (cartão/CPF/senha) da sua mensagem. Nunca compartilhe esses "
              "dados no chat.",
        "en": "\n\nFor your security I removed sensitive data (card/CPF/password) from your message. Never share it "
              "in chat."},
}

_PT_HINTS = re.compile(r"[ãõçáéíóúâê]|\b(não|você|qual|quando|como|minha|meu|vendas|maquininha|obrigad)\b", re.I)


def guess_language(text: str) -> str:
    return "pt" if _PT_HINTS.search(text) else "en"


def lang_key(language: str | None) -> str:
    return "pt" if (language or "").lower().startswith("pt") else "en"


def policy(kind: str, language: str | None, **fmt: str) -> str:
    return POLICY[kind][lang_key(language)].format(**fmt)
