"""Curated knowledge-base sources (ADR-08).

The example questions (Get Clássica/Smart, crediário, Pix) live on the Brazilian product site; its page sitemap is
broken, so the list is curated. `/duvidas/` (the FAQ) is the densest source. getnet.net/en adds the global view.
"""

GETNET_BR = "https://site.getnet.com.br"
GETNET_GLOBAL = "https://www.getnet.net/en"

SOURCE_URLS: list[str] = [
    f"{GETNET_BR}/duvidas/",
    f"{GETNET_BR}/todas-as-maquininhas/",
    f"{GETNET_BR}/pix/",
    f"{GETNET_BR}/crediario/",
    f"{GETNET_BR}/link-de-pagamento/",
    f"{GETNET_BR}/pagamentos-pelo-whatsapp/",
    f"{GETNET_BR}/conta-digital/",
    f"{GETNET_BR}/boas-vindas/manual-maquinas/",
    f"{GETNET_BR}/get-ajuda/",
    f"{GETNET_BR}/get-tap/",
    f"{GETNET_BR}/taxas-personalizadas/",
    f"{GETNET_BR}/emprestimo/",
    f"{GETNET_BR}/ecommerce/",
    f"{GETNET_BR}/",
    f"{GETNET_GLOBAL}/",
    f"{GETNET_GLOBAL}/our-solutions/in-person-payments",
    f"{GETNET_GLOBAL}/our-solutions/online-payments",
    f"{GETNET_GLOBAL}/corporate/about-us",
]
