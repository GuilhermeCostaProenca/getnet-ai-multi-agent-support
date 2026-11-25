"""SEC-01..03, MEM-01, API-02, OBS-02, UI-01."""

from app.security import issue_token
from tests.conftest import make_settings


def test_ac10_requests_without_or_with_bad_token_are_401(client):
    assert client.post("/v1/chat", json={"message": "oi"}).status_code == 401
    assert client.post("/v1/chat", json={"message": "oi"}, headers={"Authorization": "Bearer nope"}).status_code == 401
    expired = issue_token(make_settings(), "cliente1988", ttl_minutes=-1)
    r = client.post("/v1/chat", json={"message": "oi"}, headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401 and "expired" in r.json()["detail"]


def test_ac11_body_user_id_must_match_token(chat, client, auth):
    r = client.post("/v1/chat", json={"message": "oi", "user_id": "cliente2000"}, headers=auth("cliente1988"))
    assert r.status_code == 403


def test_ac11_tools_ignore_llm_supplied_identity():
    from app.tools.customer import execute_tool

    out = execute_tool("cliente2000", "get_merchant_profile", {"user_id": "cliente1988"})
    assert out["name"].startswith("Ótica")


def test_ac12_roles_and_rate_limit(llm, store, auth):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(make_settings(rate_limit_per_minute=2), llm=llm, store=store)) as c:
        assert c.get("/v1/operator/tickets", headers=auth()).status_code == 403
        assert c.get("/v1/operator/tickets", headers=auth("op", "operator")).status_code == 200
        codes = [c.post("/v1/chat", json={"message": "oi"}, headers=auth()).status_code for _ in range(3)]
        assert codes == [200, 200, 429]


def test_ac21_foreign_conversation_is_404(chat, client, auth):
    cid = chat("Como funciona o crediário?").json()["conversation_id"]
    assert chat("e o pix?", cid, sub="cliente2000").status_code == 404
    assert client.get(f"/v1/conversations/{cid}", headers=auth("cliente2000")).status_code == 404
    assert client.get(f"/v1/conversations/{cid}", headers=auth()).json()["messages"][0]["role"] == "user"


def test_ac42_idempotency_key_replays_and_rejects_other_body(client, auth, llm):
    h = {**auth(), "Idempotency-Key": "k-1"}
    first = client.post("/v1/chat", json={"message": "crediário?"}, headers=h).json()
    calls = len(llm.calls)
    again = client.post("/v1/chat", json={"message": "crediário?"}, headers=h).json()
    assert again == first and len(llm.calls) == calls
    assert client.post("/v1/chat", json={"message": "outra"}, headers=h).status_code == 422


def test_ac41_metrics_count_requests_handoffs_and_latency(chat, client):
    chat("Como funciona o crediário?")
    text = client.get("/metrics").text
    assert 'chat_requests_total{agents="knowledge"} 1' in text and 'chat_latency_ms{quantile="0.5"}' in text


def test_ac50_console_is_served_and_dev_tokens_work(client):
    page = client.get("/")
    assert page.status_code == 200 and "Raio-x da resposta" in page.text and "Operador humano" in page.text
    assert client.post("/v1/auth/dev-token", json={"user_id": "cliente1988"}).status_code == 200


def test_dev_tokens_disabled_by_default(llm, store):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(make_settings(enable_dev_tokens=False), llm=llm, store=store)) as c:
        assert c.post("/v1/auth/dev-token", json={"user_id": "x"}).status_code == 404
        assert c.get("/health").json() == {"status": "ok", "knowledge_chunks": 3}
