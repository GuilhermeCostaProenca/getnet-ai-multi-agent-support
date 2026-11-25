"""Customer Support tools over SIMULATED merchant data (REQ-SUP-01, CON-03, ADR-10).

There is no Getnet backend in this challenge, so records are MOCK and every output says so. The tools still compute
real things: settlement dates in the merchant's timezone (business days), terminal diagnosis and decline-code
meaning. The tool schemas expose no user id: the server binds the authenticated identity (SEC-02), and every call
runs under a deadline (RES-01).
"""

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as ToolTimeout
from datetime import date, datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Sao_Paulo")
MOCK = "MOCK — simulated data for this challenge, not a real Getnet account"

MERCHANTS: dict[str, dict[str, Any]] = {
    "cliente1988": {
        "name": "Padaria Pão Dourado (MOCK)", "plan": "Get Smart — plano Flex",
        "settlement_rules": {"pix": 0, "debit": 1, "credit": 30},
        "bank_account": {"bank": "Banco 033", "branch": "0001", "account": "****4321"},
        "receivables_available": 3200.00,
        "terminals": [
            {"id": "T-1001", "model": "Get Smart", "status": "offline", "connectivity": "wifi", "signal": "weak",
             "last_seen_minutes": 95},
            {"id": "T-1002", "model": "Get Mini", "status": "online", "connectivity": "4g", "signal": "good",
             "last_seen_minutes": 1},
        ],
        "sales_yesterday": [
            {"id": "V-501", "method": "pix", "amount": 89.90, "terminal": "T-1002"},
            {"id": "V-502", "method": "debit", "amount": 120.50, "terminal": "T-1002"},
            {"id": "V-503", "method": "credit", "amount": 450.00, "terminal": "T-1001", "installments": 3},
        ],
        "declines_today": [{"terminal": "T-1002", "code": "51", "amount": 230.00}],
    },
    "cliente2000": {
        "name": "Ótica Visão Clara (MOCK)", "plan": "Get Clássica — plano Padrão",
        "settlement_rules": {"pix": 0, "debit": 1, "credit": 30},
        "bank_account": {"bank": "Banco 341", "branch": "0420", "account": "****8800"},
        "receivables_available": 0.0,
        "terminals": [{"id": "T-2001", "model": "Get Clássica", "status": "online", "connectivity": "4g",
                       "signal": "good", "last_seen_minutes": 3}],
        "sales_yesterday": [{"id": "V-901", "method": "credit", "amount": 980.00, "terminal": "T-2001",
                             "installments": 1}],
        "declines_today": [],
    },
}

DECLINE_CODES = {
    "05": "Transação não autorizada pelo emissor — o cliente deve contatar o banco do cartão.",
    "51": "Saldo/limite insuficiente — peça outro meio de pagamento.",
    "54": "Cartão vencido — peça outro cartão.",
    "55": "Senha incorreta — o cliente pode tentar novamente.",
    "57": "Transação não permitida para o cartão — tente outra modalidade (débito/crédito).",
    "91": "Emissor indisponível — tente novamente em alguns minutos.",
}


def business_today() -> date:
    return datetime.now(TZ).date()


def add_business_days(start: date, days: int) -> date:
    current = start
    while days > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            days -= 1
    return current


def _merchant(user_id: str) -> dict[str, Any] | None:
    return MERCHANTS.get(user_id)


def get_merchant_profile(user_id: str) -> dict[str, Any]:
    m = _merchant(user_id)
    if m is None:
        return {"error": "customer_not_found", "data_source": MOCK}
    return {"data_source": MOCK, "name": m["name"], "plan": m["plan"], "bank_account": m["bank_account"],
            "settlement_rules_business_days": m["settlement_rules"], "terminals": [t["id"] for t in m["terminals"]]}


def get_sales_and_settlements(user_id: str) -> dict[str, Any]:
    m = _merchant(user_id)
    if m is None:
        return {"error": "customer_not_found", "data_source": MOCK}
    today = business_today()
    sale_day = today - timedelta(days=1)
    rows = []
    for sale in m["sales_yesterday"]:
        expected = add_business_days(sale_day, m["settlement_rules"][sale["method"]])
        rows.append({**sale, "sale_date": sale_day.isoformat(), "expected_deposit": expected.isoformat(),
                     "status": "deposited" if expected <= today else "scheduled"})
    return {"data_source": MOCK, "today": today.isoformat(), "bank_account": m["bank_account"], "sales": rows,
            "total_deposited": round(sum(r["amount"] for r in rows if r["status"] == "deposited"), 2),
            "total_scheduled": round(sum(r["amount"] for r in rows if r["status"] == "scheduled"), 2)}


def diagnose_terminal(user_id: str, terminal_id: str | None = None, error_code: str | None = None) -> dict[str, Any]:
    m = _merchant(user_id)
    if m is None:
        return {"error": "customer_not_found", "data_source": MOCK}
    terminals = [t for t in m["terminals"] if terminal_id in (None, "", t["id"])]
    if not terminals:
        return {"error": "terminal_not_found", "terminal_id": terminal_id, "data_source": MOCK}
    findings = []
    for t in terminals:
        steps = []
        if t["status"] != "online":
            steps = ["Reinicie a maquininha (segure o botão de energia por 10 s).",
                     f"Verifique a conexão {t['connectivity']}: sinal {t['signal']}; aproxime do roteador ou troque para o chip 4G.",
                     "Confirme data/hora e se há atualização pendente no menu Configurações.",
                     "Se continuar offline após 15 min, fale com o suporte para troca do equipamento."]
        findings.append({**t, "healthy": t["status"] == "online", "recommended_steps": steps})
    declines = [{**d, "meaning": DECLINE_CODES.get(d["code"], "Código não mapeado — contate o suporte.")}
                for d in m["declines_today"] if terminal_id in (None, "", d["terminal"])]
    result: dict[str, Any] = {"data_source": MOCK, "terminals": findings, "declines_today": declines}
    if error_code:
        result["error_code"] = {"code": error_code, "meaning": DECLINE_CODES.get(error_code, "Código não mapeado.")}
    return result


def request_receivables_advance(user_id: str, amount: float) -> dict[str, Any]:
    """HITL-05: never executes. Validates and opens a request that a human must approve."""
    m = _merchant(user_id)
    if m is None:
        return {"error": "customer_not_found", "data_source": MOCK}
    available = m["receivables_available"]
    if amount <= 0 or amount > available:
        return {"data_source": MOCK, "status": "rejected", "reason": "amount_out_of_range", "requested": amount,
                "available": available}
    return {"data_source": MOCK, "status": "pending_human_approval", "action": "receivables_advance",
            "requested": amount, "available": available, "executed": False}


TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    "get_merchant_profile": get_merchant_profile,
    "get_sales_and_settlements": get_sales_and_settlements,
    "diagnose_terminal": diagnose_terminal,
    "request_receivables_advance": request_receivables_advance,
}

TOOL_SPECS: list[dict[str, Any]] = [
    {"name": "get_merchant_profile",
     "description": "Current merchant's plan, bank account for deposits, settlement rules and terminal ids.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_sales_and_settlements",
     "description": "Yesterday's sales of the current merchant with the expected deposit date and status of each "
                    "(use for 'when will my money / sales be deposited').",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "diagnose_terminal",
     "description": "Diagnose the merchant's card machines: connectivity status, recommended steps and today's "
                    "declined transactions with the meaning of each decline code. Omit terminal_id to check all.",
     "input_schema": {"type": "object", "properties": {
         "terminal_id": {"type": "string", "description": "e.g. T-1001; omit to check every machine"},
         "error_code": {"type": "string", "description": "decline/error code shown on the machine, if any"}}}},
    {"name": "request_receivables_advance",
     "description": "Open a receivables-advance (antecipação) request for human approval. Does NOT execute it.",
     "input_schema": {"type": "object", "properties": {"amount": {"type": "number", "description": "BRL amount"}},
                      "required": ["amount"]}},
]

_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="tool")


def execute_tool(user_id: str, name: str, arguments: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]:
    """The authenticated identity is bound here; any LLM-supplied user_id is dropped (SEC-02)."""
    fn = TOOLS.get(name)
    if fn is None:
        return {"error": "unknown_tool", "name": name}
    args = {k: v for k, v in arguments.items() if k not in ("user_id", "customer_id")}
    future = _POOL.submit(fn, user_id, **args)
    try:
        return future.result(timeout=timeout)
    except ToolTimeout:
        return {"error": "tool_timeout", "name": name, "timeout_seconds": timeout}
    except TypeError as exc:
        return {"error": "invalid_arguments", "detail": str(exc)}
