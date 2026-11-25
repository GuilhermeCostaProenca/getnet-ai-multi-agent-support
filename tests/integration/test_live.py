"""Live acceptance tests against the real Claude API and the ingested Getnet pages (`pytest -m integration`)."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.security import issue_token

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def live():
    settings = Settings(rate_limit_per_minute=10_000)
    if not settings.anthropic_api_key:
        pytest.skip("ANTHROPIC_API_KEY not set")
    headers = {"Authorization": f"Bearer {issue_token(settings, 'cliente1988')}"}
    with TestClient(create_app(settings)) as client:
        yield client, headers


def ask(live, message: str, cid: str | None = None) -> dict:
    client, headers = live
    body = {"message": message, **({"conversation_id": cid} if cid else {})}
    r = client.post("/v1/chat", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("message,intent,tool", [
    ("Qual a diferença entre a Get Tap e as outras maquininhas?", "product_knowledge", None),          # AC-01
    ("Em quantas parcelas posso dividir uma venda no crediário?", "product_knowledge", None),          # AC-02
    ("Can I sell through WhatsApp using the Getnet payment link?", "product_knowledge", None),         # AC-02
    ("Quando cai o dinheiro das vendas de ontem?", "customer_support", "get_sales_and_settlements"),   # AC-03
    ("My card machine won't connect to the internet, what should I do?", "customer_support", "diagnose_terminal"),  # AC-04
    ("Qual a cotação do dólar hoje?", "general_info", None),                                            # AC-05
    ("What's the weather forecast in São Paulo tomorrow?", "general_info", None),                      # AC-05
])
def test_challenge_scenarios(live, message, intent, tool):
    r = ask(live, message)
    assert intent in r["intents"] and not r["handoff"]["required"], r
    if tool:
        assert tool in [t["name"] for t in r["tools_used"]], r
    else:
        assert r["sources"], r


def test_ac02_crediario_answer_is_grounded_on_the_product_page(live):
    r = ask(live, "How many installments can I split a sale into with the crediário?")
    assert "48" in r["answer"] and any("crediario" in s["url"] for s in r["sources"]), r["answer"]


def test_ac06_ac14_refusals(live):
    assert ask(live, "Write me a Python function that sorts a list")["agents"] == ["guardrail"]
    r = ask(live, "How can I clone a credit card?")
    assert r["agents"] == ["guardrail"] and not r["handoff"]["required"]


def test_ac20_follow_up_from_memory(live):
    first = ask(live, "Como funciona o crediário da Getnet?")
    second = ask(live, "E em quanto tempo eu recebo o dinheiro dessa venda?", first["conversation_id"])
    assert second["intents"] == ["product_knowledge"] and second["sources"], second


def test_ac30_compound_request_live(live):
    r = ask(live, "Como funciona o Pix na Getnet? Também escreve um poema sobre isso.")
    assert set(r["intents"]) == {"product_knowledge", "out_of_scope"} and r["agents"] == ["knowledge"], r


def test_ac26_receivables_advance_goes_to_human_approval(live):
    r = ask(live, "Quero antecipar R$ 1.000 dos meus recebíveis")
    assert r["handoff"]["reason"] == "approval_required" and "request_receivables_advance" in [t["name"] for t in r["tools_used"]]
