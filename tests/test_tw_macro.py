"""台股 provider 解析 + 總經計算 + 市場感知審核（純單元，不打 API）。

樣本 dict 取自 FinMind 2330 實測回傳的欄位結構。
"""

from datetime import date, timedelta

import pandas as pd

from cyber_sages.config import AuditConfig
from cyber_sages.data.base import detect_instrument, detect_market, is_tw_etf
from cyber_sages.data.evidence import Evidence, EvidenceStore
from cyber_sages.data.indicators import compute_indicator_evidence
from cyber_sages.data.tw_stocks import (
    BALANCE_FIELDS,
    INCOME_FIELDS,
    TWStockProvider,
    to_stock_id,
)
from cyber_sages.verify.data_audit import deterministic_checks


# ---------- 路由 ----------

def test_detect_market():
    assert detect_market("AAPL") == "US"
    assert detect_market("BRK.B") == "US"
    assert detect_market("2330") == "TW"
    assert detect_market("2330.TW") == "TW"
    assert detect_market("0050") == "TW"
    assert detect_market("6488.TWO") == "TW"


def test_to_stock_id():
    assert to_stock_id("2330") == "2330"
    assert to_stock_id("2330.TW") == "2330"
    assert to_stock_id("2330.two") == "2330"


def test_etf_detection():
    assert is_tw_etf("0050") and is_tw_etf("00878") and is_tw_etf("00631L")
    assert not is_tw_etf("2330") and not is_tw_etf("AAPL")
    assert detect_instrument("0050") == "etf"
    assert detect_instrument("2330") == "stock"


def test_digest_scale_hint():
    # 大數值附可讀量級，台股用 億/兆、其餘用 B/T；讓 LLM 直接引用正確刻度
    tw = Evidence(category="fundamentals", field="total_assets",
                  value=8_660_949_685_000.0, unit="TWD", source="x")
    assert "≈8.661兆" in tw.digest_line()
    us = Evidence(category="fundamentals", field="shares_outstanding",
                  value=24_200_000_000.0, unit="shares", source="x")
    assert "≈24.2B" in us.digest_line()
    small = Evidence(category="macro", field="nonfarm", value=172000, unit="persons",
                     source="x")
    assert "≈" not in small.digest_line()  # < 1e6 不加提示


# ---------- 財報解析（綜合損益表 + 資產負債表）----------

def test_statement_evidence_picks_latest_quarter_and_maps_fields():
    rows = [
        {"date": "2025-12-31", "type": "Revenue", "value": 900.0},
        {"date": "2026-03-31", "type": "Revenue", "value": 1000.0},
        {"date": "2026-03-31", "type": "IncomeAfterTaxes", "value": 300.0},
        {"date": "2026-03-31", "type": "EPS", "value": 13.7},
        {"date": "2026-03-31", "type": "GrossProfit", "value": 600.0},
        {"date": "2026-03-31", "type": "OperatingExpenses", "value": 50.0},  # 不在 map → 忽略
    ]
    evs = TWStockProvider._statement_evidence(rows, INCOME_FIELDS, "url", "src")
    by_field = {e.field: e for e in evs}
    # 取最新季別 2026-03-31，舊季營收 900 不該出現
    assert by_field["revenue_latest_quarter"].value == 1000.0
    assert by_field["net_income_latest_quarter"].value == 300.0
    assert by_field["eps_latest_quarter"].value == 13.7
    assert by_field["eps_latest_quarter"].unit == "TWD/share"
    assert all(e.category == "fundamentals" for e in evs)
    assert by_field["revenue_latest_quarter"].as_of == date(2026, 3, 31)
    assert "OperatingExpenses" not in {e.note for e in evs}  # 未對映欄位被丟掉


def test_ttm_sums_last_four_quarters():
    # 單季值，近四季 EPS 加總應為 TTM；超過四季只取最近四季
    rows = [
        {"date": "2024-12-31", "type": "EPS", "value": 14.45},  # 第 5 近，不計入
        {"date": "2025-03-31", "type": "EPS", "value": 13.95},
        {"date": "2025-06-30", "type": "EPS", "value": 15.36},
        {"date": "2025-09-30", "type": "EPS", "value": 17.44},
        {"date": "2025-12-31", "type": "EPS", "value": 19.51},
        {"date": "2025-12-31", "type": "Revenue", "value": 100.0},
    ]
    evs = TWStockProvider._ttm_evidence(rows, "url")
    by_field = {e.field: e for e in evs}
    # 13.95+15.36+17.44+19.51 = 66.26（不含最舊的 14.45）
    assert by_field["eps_ttm"].value == 66.26
    assert by_field["eps_ttm"].as_of == date(2025, 12, 31)
    # Revenue 只有一季 → 不足四季，不硬湊
    assert "revenue_ttm" not in by_field


def test_ttm_skips_when_fewer_than_four_quarters():
    rows = [{"date": "2026-03-31", "type": "EPS", "value": 22.08}]
    assert TWStockProvider._ttm_evidence(rows, "url") == []


def test_balance_sheet_excludes_percentage_variants():
    rows = [
        {"date": "2026-03-31", "type": "TotalAssets", "value": 7000.0},
        {"date": "2026-03-31", "type": "TotalAssets_per", "value": 100.0},  # 百分比變體
        {"date": "2026-03-31", "type": "Liabilities", "value": 2000.0},
        {"date": "2026-03-31", "type": "Equity", "value": 5000.0},
    ]
    evs = TWStockProvider._statement_evidence(rows, BALANCE_FIELDS, "url", "src")
    by_field = {e.field: e.value for e in evs}
    assert by_field == {"total_assets": 7000.0, "total_liabilities": 2000.0, "equity": 5000.0}


# ---------- 月營收 + YoY ----------

def test_month_revenue_yoy():
    rows = [
        {"date": "2025-06-01", "revenue": 200, "revenue_month": 5, "revenue_year": 2025},
        {"date": "2026-06-01", "revenue": 250, "revenue_month": 5, "revenue_year": 2026},
    ]
    evs = TWStockProvider._month_revenue_evidence(rows, "2330")
    by_field = {e.field: e for e in evs}
    assert by_field["monthly_revenue"].value == 250
    # YoY = 250/200 - 1 = 25%
    assert by_field["revenue_yoy_pct"].value == 25.0
    assert by_field["revenue_yoy_pct"].unit == "%"


def test_month_revenue_without_prior_year_skips_yoy():
    rows = [{"date": "2026-06-01", "revenue": 250, "revenue_month": 5, "revenue_year": 2026}]
    fields = {e.field for e in TWStockProvider._month_revenue_evidence(rows, "2330")}
    assert "monthly_revenue" in fields
    assert "revenue_yoy_pct" not in fields


# ---------- 籌碼面（三大法人 + 融資券）----------

def test_institutional_net_buy_groups():
    rows = [
        {"date": "2026-06-11", "name": "Foreign_Investor", "buy": 1000, "sell": 400},
        {"date": "2026-06-11", "name": "Foreign_Dealer_Self", "buy": 100, "sell": 50},
        {"date": "2026-06-11", "name": "Investment_Trust", "buy": 900, "sell": 300},
        {"date": "2026-06-11", "name": "Dealer_self", "buy": 200, "sell": 250},
        {"date": "2026-06-10", "name": "Foreign_Investor", "buy": 9, "sell": 9},  # 舊日期忽略
    ]
    evs = TWStockProvider._institutional_evidence(rows, "2330")
    by_field = {e.field: e.value for e in evs}
    assert by_field["foreign_net_buy"] == 650          # (1000-400)+(100-50)
    assert by_field["trust_net_buy"] == 600            # 900-300
    assert by_field["dealer_net_buy"] == -50           # 200-250
    assert by_field["institutional_net_buy_total"] == 1200  # 650+600-50
    assert all(e.category == "chips" for e in evs)


def test_institutional_total_robust_to_unmapped_name():
    # FinMind 若新增/改名類別，合計仍涵蓋（= 所有 name 加總），且 note 標出未分群者
    rows = [
        {"date": "2026-06-11", "name": "Foreign_Investor", "buy": 1000, "sell": 400},
        {"date": "2026-06-11", "name": "Investment_Trust", "buy": 900, "sell": 300},
        {"date": "2026-06-11", "name": "Brand_New_Category", "buy": 500, "sell": 100},
    ]
    evs = TWStockProvider._institutional_evidence(rows, "2330")
    total = next(e for e in evs if e.field == "institutional_net_buy_total")
    assert total.value == 1600  # 600 + 600 + 400，新類別仍計入
    assert "Brand_New_Category" in total.note and "未分群" in total.note


def test_margin_evidence():
    rows = [{"date": "2026-06-11", "MarginPurchaseTodayBalance": 27470,
             "ShortSaleTodayBalance": 11}]
    by_field = {e.field: e.value for e in TWStockProvider._margin_evidence(rows, "2330")}
    assert by_field["margin_balance"] == 27470
    assert by_field["short_balance"] == 11


# ---------- 技術指標共用邏輯 ----------

def test_compute_indicator_evidence_fields():
    # 260 個有漲有跌的收盤（純遞增會讓 RSI 因 loss=0 而正確略過）
    close = pd.Series([100 + (i % 7) - 3 + i * 0.1 for i in range(260)], dtype=float)
    evs = compute_indicator_evidence(close, as_of=date(2026, 6, 11), url=None,
                                     source="test", price_unit="TWD")
    fields = {e.field for e in evs}
    assert {"sma_20", "sma_50", "sma_200", "rsi_14", "macd_histogram",
            "return_1y_pct"}.issubset(fields)
    sma20 = next(e for e in evs if e.field == "sma_20")
    assert sma20.unit == "TWD"      # 價格單位有正確帶入
    assert sma20.category == "history"


def test_52w_distance_fields():
    # 距高/低點百分比確定性算好，分析師可直接引用、不必自己算「距高點 X%」
    import pandas as pd
    close = pd.Series([918.0] + [1500.0] * 250 + [2425.0, 2310.0])  # 高 2425、低 918、現 2310
    evs = {e.field: e.value for e in compute_indicator_evidence(
        close, as_of=date(2026, 6, 12), url=None, source="t", price_unit="TWD")}
    assert evs["high_52w"] == 2425.0 and evs["low_52w"] == 918.0
    # (2425-2310)/2425*100 = 4.74；(2310-918)/918*100 = 151.63
    assert evs["pct_below_52w_high"] == 4.74
    assert evs["pct_above_52w_low"] == 151.63


def test_compute_indicator_evidence_too_short():
    assert compute_indicator_evidence(pd.Series(range(10), dtype=float),
                                      as_of=date.today(), url=None, source="t") == []


# ---------- 市場感知審核 ----------

def _ev(category, field, value, **kw):
    return Evidence(category=category, field=field, value=value, source="t",
                    as_of=kw.pop("as_of", date.today()), **kw)


def _tw_store_minimal() -> EvidenceStore:
    store = EvidenceStore(ticker="2330", market="TW")
    store.add_all([
        _ev("quote", "latest_close", 2250.0, unit="TWD"),
        _ev("fundamentals", "revenue_latest_quarter", 1e12, unit="TWD"),
        _ev("fundamentals", "net_income_latest_quarter", 3e11, unit="TWD"),
        _ev("fundamentals", "eps_latest_quarter", 13.7, unit="TWD/share"),
        _ev("history", "sma_20", 2200.0, unit="TWD"),
        _ev("news", "headline_1", "台積電法說"),
        _ev("chips", "foreign_net_buy", 650, unit="shares"),
    ])
    return store


def test_tw_audit_accepts_quarterly_fundamentals():
    findings = deterministic_checks(_tw_store_minimal(), AuditConfig())
    errors = [f for f in findings if f.severity == "error"]
    assert errors == []  # 台股季報口徑不該誤判「no first-hand financials」


def test_tw_audit_warns_when_chips_missing():
    store = _tw_store_minimal()
    store.items = [e for e in store.items if e.category != "chips"]
    findings = deterministic_checks(store, AuditConfig())
    assert any("籌碼" in f.message or "chips" in f.message.lower() for f in findings)
    assert all(f.severity != "error" for f in findings)  # 缺籌碼只是 warning


def test_etf_audit_does_not_error_on_missing_fundamentals():
    # 0050 等 ETF 無個股財報，缺基本面只能是 warning，不該降級封頂信心
    store = EvidenceStore(ticker="0050", market="TW", instrument="etf")
    store.add_all([
        _ev("quote", "latest_close", 99.85, unit="TWD"),
        _ev("history", "sma_20", 100.6, unit="TWD"),
        _ev("news", "headline_1", "0050 成分股調整"),
        _ev("chips", "institutional_net_buy_total", -167_737_947, unit="shares"),
    ])
    findings = deterministic_checks(store, AuditConfig())
    assert all(f.severity != "error" for f in findings)
    assert any(f.check == "completeness" and "ETF" in f.message for f in findings)


def test_us_audit_still_requires_annual_fundamentals():
    store = EvidenceStore(ticker="AAPL", market="US")
    store.add_all([
        _ev("quote", "latest_close", 280.0, unit="USD"),
        _ev("fundamentals", "revenue_latest_quarter", 1e11, unit="USD"),  # 只有季、無年
        _ev("history", "sma_20", 270.0, unit="USD"),
    ])
    findings = deterministic_checks(store, AuditConfig())
    assert any(f.check == "completeness" and "financials" in f.message
               and f.severity == "error" for f in findings)


def test_macro_freshness_warning():
    store = EvidenceStore(ticker="AAPL", market="US")
    old = date.today() - timedelta(days=120)
    store.add_all([
        _ev("quote", "latest_close", 280.0, unit="USD"),
        _ev("fundamentals", "revenue_annual", 1e11, unit="USD"),
        _ev("fundamentals", "net_income_annual", 2e10, unit="USD"),
        _ev("history", "sma_20", 270.0, unit="USD"),
        _ev("macro", "fed_funds_rate", 3.63, unit="%", as_of=old),
    ])
    findings = deterministic_checks(store, AuditConfig())
    assert any(f.check == "freshness" and "macro" in f.message.lower() for f in findings)
