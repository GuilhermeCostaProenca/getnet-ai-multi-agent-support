def test_chat_endpoint_contract(client):
    resp = client.post("/chat", json={"message": "Do I need a bank account for Pix?", "user_id": "cliente1988"})
    assert resp.status_code == 200
    body = resp.json()
    expected = {"request_id", "user_id", "answer", "agent", "intent", "confidence", "language", "sources",
                "tools_used", "handoff", "guardrails", "trace", "latency_ms"}
    assert expected <= body.keys() and body["user_id"] == "cliente1988"


def test_ac19_invalid_payloads_return_422(client):
    assert client.post("/chat", json={"message": "", "user_id": "cliente1988"}).status_code == 422
    assert client.post("/chat", json={"message": "hi"}).status_code == 422
    assert client.post("/chat", json={"message": "hi", "user_id": "bad id!"}).status_code == 422
    assert client.post("/chat", json={"message": "x" * 2001, "user_id": "u"}).status_code == 422


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "knowledge_base_chunks": 1}


def test_health_degraded_when_knowledge_base_is_empty(client, store):
    store.hits = []
    assert client.get("/health").json() == {"status": "degraded", "knowledge_base_chunks": 0}
