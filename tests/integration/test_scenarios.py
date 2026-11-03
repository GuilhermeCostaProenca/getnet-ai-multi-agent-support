"""Integration tests: real OpenAI + real ingested knowledge base (pytest -m integration).

They need OPENAI_API_KEY and a populated Chroma collection (`python -m app.rag.ingest`).
LLM output is non-deterministic, so assertions target routing, tools and grounding signals,
not exact wording.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

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
    ("AC-12", "Write me a Python function that sorts a list", "out_of_scope", None),
]


@pytest.fixture(scope="module")
def live_client():
    if not get_settings().openai_api_key:
        pytest.skip("OPENAI_API_KEY not configured")
    from app.main import create_app

    with TestClient(create_app()) as client:
        yield client


@pytest.mark.parametrize("ac,message,intent,tool", CASES, ids=[c[0] for c in CASES])
def test_challenge_scenarios(live_client, ac, message, intent, tool):
    body = live_client.post("/chat", json={"message": message, "user_id": "cliente1988"}).json()
    assert body["intent"] == intent, body
    if intent == "product_knowledge":
        assert any("getnet" in s["url"] for s in body["sources"]), body
    if tool:
        assert tool in [t["name"] for t in body["tools_used"]], body
