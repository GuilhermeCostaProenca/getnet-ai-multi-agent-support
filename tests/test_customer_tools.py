from datetime import date

from app.tools.customer_tools import MOCK_LABEL, diagnose_terminal, execute_tool, get_sales_and_settlements

MONDAY = date(2026, 11, 2)


def test_ac03_settlement_dates_follow_rules():
    out = get_sales_and_settlements("cliente1988", days_back=1, today=MONDAY)
    sale_day = "2026-11-01"  # Sunday
    by_method = {(i["method"], i["installment"]): i for i in out["items"] if i["sale_date"] == sale_day}
    assert by_method[("pix", "1/1")]["expected_deposit_date"] == sale_day
    assert by_method[("debit", "1/1")]["expected_deposit_date"] == "2026-11-02"  # next business day
    assert by_method[("credit", "1/1")]["expected_deposit_date"] == "2026-12-01"  # D+30
    assert by_method[("credit", "3/3")]["amount"] == 200.0


def test_business_day_uses_brazil_timezone_not_utc():
    from datetime import datetime, timezone

    from app.tools.customer_tools import business_today

    # 01:30 UTC on Nov 4 is still 22:30 on Nov 3 in São Paulo
    assert business_today(datetime(2026, 11, 4, 1, 30, tzinfo=timezone.utc)) == date(2026, 11, 3)


def test_ac08_decline_codes_are_explained():
    out = diagnose_terminal("cliente1988", None, "51")
    assert out["decline_codes"][0]["code"] == "51"
    assert "Insufficient" in out["decline_codes"][0]["meaning"]


def test_ac05_offline_wifi_terminal_gets_steps():
    t1001 = next(t for t in diagnose_terminal("cliente1988")["terminals"] if t["terminal_id"] == "T-1001")
    assert any("offline" in i for i in t1001["issues"]) and t1001["recommended_steps"]


def test_ac20_unknown_user_returns_not_found():
    assert execute_tool("ghost", "get_sales_and_settlements", {"days_back": 3})["error"] == "customer_not_found"


def test_ac15_user_id_argument_from_llm_is_ignored():
    out = execute_tool("cliente1988", "get_merchant_profile", {"user_id": "cliente2000"})
    assert out["user_id"] == "cliente1988"


def test_ac22_every_tool_output_is_labeled_mock():
    for name, args in [("get_merchant_profile", {}), ("get_sales_and_settlements", {"days_back": 7}),
                       ("diagnose_terminal", {"terminal_id": None, "error_code": None})]:
        assert execute_tool("cliente1988", name, args)["data_source"] == MOCK_LABEL


def test_unknown_tool_and_bad_arguments_do_not_raise():
    assert execute_tool("cliente1988", "drop_tables", {})["error"] == "unknown_tool"
    assert execute_tool("cliente1988", "get_merchant_profile", {"bogus": 1})["error"] == "invalid_arguments"
