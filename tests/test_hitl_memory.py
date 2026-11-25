"""HITL-01..05 and MEM-01..03."""

from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import FakeLLM, make_settings, plan, seeded_store, task


def test_ac23_ac24_escalation_pauses_operator_loop_and_resume(chat, client, auth, llm):
    llm.plans = [plan(human=True)]
    first = chat("Quero falar com um atendente").json()
    tid, cid = first["handoff"]["ticket_id"], first["conversation_id"]
    assert first["handoff"]["reason"] == "user_requested_human" and first["status"] == "awaiting_human"  # AC-25

    calls = len(llm.calls)
    paused = chat("alguma novidade?", cid).json()
    assert paused["agents"] == ["handoff"] and len(llm.calls) == calls  # HITL-02: no LLM while paused

    op = auth("operador.ana", "operator")
    queue = client.get("/v1/operator/tickets?status=open", headers=op).json()
    assert queue[0]["context"]["recent_messages"] and queue[0]["ticket_id"] == tid
    assert client.post(f"/v1/operator/tickets/{tid}/reply", json={"message": "oi"}, headers=op).status_code == 409
    assert client.post(f"/v1/operator/tickets/{tid}/claim", headers=op).json()["status"] == "claimed"
    client.post(f"/v1/operator/tickets/{tid}/reply", json={"message": "Sua taxa é 1,99%."}, headers=op)
    assert "1,99%" in chat("e aí?", cid).json()["answer"]
    assert client.post(f"/v1/operator/tickets/{tid}/resolve", json={}, headers=op).json()["status"] == "resolved"
    resumed = chat("Como funciona o crediário?", cid).json()
    assert resumed["status"] == "active" and resumed["agents"] == ["knowledge"]
    assert "Human support closed ticket" in llm.called("plan")[-1]


def test_ac26_receivables_advance_needs_human_approval(chat, client, auth, llm):
    llm.default_plan = plan(task("customer_support", "antecipar 1000"))
    llm.tool_plan = [("request_receivables_advance", {"amount": 1000})]
    r = chat("Quero antecipar R$ 1000").json()
    assert r["handoff"]["reason"] == "approval_required" and "escalation" in r["agents"]
    ticket = client.get("/v1/operator/tickets", headers=auth("op", "operator")).json()[0]
    action = ticket["context"]["pending_actions"][0]
    assert action["status"] == "pending_human_approval" and action["executed"] is False


def test_ac22_old_turns_are_summarised_and_persisted(chat, client, auth, llm, store):
    cid = None
    for i in range(8):
        cid = chat(f"pergunta {i} sobre crediário", cid).json()["conversation_id"]
    assert llm.called("summary") and store.get_conversation(cid)["summary"].startswith("Resumo")
    assert "[summary of earlier turns]" in llm.called("plan")[-1]


def test_ac21_history_survives_a_new_app_instance(tmp_path, auth):
    path = str(tmp_path / "db.sqlite")
    seeded_store(path)
    llm = FakeLLM()
    with TestClient(create_app(make_settings(database_path=path), llm=llm)) as c:
        cid = c.post("/v1/chat", json={"message": "crediário?"}, headers=auth()).json()["conversation_id"]
    with TestClient(create_app(make_settings(database_path=path), llm=llm)) as c:
        msgs = c.get(f"/v1/conversations/{cid}", headers=auth()).json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
