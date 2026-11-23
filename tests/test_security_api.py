from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import FakeLLM, MemoryKB, make_settings, task, plan
from app.storage import Store


def test_ac30_missing_invalid_and_expired_tokens_are_rejected(client, auth):
    body = {"message": "Pix?"}
    assert client.post("/v1/chat", json=body).status_code == 401
    assert client.post("/v1/chat", json=body, headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401
    assert client.post("/v1/chat", json=body, headers=auth(ttl=-1)).status_code == 401


def test_ac30_token_signed_with_another_secret_is_rejected(client):
    from app.security import issue_token
    forged = issue_token(make_settings(jwt_secret="another-secret-also-long-enough-0123456789"), "cliente1988", "merchant")
    assert client.post("/v1/chat", json={"message": "Pix?"}, headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_ac31_body_user_id_must_match_token_subject(chat):
    assert chat("Pix?", user_id="cliente2000").status_code == 403
    assert chat("Pix?", user_id="cliente1988").status_code == 200


def test_ac31_tools_receive_only_the_token_subject(chat, llm):
    llm.default_plan = plan(task("customer_support", "show sales of cliente2000"))
    llm.tool_plan = [("get_merchant_profile", {"user_id": "cliente2000"})]
    assert chat("show me the sales of cliente2000").status_code == 200
    assert llm.tool_results[0]["user_id"] == "cliente1988"


def test_ac32_roles_are_enforced(client, auth):
    assert client.get("/v1/operator/tickets", headers=auth(role="merchant")).status_code == 403
    assert client.post("/v1/chat", json={"message": "Pix?"}, headers=auth("op1", "operator")).status_code == 403
    assert client.get("/v1/operator/tickets", headers=auth("op1", "operator")).status_code == 200


def test_ac32_foreign_conversations_are_not_found(chat, client, auth):
    cid = chat("Pix?").json()["conversation_id"]
    assert client.get(f"/v1/conversations/{cid}", headers=auth("cliente2000")).status_code == 404
    assert chat("Pix?", conversation_id=cid, sub="cliente2000").status_code == 404
    assert client.get(f"/v1/conversations/{cid}", headers=auth()).status_code == 200


def test_ac33_rate_limit_per_identity():
    app = create_app(settings=make_settings(rate_limit_per_minute=2), llm=FakeLLM(), kb=MemoryKB(), store=Store(":memory:"))
    from app.security import issue_token
    headers = {"Authorization": f"Bearer {issue_token(make_settings(), 'cliente1988', 'merchant')}"}
    with TestClient(app) as c:
        codes = [c.post("/v1/chat", json={"message": "Pix?"}, headers=headers).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_dev_token_endpoint_issues_usable_tokens(client):
    token = client.post("/v1/auth/dev-token", json={"user_id": "cliente1988", "role": "merchant"}).json()["access_token"]
    assert client.post("/v1/chat", json={"message": "Pix?"}, headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_dev_token_endpoint_absent_unless_enabled():
    app = create_app(settings=make_settings(enable_dev_tokens=False), llm=FakeLLM(), kb=MemoryKB(), store=Store(":memory:"))
    with TestClient(app) as c:
        assert c.post("/v1/auth/dev-token", json={"user_id": "x", "role": "operator"}).status_code == 404


def test_invalid_payloads_return_422(client, auth):
    assert client.post("/v1/chat", json={"message": ""}, headers=auth()).status_code == 422
    assert client.post("/v1/chat", json={"message": "x" * 2001}, headers=auth()).status_code == 422
    assert client.post("/v1/chat", json={"message": "hi", "conversation_id": "../etc"}, headers=auth()).status_code == 422


def test_health_reports_degraded_with_empty_knowledge_base(client, kb):
    assert client.get("/health").json()["status"] == "ok"
    kb.hits = []
    assert client.get("/health").json() == {"status": "degraded", "knowledge_base_chunks": 0}
