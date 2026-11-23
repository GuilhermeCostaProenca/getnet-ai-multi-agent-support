"""End-to-end demo against a running API (real LLM): auth, memory, escalation, operator loop and resume.

Usage: python scripts/demo_flow.py [--url http://127.0.0.1:8000]   (needs ENABLE_DEV_TOKENS=true on the server)
"""

import argparse

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    base = parser.parse_args().url
    c = httpx.Client(base_url=base, timeout=120)

    def token(user: str, role: str) -> dict:
        t = c.post("/v1/auth/dev-token", json={"user_id": user, "role": role}).json()["access_token"]
        return {"Authorization": f"Bearer {t}"}

    merchant, other, operator = token("cliente1988", "merchant"), token("cliente2000", "merchant"), token("op.ana", "operator")

    def chat(message: str, cid: str | None = None) -> dict:
        r = c.post("/v1/chat", json={"message": message, "conversation_id": cid}, headers=merchant)
        r.raise_for_status()
        return r.json()

    def show(n: str, r: dict) -> None:
        print(f"{n} status={r['status']} agents={r['agents']} intents={r['intents']} tools={[t['name'] for t in r['tools_used']]} "
              f"sources={len(r['sources'])} handoff={r['handoff']['required']} llm_calls={r['usage']['llm_calls']} "
              f"tokens={r['usage']['input_tokens']}+{r['usage']['output_tokens']} {r['latency_ms']}ms")

    print("security:", "no token ->", c.post("/v1/chat", json={"message": "hi"}).status_code,
          "| user_id mismatch ->", c.post("/v1/chat", json={"message": "hi", "user_id": "cliente2000"}, headers=merchant).status_code,
          "| merchant on operator API ->", c.get("/v1/operator/tickets", headers=merchant).status_code)

    r1 = chat("How many installments can I split a sale into with the crediário?")
    cid = r1["conversation_id"]
    show("1 product question     ", r1)
    r2 = chat("And can I also sell it with the payment link on WhatsApp?", cid)
    show("2 follow-up (memory)   ", r2)
    planner = next(s["detail"] for s in r2["trace"] if s["step"] == "planner")
    print("  planner:", planner)
    r3 = chat("What's the crediário limit? Also write me a poem about it.", cid)
    show("3 compound request     ", r3)
    show("4a absurd question     ", chat("Does Getnet offer a cashback program for merchants on Saturn?", cid))
    r4 = chat("I'd like to talk to a human attendant about my fees, please.", cid)
    show("4b asks for a human    ", r4)
    ticket = r4["handoff"]["ticket_id"]
    print("  ticket:", ticket, "| reason:", r4["handoff"]["reason"])
    show("5 paused (no agent)    ", chat("any update?", cid))

    print("other merchant reading this conversation ->",
          c.get(f"/v1/conversations/{cid}", headers=other).status_code)
    queue = c.get("/v1/operator/tickets", params={"status": "open"}, headers=operator).json()
    mine = next(t for t in queue if t["ticket_id"] == ticket)
    print(f"6 operator queue: {len(queue)} open; context reason={mine['context']['reason']} "
          f"recent_messages={len(mine['context']['recent_messages'])}")
    print("  claim ->", c.post(f"/v1/operator/tickets/{ticket}/claim", headers=operator).json()["status"])
    c.post(f"/v1/operator/tickets/{ticket}/reply", headers=operator,
           json={"message": "There is no Saturn program; Getnet offers custom rates (taxas personalizadas)."})
    r7 = chat("ok?", cid)
    print("7 merchant sees operator reply:", "taxas personalizadas" in r7["answer"])
    print("  resolve ->", c.post(f"/v1/operator/tickets/{ticket}/resolve", headers=operator,
                                 json={"resolution": "explained there is no Saturn program"}).json()["status"])
    show("8 agents resumed       ", chat("Thanks! How does Pix work at Getnet?", cid))
    history = c.get(f"/v1/conversations/{cid}", headers=merchant).json()
    print("9 persisted history:", [m["role"] for m in history["messages"]])


if __name__ == "__main__":
    main()
