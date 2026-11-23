"""Deterministic policy texts: refusals and status messages are templates, not generations."""

_TEXT = {
    "out_of_scope": {
        "en": "I'm Getnet's assistant: I can help with Getnet products and services, your sales, deposits and card "
              "machine, or quick factual questions like weather or exchange rates. I can't help with that request.",
        "pt": "Sou o assistente da Getnet: ajudo com produtos e serviços Getnet, suas vendas, recebimentos e "
              "maquininha, ou perguntas rápidas como clima e câmbio. Não consigo ajudar com esse pedido.",
    },
    "partial_out_of_scope": {
        "en": "About the rest of your message: that part is outside what Getnet's assistant can do, so I left it out.",
        "pt": "Sobre o restante da sua mensagem: essa parte está fora do que o assistente da Getnet pode fazer, então deixei de fora.",
    },
    "unsafe": {
        "en": "I can't help with that request. If you suspect fraud or misuse of your account, contact Getnet at "
              "4002-4000 or 4003-4000 (capitals and metro areas).",
        "pt": "Não posso ajudar com esse pedido. Se suspeita de fraude ou uso indevido da sua conta, fale com a Getnet "
              "pelo 4002-4000 ou 4003-4000 (capitais e regiões metropolitanas).",
    },
    "blocked": {
        "en": "I can't process that message because it tries to change how I work. Ask me about Getnet products, "
              "your sales and deposits, or your card machine.",
        "pt": "Não posso processar essa mensagem porque ela tenta alterar meu funcionamento. Pergunte sobre produtos "
              "Getnet, suas vendas e recebimentos ou sua maquininha.",
    },
    "clarify": {
        "en": "Could you give me a bit more detail about what you need?",
        "pt": "Pode me dar um pouco mais de detalhe sobre o que você precisa?",
    },
    "escalated": {
        "en": "I've forwarded your request to a human specialist (ticket {ticket}). Their reply will appear in this "
              "conversation.",
        "pt": "Encaminhei sua solicitação para um especialista humano (protocolo {ticket}). A resposta dele vai "
              "aparecer nesta conversa.",
    },
    "awaiting_human": {
        "en": "A human specialist is handling this conversation (ticket {ticket}). I've added your message to the "
              "ticket; their reply will appear here.",
        "pt": "Um especialista humano está cuidando desta conversa (protocolo {ticket}). Adicionei sua mensagem ao "
              "atendimento; a resposta dele vai aparecer aqui.",
    },
    "specialist_reply": {
        "en": "Latest reply from the specialist: {reply}",
        "pt": "Última resposta do especialista: {reply}",
    },
    "not_found": {
        "en": "I couldn't find this in Getnet's official content.",
        "pt": "Não encontrei essa informação no conteúdo oficial da Getnet.",
    },
    "pii_warning": {
        "en": "\n\nNote: for your security I removed sensitive data (card number, CPF or password) from your message. "
              "Never share it in chats.",
        "pt": "\n\nAtenção: por segurança removi dados sensíveis (cartão, CPF ou senha) da sua mensagem. Nunca "
              "compartilhe esses dados em conversas.",
    },
}


_PT_HINTS = ("ã", "ç", "õ", "é", " não ", " você", " minha", " meu ", " está", " obrigad", " quando ", " como ")


def guess_language(text: str) -> str:
    """Cheap fallback used only on paths that never call the planner (paused or blocked conversations)."""
    padded = f" {text.lower()} "
    return "pt" if any(h in padded for h in _PT_HINTS) else "en"


def lang_of(language: str | None) -> str:
    return "pt" if (language or "").lower().startswith("pt") else "en"


def policy_text(kind: str, language: str | None, **fmt: str) -> str:
    text = _TEXT[kind][lang_of(language)]
    return text.format(**fmt) if fmt else text
