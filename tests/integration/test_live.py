"""Integration tests against the real LLM and the ingested knowledge base (pytest -m integration).

Assertions target routing, tools, grounding and state — not exact wording (LLM output is non-deterministic).
"""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.security import issue_token

pytestmark = pytest.mark.integration

CASES = [
    ("AC-01", "What's the difference between the Get Clássica and the Get Smart?", "product_knowledge", None),
    ("AC-02", "What's the weather forecast in Porto Alegre tomorrow?", "general_info", None),
    ("AC-03", "When will the money from yesterday's sales be deposited?", "customer_support", "get_sales_and_settlements"),
    ("AC-04", "Do I need a bank account to receive my sales via Pix?", "product_knowledge", None),
    ("AC-05", "My card machine won't connect to the internet, what should I do?", "customer_support", "diagnose_terminal"),
    ("AC-06", "How does receivables advance (antecipação) work with Getnet?", "product_knowledge", None),
    ("AC-07", "What's the euro exchange rate today?", "general_info", None),
    ("AC-08", "My card machine is showing a transaction decline error.", "customer_support", "diagnose_terminal"),
    ("AC-09", "How many installments can I split a sale into with the crediário?", "product_knowledge", None),
    ("AC-10", "Can I sell through WhatsApp using the Payment Link?", "product_knowledge", None),
]


@pytest.fixture(scope="module")
def live():
    settings = Settings(rate_limit_per_minute=1000)
    if not settings.openai_api_key or not settings.jwt_secret:
        pytest.skip("OPENAI_API_KEY and JWT_SECRET required")
    headers = {"Authorization": f"Bearer {issue_token(settings, 'cliente1988', 'merchant')}"}
    with TestClient(create_app(settings=settings)) as client:
        yield client, headers


@pytest.mark.parametrize("ac,message,intent,tool", CASES, ids=[c[0] for c in CASES])
def test_challenge_scenarios(live, ac, message, intent, tool):
    client, headers = live
    body = client.post("/v1/chat", json={"message": message, "user_id": "cliente1988"}, headers=headers).json()
    assert intent in body["intents"], body
    assert body["sources"] or tool, body
    if tool:
        assert tool in [t["name"] for t in body["tools_used"]], body


def test_ac35_follow_up_is_resolved_from_memory(live):
    client, headers = live
    first = client.post("/v1/chat", json={"message": "How many installments can I split a sale into with the crediário?"},
                        headers=headers).json()
    # "it" only makes sense with memory; the fact asked for is documented on the Crediário page ("até 2 dias úteis").
    # (The earlier follow-up, "can I sell it through a payment link", is not documented: abstaining was correct.)
    second = client.post("/v1/chat", json={"message": "And how fast do I receive the money from it?",
                                            "conversation_id": first["conversation_id"]}, headers=headers).json()
    assert second["intents"] == ["product_knowledge"] and second["sources"], second
    assert "2" in second["answer"] and any("crediario" in s["url"] for s in second["sources"]), second["answer"]


def test_ac38_compound_request_live(live):
    client, headers = live
    body = client.post("/v1/chat", json={"message": "How many installments does the crediário allow? Also write me a poem."},
                       headers=headers).json()
    assert set(body["intents"]) == {"product_knowledge", "out_of_scope"} and body["agents"] != ["guardrail"], body
