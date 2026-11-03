"""Customer Support tools over SIMULATED customer data (REQ-SUP-01/02, GR-AUTHZ-01).

There is no Getnet backend integration, so customer records below are MOCK data. The tools
still perform real operations on that data (settlement-date calculation, terminal diagnosis,
decline-code interpretation), and every output carries `data_source` = MOCK so the LLM and the
user never mistake it for real records.

Authorization: tools receive `user_id` from the orchestrator (request payload), never from the
LLM. The LLM-facing schemas below do not even expose a user_id parameter.
"""

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

# "Yesterday" must be the merchant's yesterday, not the server's (containers run in UTC).
BUSINESS_TZ = ZoneInfo("America/Sao_Paulo")

MOCK_LABEL = "MOCK - simulated data for demonstration, not real customer records"

_CUSTOMERS: dict[str, dict[str, Any]] = {
    "cliente1988": {
        "merchant_name": "Padaria Bom Dia (mock)",
        "plan": "Get Smart",
        "bank_account": "Santander ag. 0001 c/c ****4321",
        "settlement": {"debit_business_days": 1, "credit_days": 30, "auto_advance": False},
        "terminals": [
            {"terminal_id": "T-1001", "model": "Get Smart", "connectivity": "wifi", "status": "offline",
             "last_seen_hours_ago": 20, "signal": "weak", "firmware": "3.2.1", "firmware_latest": "3.4.0"},
            {"terminal_id": "T-1002", "model": "Get Clássica", "connectivity": "chip 4G", "status": "online",
             "last_seen_hours_ago": 0, "signal": "good", "firmware": "2.8.0", "firmware_latest": "2.8.0"},
        ],
        "sales": [
            {"days_ago": 1, "method": "credit", "installments": 1, "amount": 350.00, "terminal_id": "T-1002"},
            {"days_ago": 1, "method": "debit", "installments": 1, "amount": 120.50, "terminal_id": "T-1002"},
            {"days_ago": 1, "method": "pix", "installments": 1, "amount": 89.90, "terminal_id": "T-1002"},
            {"days_ago": 1, "method": "credit", "installments": 3, "amount": 600.00, "terminal_id": "T-1001"},
            {"days_ago": 2, "method": "debit", "installments": 1, "amount": 75.00, "terminal_id": "T-1001"},
            {"days_ago": 3, "method": "credit", "installments": 1, "amount": 210.00, "terminal_id": "T-1002"},
        ],
        "declines": [
            {"days_ago": 0, "terminal_id": "T-1002", "code": "51", "amount": 230.00},
            {"days_ago": 0, "terminal_id": "T-1002", "code": "05", "amount": 98.00},
        ],
    },
    "cliente2000": {
        "merchant_name": "Loja Exemplo (mock)",
        "plan": "Get Clássica",
        "bank_account": "Santander ag. 0002 c/c ****9876",
        "settlement": {"debit_business_days": 1, "credit_days": 30, "auto_advance": True},
        "terminals": [
            {"terminal_id": "T-2001", "model": "Get Clássica", "connectivity": "chip 4G", "status": "online",
             "last_seen_hours_ago": 0, "signal": "good", "firmware": "2.8.0", "firmware_latest": "2.8.0"},
        ],
        "sales": [{"days_ago": 1, "method": "credit", "installments": 1, "amount": 999.00, "terminal_id": "T-2001"}],
        "declines": [],
    },
}

# Reference mapping of common ISO 8583 / Brazilian acquirer response codes.
DECLINE_CODES: dict[str, dict[str, str]] = {
    "05": {"meaning": "Not authorized by the card issuer (do not honor)",
           "action": "Ask the customer to contact their bank or use another card/payment method (e.g. Pix)."},
    "14": {"meaning": "Invalid card number", "action": "Check the card and retry; if it persists, use another card."},
    "51": {"meaning": "Insufficient funds or credit limit",
           "action": "Ask for another card, a lower amount, or Pix."},
    "54": {"meaning": "Expired card", "action": "Ask for a valid card."},
    "55": {"meaning": "Wrong PIN", "action": "Let the customer retry the PIN carefully; after several errors the card may be blocked."},
    "57": {"meaning": "Transaction not permitted for this card",
           "action": "Try another function (debit/credit) or another card."},
    "91": {"meaning": "Issuer or network temporarily unavailable", "action": "Wait a few minutes and retry."},
}


def business_today(now: datetime | None = None) -> date:
    return (now or datetime.now(BUSINESS_TZ)).astimezone(BUSINESS_TZ).date()


def _customer(user_id: str) -> dict[str, Any] | None:
    return _CUSTOMERS.get(user_id)


def _not_found(user_id: str) -> dict[str, Any]:
    return {"error": "customer_not_found", "user_id": user_id, "data_source": MOCK_LABEL}


def _add_business_days(start: date, days: int) -> date:
    current = start
    while days > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            days -= 1
    return current


def _next_business_day(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def expected_deposit_date(sale_date: date, method: str, installment: int, settlement: dict[str, Any]) -> date:
    """Settlement rule used by the mock: Pix D+0, debit D+1 business day, credit D+30 per installment
    (or D+1 business day when automatic receivables advance is enabled)."""
    if method == "pix":
        return sale_date
    if method == "debit" or settlement.get("auto_advance"):
        return _add_business_days(sale_date, settlement["debit_business_days"])
    return _next_business_day(sale_date + timedelta(days=settlement["credit_days"] * installment))


def get_merchant_profile(user_id: str) -> dict[str, Any]:
    c = _customer(user_id)
    if not c:
        return _not_found(user_id)
    return {"user_id": user_id, "merchant_name": c["merchant_name"], "plan": c["plan"],
            "bank_account": c["bank_account"], "settlement_rules": c["settlement"],
            "terminals": [t["terminal_id"] for t in c["terminals"]], "data_source": MOCK_LABEL}


def get_sales_and_settlements(user_id: str, days_back: int = 7, today: date | None = None) -> dict[str, Any]:
    c = _customer(user_id)
    if not c:
        return _not_found(user_id)
    today = today or business_today()
    days_back = max(1, min(int(days_back), 30))
    items = []
    for s in c["sales"]:
        if s["days_ago"] > days_back:
            continue
        sale_date = today - timedelta(days=s["days_ago"])
        parts = s["installments"]
        for n in range(1, parts + 1):
            deposit = expected_deposit_date(sale_date, s["method"], n, c["settlement"])
            items.append({
                "sale_date": sale_date.isoformat(), "method": s["method"], "terminal_id": s["terminal_id"],
                "installment": f"{n}/{parts}", "amount": round(s["amount"] / parts, 2),
                "expected_deposit_date": deposit.isoformat(),
                "status": "deposited" if deposit <= today else "scheduled",
            })
    return {"user_id": user_id, "today": today.isoformat(), "bank_account": c["bank_account"],
            "auto_advance_enabled": c["settlement"]["auto_advance"], "items": items,
            "total_scheduled": round(sum(i["amount"] for i in items if i["status"] == "scheduled"), 2),
            "data_source": MOCK_LABEL}


def diagnose_terminal(user_id: str, terminal_id: str | None = None, error_code: str | None = None) -> dict[str, Any]:
    c = _customer(user_id)
    if not c:
        return _not_found(user_id)
    terminals = [t for t in c["terminals"] if terminal_id in (None, "", t["terminal_id"])]
    if not terminals:
        return {"error": "terminal_not_found", "terminal_id": terminal_id, "data_source": MOCK_LABEL}
    diagnostics = []
    for t in terminals:
        issues, steps = [], []
        if t["status"] == "offline":
            issues.append(f"Terminal offline for {t['last_seen_hours_ago']}h")
            if t["connectivity"] == "wifi":
                steps += ["Check that the Wi-Fi router is on and has internet", "Move the machine closer to the router",
                          "Re-select the Wi-Fi network and re-enter the password in the machine's settings"]
            else:
                steps += ["Check mobile signal at the location", "Restart the machine to re-register the chip"]
            steps.append("Restart the machine (hold the power button) and run a test transaction")
        if t["signal"] == "weak":
            issues.append("Weak signal reported in the last connection")
        if t["firmware"] != t["firmware_latest"]:
            issues.append(f"Outdated firmware {t['firmware']} (latest {t['firmware_latest']})")
            steps.append("Once online, install the pending update from the machine's settings menu")
        diagnostics.append({**t, "issues": issues or ["No issue detected"], "recommended_steps": steps})
    codes = [error_code] if error_code else sorted({d["code"] for d in c["declines"]})
    declines = [{"code": code, **DECLINE_CODES.get(code, {"meaning": "Unknown code",
                 "action": "Contact Getnet support with the code shown on the screen."})} for code in codes]
    recent = [d for d in c["declines"] if terminal_id in (None, "", d["terminal_id"])]
    return {"user_id": user_id, "terminals": diagnostics, "decline_codes": declines,
            "recent_declines_today": recent, "data_source": MOCK_LABEL}


TOOL_FUNCTIONS = {
    "get_merchant_profile": get_merchant_profile,
    "get_sales_and_settlements": get_sales_and_settlements,
    "diagnose_terminal": diagnose_terminal,
}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function", "name": "get_merchant_profile", "strict": True,
        "description": "Get the current merchant's plan, bank account (masked), settlement rules and terminal ids.",
        "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "type": "function", "name": "get_sales_and_settlements", "strict": True,
        "description": "List the current merchant's sales in the last N days with the expected deposit "
                       "(settlement) date and status of each installment.",
        "parameters": {
            "type": "object",
            "properties": {"days_back": {"type": "integer", "description": "How many days back to include (1-30)"}},
            "required": ["days_back"], "additionalProperties": False,
        },
    },
    {
        "type": "function", "name": "diagnose_terminal", "strict": True,
        "description": "Diagnose the current merchant's card machines (connectivity, signal, firmware) and "
                       "explain transaction decline codes, with recommended steps.",
        "parameters": {
            "type": "object",
            "properties": {
                "terminal_id": {"type": ["string", "null"], "description": "Terminal id, or null for all"},
                "error_code": {"type": ["string", "null"], "description": "Decline/error code shown, or null"},
            },
            "required": ["terminal_id", "error_code"], "additionalProperties": False,
        },
    },
]


def execute_tool(user_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a tool call with the request's user_id bound by the server (GR-AUTHZ-01)."""
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return {"error": "unknown_tool", "name": name}
    safe_args = {k: v for k, v in arguments.items() if k != "user_id"}
    try:
        return fn(user_id, **safe_args)
    except TypeError as exc:
        return {"error": "invalid_arguments", "detail": str(exc)}
