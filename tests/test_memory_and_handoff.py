from tests.conftest import GETNET_HIT


def _escalate(chat, llm, kb):
    """Product question with no grounding anywhere -> handoff (ORC-04 path)."""
    kb.hits = []
    return chat("What is the fee of product X?")


def test_ac35_conversation_memory_feeds_the_planner(chat, client, auth, llm):
    first = chat("How many installments with crediário?").json()
    cid = first["conversation_id"]
    second = chat("and with the payment link?", conversation_id=cid).json()
    assert second["conversation_id"] == cid
    planner_input = llm.called("plan")[1]
    assert "How many installments with crediário?" in planner_input and "and with the payment link?" in planner_input
    history = client.get(f"/v1/conversations/{cid}", headers=auth()).json()
    assert [m["role"] for m in history["messages"]] == ["user", "assistant", "user", "assistant"]


def test_ac36_escalation_pauses_the_conversation(chat, llm, kb, client, auth):
    first = _escalate(chat, llm, kb).json()
    assert first["handoff"]["required"] and first["status"] == "awaiting_human"
    plans_before = len(llm.called("plan"))
    second = chat("any news?", conversation_id=first["conversation_id"]).json()
    assert second["agents"] == ["handoff"] and first["handoff"]["ticket_id"] in second["answer"]
    assert len(llm.called("plan")) == plans_before  # no agent ran while paused
    tickets = client.get("/v1/operator/tickets?status=open", headers=auth("op1", "operator")).json()
    assert tickets[0]["context"]["reason"] == "no_grounded_answer"
    assert any("fee of product X" in m["content"] for m in tickets[0]["context"]["recent_messages"])


def test_ac37_operator_claims_replies_and_resolves_then_agents_resume(chat, llm, kb, client, auth):
    first = _escalate(chat, llm, kb).json()
    cid, ticket = first["conversation_id"], first["handoff"]["ticket_id"]
    op = auth("op1", "operator")
    assert client.post(f"/v1/operator/tickets/{ticket}/reply", json={"message": "hi"}, headers=op).status_code == 409
    assert client.post(f"/v1/operator/tickets/{ticket}/claim", headers=op).json()["status"] == "claimed"
    assert client.post(f"/v1/operator/tickets/{ticket}/claim", headers=auth("op2", "operator")).status_code == 409
    client.post(f"/v1/operator/tickets/{ticket}/reply", json={"message": "Product X costs 1.99%."}, headers=op)
    conversation = client.get(f"/v1/conversations/{cid}", headers=auth()).json()
    assert conversation["status"] == "awaiting_human"
    assert any(m["role"] == "operator" and "1.99%" in m["content"] for m in conversation["messages"])
    paused = chat("so what is the fee?", conversation_id=cid).json()  # still paused: the reply is surfaced, no agent runs
    assert paused["agents"] == ["handoff"] and "1.99%" in paused["answer"]
    resolved = client.post(f"/v1/operator/tickets/{ticket}/resolve", json={"resolution": "fee explained"}, headers=op)
    assert resolved.json()["status"] == "resolved"
    kb.hits = [GETNET_HIT]
    third = chat("thanks! and how does Pix work?", conversation_id=cid).json()
    assert third["status"] == "active" and third["agents"] == ["knowledge"]  # agents resumed
    assert "Human support closed ticket" in llm.called("plan")[-1] and "1.99%" in llm.called("plan")[-1]


def test_money_moving_action_requires_human_approval(chat, llm, client, auth):
    from tests.conftest import plan, task
    llm.default_plan = plan(task("customer_support", "advance R$ 300 of my receivables"))
    llm.tool_plan = [("request_receivables_advance", {"amount": 300})]
    r = chat("Antecipe R$ 300 dos meus recebíveis").json()
    assert r["handoff"]["required"] and r["handoff"]["reason"] == "approval_required" and r["status"] == "awaiting_human"
    ticket = client.get("/v1/operator/tickets?status=open", headers=auth("op1", "operator")).json()[0]
    action = ticket["context"]["pending_actions"][0]
    assert action["action"] == "receivables_advance" and action["requested"] == 300 and action["status"] == "pending_human_approval"


def test_unknown_ticket_returns_404(client, auth):
    assert client.post("/v1/operator/tickets/HO-NOPE/claim", headers=auth("op1", "operator")).status_code == 404


def test_history_survives_a_new_app_instance(tmp_path, llm, kb):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.security import issue_token
    from app.storage import Store
    from tests.conftest import make_settings

    settings = make_settings(database_path=str(tmp_path / "app.db"))
    headers = {"Authorization": f"Bearer {issue_token(settings, 'cliente1988', 'merchant')}"}
    with TestClient(create_app(settings=settings, llm=llm, kb=kb, store=Store(settings.database_path))) as c:
        cid = c.post("/v1/chat", json={"message": "Pix?"}, headers=headers).json()["conversation_id"]
    with TestClient(create_app(settings=settings, llm=llm, kb=kb, store=Store(settings.database_path))) as c:
        assert len(c.get(f"/v1/conversations/{cid}", headers=headers).json()["messages"]) == 2
