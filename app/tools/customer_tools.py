"""Customer Support tools over SIMULATED customer data (REQ-SUP-01, SEC-02).

No Getnet backend is available, so records are MOCK; the tools still compute real things (settlement dates in the
merchant's timezone, terminal diagnosis, decline-code interpretation) and every output says it is MOCK.
The LLM-facing schemas expose no user id: the server binds the authenticated identity when executing a tool.
"""

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

MOCK_LABEL = "MOCK - simulated data for demonstration, not real customer records"
BUSINESS_TZ = ZoneInfo("America/Sao_Paulo")

_CUSTOMERS: dict[str, dict[str, Any]] = {
    "cliente1988": {
        "merchant_name": "Padaria Bom Dia (mock)", "plan": "Get Smart",
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
        "declines": [{"days_ago": 0, "terminal_id": "T-1002", "code": "51", "amount": 230.00},
                     {"days_ago": 0, "terminal_id": "T-1002", "code": "05", "amount": 98.00}],
    },
    "cliente2000": {
        "merchant_name": "Loja Exemplo (mock)", "plan": "Get Clássica",
        "bank_account": "Itaú ag. 0002 c/c ****9876",
        "settlement": {"debit_business_days": 1, "credit_days": 30, "auto_advance": True},
        "terminals": [{"terminal_id": "T-2001", "model": "Get Clássica", "connectivity": "chip 4G", "status": "online",
                       "last_seen_hours_ago": 0, "signal": "good", "firmware": "2.8.0", "firmware_latest": "2.8.0"}],
        "sales": [{"days_ago": 1, "method": "credit", "installments": 1, "amount": 999.00, "terminal_id": "T-2001"}],
        "declines": [],
    },
}

DECLINE_CODES = {
    "05": ("Not authorized by the card issuer (do not honor)", "Ask the customer to contact their bank or pay another way (e.g. Pix)."),
    "14": ("Invalid card number", "Check the card and retry; if it persists, use another card."),
    "51": ("Insufficient funds or credit limit", "Ask for another card, a lower amount, or Pix."),
    "54": ("Expired card", "Ask for a valid card."),
    "55": ("Wrong PIN", "Let the customer retry the PIN carefully; repeated errors may block the card."),
    "57": ("Transaction not permitted for this card", "Try another function (debit/credit) or another card."),
    "91": ("Issuer or network temporarily unavailable", "Wait a few minutes and retry."),
}


def business_today(now: datetime | None = None) -> date:
    return (now or datetime.now(BUSINESS_TZ)).astimezone(BUSINESS_TZ).date()


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


def expected_deposit_date(sale_date: date, method: str, installment: int, rules: dict[str, Any]) -> date:
    """Mock rule: Pix D+0; debit D+1 business day; credit D+30 per installment (D+1 with automatic advance)."""
    if method == "pix":
        return sale_date
    if method == "debit" or rules["auto_advance"]:
        return _add_business_days(sale_date, rules["debit_business_days"])
    return _next_business_day(sale_date + timedelta(days=rules["credit_days"] * installment))


def _not_found(user_id: str) -> dict[str, Any]:
    return {"error": "customer_not_found", "user_id": user_id, "data_source": MOCK_LABEL}


def get_merchant_profile(user_id: str) -> dict[str, Any]:
    c = _CUSTOMERS.get(user_id)
    if not c:
        return _not_found(user_id)
    return {"user_id": user_id, "merchant_name": c["merchant_name"], "plan": c["plan"], "bank_account": c["bank_account"],
            "settlement_rules": c["settlement"], "terminals": [t["terminal_id"] for t in c["terminals"]],
            "data_source": MOCK_LABEL}


def get_sales_and_settlements(user_id: str, days_back: int = 7, today: date | None = None) -> dict[str, Any]:
    c = _CUSTOMERS.get(user_id)
    if not c:
        return _not_found(user_id)
    today = today or business_today()
    days_back = max(1, min(int(days_back), 30))
    items = []
    for s in c["sales"]:
        if s["days_ago"] > days_back:
            continue
        sale_date = today - timedelta(days=s["days_ago"])
        for n in range(1, s["installments"] + 1):
            deposit = expected_deposit_date(sale_date, s["method"], n, c["settlement"])
            items.append({"sale_date": sale_date.isoformat(), "method": s["method"], "terminal_id": s["terminal_id"],
                          "installment": f"{n}/{s['installments']}", "amount": round(s["amount"] / s["installments"], 2),
                          "expected_deposit_date": deposit.isoformat(),
                          "status": "deposited" if deposit <= today else "scheduled"})
    return {"user_id": user_id, "today": today.isoformat(), "timezone": "America/Sao_Paulo",
            "bank_account": c["bank_account"], "auto_advance_enabled": c["settlement"]["auto_advance"], "items": items,
            "total_scheduled": round(sum(i["amount"] for i in items if i["status"] == "scheduled"), 2),
            "data_source": MOCK_LABEL}


def diagnose_terminal(user_id: str, terminal_id: str | None = None, error_code: str | None = None) -> dict[str, Any]:
    c = _CUSTOMERS.get(user_id)
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
            steps.append("Restart the machine and run a test transaction")
        if t["signal"] == "weak":
            issues.append("Weak signal in the last connection")
        if t["firmware"] != t["firmware_latest"]:
            issues.append(f"Outdated firmware {t['firmware']} (latest {t['firmware_latest']})")
            steps.append("Once online, install the pending update from the settings menu")
        diagnostics.append({**t, "issues": issues or ["No issue detected"], "recommended_steps": steps})
    codes = [error_code] if error_code else sorted({d["code"] for d in c["declines"]})
    declines = [{"code": code, "meaning": DECLINE_CODES.get(code, ("Unknown code", ""))[0],
                 "action": DECLINE_CODES.get(code, ("", "Contact Getnet support with the code shown."))[1]} for code in codes]
    return {"user_id": user_id, "terminals": diagnostics, "decline_codes": declines,
            "recent_declines_today": [d for d in c["declines"] if terminal_id in (None, "", d["terminal_id"])],
            "data_source": MOCK_LABEL}


TOOL_FUNCTIONS = {"get_merchant_profile": get_merchant_profile,
                  "get_sales_and_settlements": get_sales_and_settlements,
                  "diagnose_terminal": diagnose_terminal}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {"type": "function", "name": "get_merchant_profile", "strict": True,
     "description": "Get the current merchant's plan, masked bank account, settlement rules and terminal ids.",
     "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
    {"type": "function", "name": "get_sales_and_settlements", "strict": True,
     "description": "List the current merchant's sales in the last N days with each installment's expected deposit date and status.",
     "parameters": {"type": "object", "properties": {"days_back": {"type": "integer", "description": "Days back to include (1-30)"}},
                    "required": ["days_back"], "additionalProperties": False}},
    {"type": "function", "name": "diagnose_terminal", "strict": True,
     "description": "Diagnose the current merchant's card machines (connectivity, signal, firmware) and explain decline codes.",
     "parameters": {"type": "object",
                    "properties": {"terminal_id": {"type": ["string", "null"], "description": "Terminal id or null for all"},
                                   "error_code": {"type": ["string", "null"], "description": "Decline/error code shown, or null"}},
                    "required": ["terminal_id", "error_code"], "additionalProperties": False}},
]


def execute_tool(user_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Dispatch with the authenticated identity bound by the server; any LLM-supplied user_id is dropped."""
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return {"error": "unknown_tool", "name": name}
    try:
        return fn(user_id, **{k: v for k, v in arguments.items() if k != "user_id"})
    except TypeError as exc:
        return {"error": "invalid_arguments", "detail": str(exc)}
