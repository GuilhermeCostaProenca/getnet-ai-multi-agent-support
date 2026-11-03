"""Deterministic policy responses for blocked requests (GR-SCOPE-01, GR-INJ-01, GR-MOD-01).

Refusals are policy, not generation: no LLM call, predictable wording, zero cost.
"""

_TEXT = {
    "out_of_scope": {
        "en": "I'm Getnet's assistant, so I can only help with Getnet products and services, your account "
        "and card machine, or quick factual questions like weather or exchange rates. I can't help with "
        "that request, but feel free to ask me anything about your sales, Pix, payment link or your machine.",
        "pt": "Sou o assistente da Getnet e posso ajudar apenas com produtos e serviços Getnet, sua conta e "
        "sua maquininha, ou com perguntas rápidas como clima e câmbio. Não consigo ajudar com esse pedido, "
        "mas pode me perguntar sobre suas vendas, Pix, link de pagamento ou sua máquina.",
    },
    "unsafe": {
        "en": "I can't help with that request. If you suspect fraud or misuse of your account, contact Getnet "
        "immediately at 4002-4000 or 4003-4000 (capitals and metro areas).",
        "pt": "Não posso ajudar com esse pedido. Se você suspeita de fraude ou uso indevido da sua conta, fale "
        "com a Getnet imediatamente pelo 4002-4000 ou 4003-4000 (capitais e regiões metropolitanas).",
    },
    "blocked": {
        "en": "I can't process that message because it tries to change how I work. I'm Getnet's assistant: ask me "
        "about Getnet products, your sales and deposits, or your card machine.",
        "pt": "Não posso processar essa mensagem porque ela tenta alterar meu funcionamento. Sou o assistente da "
        "Getnet: pergunte sobre produtos Getnet, suas vendas e recebimentos ou sua maquininha.",
    },
    "pii_warning": {
        "en": "\n\nNote: for your security I removed sensitive data (card number, CPF or password) from your "
        "message. Never share it in chats.",
        "pt": "\n\nAtenção: por segurança, removi dados sensíveis (cartão, CPF ou senha) da sua mensagem. "
        "Nunca compartilhe esses dados em conversas.",
    },
}


def policy_text(kind: str, language: str | None) -> str:
    lang = "pt" if (language or "").startswith("pt") else "en"
    return _TEXT[kind][lang]
