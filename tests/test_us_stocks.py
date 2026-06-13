"""美股 SEC EDGAR 解析 + 確定性衍生欄位 + W2 P/E cross-check 的純單元測試。

不打網路：把合成的 companyfacts JSON 餵給抽出的純函式 `_facts_to_evidence`，
驗證 raw 欄位、跨概念取最新、年報/季報區分，以及衍生欄位（FCF/槓桿/利息保障/
毛利率/ROE/net-net）的算術。W2 P/E cross-check 直接構造 EvidenceStore 測 audit。
"""

from datetime import date

from cyber_sages.config import AuditConfig
from cyber_sages.data.evidence import Evidence, EvidenceStore
from cyber_sages.data.us_stocks import USStockProvider
from cyber_sages.verify.data_audit import deterministic_checks


def _usd_annual(val, end="2024-12-31", start="2024-01-01", fy=2024, form="10-K"):
    return {"form": form, "start": start, "end": end, "val": val, "fy": fy}


def _usd_instant(val, end="2024-12-31", fy=2024, form="10-K"):
    # 資產負債表科目是時點值（無 start），不受年報 duration 篩選
    return {"form": form, "end": end, "val": val, "fy": fy}


def _facts() -> dict:
    g = {
        "Revenues": {"units": {"USD": [_usd_annual(1000)]}},
        "NetIncomeLoss": {"units": {"USD": [_usd_annual(200)]}},
        "EarningsPerShareDiluted": {"units": {"USD/shares": [_usd_annual(2.0)]}},
        "Assets": {"units": {"USD": [_usd_instant(5000)]}},
        "Liabilities": {"units": {"USD": [_usd_instant(3000)]}},
        "StockholdersEquity": {"units": {"USD": [_usd_instant(2000)]}},
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [_usd_annual(300)]}},
        "GrossProfit": {"units": {"USD": [_usd_annual(600)]}},
        "OperatingIncomeLoss": {"units": {"USD": [_usd_annual(400)]}},
        "AssetsCurrent": {"units": {"USD": [_usd_instant(1500)]}},
        "LiabilitiesCurrent": {"units": {"USD": [_usd_instant(800)]}},
        "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [_usd_annual(100)]}},
        "DepreciationDepletionAndAmortization": {"units": {"USD": [_usd_annual(50)]}},
        "InterestExpense": {"units": {"USD": [_usd_annual(20)]}},
    }
    dei = {"EntityCommonStockSharesOutstanding": {"units": {"shares": [_usd_instant(100)]}}}
    return {"facts": {"us-gaap": g, "dei": dei}}


def _parse() -> dict[str, Evidence]:
    evs = USStockProvider._facts_to_evidence(_facts(), "http://x")
    return {e.field: e for e in evs}


# ---------- raw 欄位 ----------

def test_raw_annual_fields_emitted():
    m = _parse()
    assert m["revenue_annual"].value == 1000
    assert m["net_income_annual"].value == 200
    assert m["gross_profit_annual"].value == 600
    assert m["capex_annual"].value == 100
    assert m["current_assets_annual"].value == 1500
    assert m["shares_outstanding"].value == 100


# ---------- 衍生欄位算術 ----------

def test_derived_fundamentals_arithmetic():
    m = _parse()
    assert m["free_cash_flow_annual"].value == 200.0     # OCF 300 − CapEx 100
    assert m["working_capital"].value == 700.0           # 1500 − 800
    assert m["net_net_value"].value == -1500.0           # 1500 − 3000
    assert m["debt_to_equity"].value == 1.5              # 3000 / 2000
    assert m["interest_coverage"].value == 20.0          # 400 / 20
    assert m["gross_margin_pct"].value == 60.0           # 600 / 1000 × 100
    assert m["roe_pct"].value == 10.0                    # 200 / 2000 × 100
    # 衍生欄位都標明來源與公式，供 cite-check 溯源
    assert "computed from SEC" in m["free_cash_flow_annual"].source
    assert "確定性衍生" in m["roe_pct"].note


def test_derived_skips_when_input_missing():
    # 抽掉利息費用與流動負債 → interest_coverage / working_capital 不該出現
    facts = _facts()
    del facts["facts"]["us-gaap"]["InterestExpense"]
    del facts["facts"]["us-gaap"]["LiabilitiesCurrent"]
    m = {e.field: e for e in USStockProvider._facts_to_evidence(facts, "http://x")}
    assert "interest_coverage" not in m
    assert "working_capital" not in m
    # 但不依賴它們的衍生欄位仍在
    assert "debt_to_equity" in m and "roe_pct" in m


def test_derived_handles_zero_denominator():
    facts = _facts()
    facts["facts"]["us-gaap"]["StockholdersEquity"]["units"]["USD"] = [_usd_instant(0)]
    m = {e.field: e for e in USStockProvider._facts_to_evidence(facts, "http://x")}
    assert "debt_to_equity" not in m  # 除以零 → 略過，不發 inf/NaN
    assert "roe_pct" not in m


# ---------- _latest 年報/季報區分 ----------

def test_latest_prefers_recent_and_respects_duration():
    entries = [
        _usd_annual(100, end="2023-12-31", start="2023-01-01"),
        _usd_annual(200, end="2024-12-31", start="2024-01-01"),
        # 一筆短 duration（季）混在 10-K 概念裡，年報篩選須排除
        {"form": "10-K", "start": "2024-10-01", "end": "2024-12-31", "val": 55},
    ]
    annual = USStockProvider._latest(entries, form_prefix="10-K", min_duration_days=300)
    assert annual["val"] == 200  # 取最新且滿足年度 duration


def test_latest_picks_by_end_date_not_value():
    # 取最新 end，與 val 大小無關（舊年度值較大也不該勝出）
    entries = [
        _usd_annual(999, end="2023-12-31", start="2023-01-01"),
        _usd_annual(10, end="2024-12-31", start="2024-01-01"),
    ]
    assert USStockProvider._latest(entries, form_prefix="10-K", min_duration_days=300)["val"] == 10


def test_latest_form_prefix_routes_annual_vs_quarterly():
    # 同概念混 10-K / 10-Q，form_prefix 正確分流到兩個出口
    entries = [
        _usd_annual(200, end="2024-12-31", start="2024-01-01", form="10-K"),
        {"form": "10-Q", "start": "2025-01-01", "end": "2025-03-31", "val": 60},
    ]
    annual = USStockProvider._latest(entries, form_prefix="10-K", min_duration_days=300)
    quarterly = USStockProvider._latest(entries, form_prefix="10-Q")
    assert annual["val"] == 200 and quarterly["val"] == 60


# ---------- W2 P/E cross-check ----------

def _us_store(trailing_pe: float, eps_annual: float = 2.0, price: float = 20.0) -> EvidenceStore:
    store = EvidenceStore(ticker="TEST", market="US")
    store.add_all([
        Evidence(category="quote", field="last_price", value=price, unit="USD", source="yf"),
        Evidence(category="quote", field="trailing_pe", value=trailing_pe, source="yfinance info (derived)"),
        Evidence(category="fundamentals", field="eps_diluted_annual", value=eps_annual,
                 unit="USD/shares", source="SEC", as_of=date.today()),
    ])
    return store


def test_pe_crosscheck_passes_when_aligned():
    # price 20 / eps 2 = implied P/E 10；yfinance 11 → 偏離 ~9% < 25%，無 finding
    findings = deterministic_checks(_us_store(trailing_pe=11.0), AuditConfig())
    assert not [f for f in findings if f.check == "cross_source" and "P/E" in f.message]


def test_pe_crosscheck_errors_on_large_divergence():
    # implied 10 vs yfinance 30 → 偏離 ~67% > 25% → error
    findings = deterministic_checks(_us_store(trailing_pe=30.0), AuditConfig())
    pe = [f for f in findings if f.check == "cross_source" and "P/E" in f.message]
    assert len(pe) == 1 and pe[0].severity == "error"


def test_pe_crosscheck_skipped_for_tw():
    # TW 不發 trailing_pe；即便構造了也只在 market==US 觸發
    store = _us_store(trailing_pe=30.0)
    store.market = "TW"
    findings = deterministic_checks(store, AuditConfig())
    assert not [f for f in findings if f.check == "cross_source" and "P/E" in f.message]


def _has_pe_finding(store: EvidenceStore) -> bool:
    return bool([f for f in deterministic_checks(store, AuditConfig())
                 if f.check == "cross_source" and "P/E" in f.message])


def test_pe_crosscheck_skips_negative_eps():
    # 虧損公司 EPS_annual < 0 → P/E 無意義，須早退不發 finding（即便數值上偏離很大）
    assert not _has_pe_finding(_us_store(trailing_pe=30.0, eps_annual=-2.0))


def test_pe_crosscheck_skips_nonpositive_yf_pe():
    # yfinance trailing_pe ≤ 0（虧損股 yfinance 常給 0/None）→ 不做比對
    assert not _has_pe_finding(_us_store(trailing_pe=0.0))


def test_pe_crosscheck_skips_when_eps_missing():
    store = EvidenceStore(ticker="TEST", market="US")
    store.add_all([
        Evidence(category="quote", field="last_price", value=20.0, unit="USD", source="yf"),
        Evidence(category="quote", field="trailing_pe", value=30.0, source="yfinance info (derived)"),
        # 無 eps_diluted_annual
    ])
    assert not _has_pe_finding(store)


# ---------- short interest（二手 FINRA，chips 類別）----------

def test_short_interest_evidence():
    info = {
        "shortPercentOfFloat": 0.0106,
        "sharesShort": 155_886_024,
        "sharesShortPriorMonth": 134_675_274,
        "shortRatio": 3.12,
        "dateShortInterest": 1_700_000_000,
    }
    m = {e.field: e for e in USStockProvider._short_interest_evidence(info, "u")}
    assert m["short_percent_of_float"].value == 1.06          # 0.0106 × 100
    assert m["shares_short"].value == 155_886_024
    assert m["short_ratio"].value == 3.12
    # MoM 變化 = (155886024/134675274 - 1) × 100 ≈ 15.75
    assert m["short_interest_change_mom_pct"].value == 15.75
    assert all(e.category == "chips" for e in m.values())
    assert all("second-hand" in e.source for e in m.values())
    assert m["short_percent_of_float"].as_of == date(2023, 11, 14)  # 自 timestamp 轉


def test_short_interest_skips_missing_fields():
    # 缺 prior month → 無 MoM；完全無 short 欄位 → 空
    m = {e.field for e in USStockProvider._short_interest_evidence({"sharesShort": 100}, "u")}
    assert m == {"shares_short"}
    assert USStockProvider._short_interest_evidence({}, "u") == []
