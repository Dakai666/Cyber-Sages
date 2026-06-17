"""美股 SEC EDGAR 解析 + 確定性衍生欄位 + W2 P/E cross-check 的純單元測試。

不打網路：把合成的 companyfacts JSON 餵給抽出的純函式 `_facts_to_evidence`，
驗證 raw 欄位、跨概念取最新、年報/季報區分，以及衍生欄位（FCF/槓桿/利息保障/
毛利率/ROE/net-net）的算術。W2 P/E cross-check 直接構造 EvidenceStore 測 audit。
"""

from datetime import date

import pandas as pd

from cyber_sages.config import AuditConfig
from cyber_sages.data.evidence import Evidence, EvidenceStore
from cyber_sages.data.us_stocks import USStockProvider, drop_phantom_bars
from cyber_sages.verify.data_audit import deterministic_checks


# ---------- Spec F / S1：幽靈 bar 防呆 ----------

def _bars(rows):
    """rows: list of (close, volume) → DataFrame，模擬 yfinance 日線（時間升冪）。"""
    idx = pd.to_datetime([f"2026-06-{12 + i:02d}" for i in range(len(rows))])
    return pd.DataFrame({"Close": [c for c, _ in rows],
                         "Volume": [v for _, v in rows]}, index=idx)


def test_drop_phantom_bars_removes_zero_volume_tail():
    # SPCX 實例：最後一根 0 量佔位 bar 被剔除，取到前一根真實 bar
    hist = _bars([(160.9, 519_000_000), (192.5, 256_000_000), (192.5, 0)])
    cleaned = drop_phantom_bars(hist)
    assert len(cleaned) == 2
    assert float(cleaned.iloc[-1]["Close"]) == 192.5
    assert int(cleaned.iloc[-1]["Volume"]) == 256_000_000


def test_drop_phantom_bars_keeps_all_real_bars():
    hist = _bars([(100.0, 1_000), (101.0, 2_000), (102.0, 3_000)])
    assert len(drop_phantom_bars(hist)) == 3


def test_drop_phantom_bars_tolerates_missing_volume_column():
    df = pd.DataFrame({"Close": [1.0, 2.0]})
    assert len(drop_phantom_bars(df)) == 2  # 無 Volume 欄位時原樣返回，不炸


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


# ---------- eps_ttm（最近四季合計，issue #19）----------

def _eps_entries(items):
    """items: list of (start, end, val, form) → companyfacts gaap dict for EPS only。"""
    return {"EarningsPerShareDiluted": {"units": {"USD/shares": [
        {"form": form, "start": s, "end": e, "val": v} for s, e, v, form in items]}}}


def test_eps_ttm_reconstructs_q4_from_fy_minus_q123():
    # 最近期是 Q1'25（10-Q）→ TTM = Q1'25 + Q4'24 + Q3'24 + Q2'24。
    # Q4'24 無 10-Q，須由 FY'24 − (Q1'24+Q2'24+Q3'24) 反推。
    gaap = _eps_entries([
        ("2024-01-01", "2024-03-31", 0.5, "10-Q"),   # Q1'24
        ("2024-04-01", "2024-06-30", 0.6, "10-Q"),   # Q2'24
        ("2024-07-01", "2024-09-30", 0.7, "10-Q"),   # Q3'24
        ("2024-01-01", "2024-12-31", 2.6, "10-K"),   # FY'24 → Q4'24 = 2.6-1.8 = 0.8
        ("2025-01-01", "2025-03-31", 0.9, "10-Q"),   # Q1'25
    ])
    val, as_of, note = USStockProvider._eps_ttm(gaap)
    # 最近四季 = Q2'24 0.6 + Q3'24 0.7 + Q4'24 0.8 + Q1'25 0.9 = 3.0
    assert val == 3.0
    assert as_of == date(2025, 3, 31)
    assert "反推" in note


def test_eps_ttm_ignores_ytd_cumulative_entries():
    # 10-Q 同時報單季與 YTD 累計（6/9 個月）；只算單季，YTD 須被 duration 篩掉。
    gaap = _eps_entries([
        ("2024-01-01", "2024-03-31", 0.5, "10-Q"),    # Q1 單季
        ("2024-04-01", "2024-06-30", 0.6, "10-Q"),    # Q2 單季
        ("2024-01-01", "2024-06-30", 1.1, "10-Q"),    # 上半年 YTD（須忽略）
        ("2024-07-01", "2024-09-30", 0.7, "10-Q"),    # Q3 單季
        ("2024-01-01", "2024-09-30", 1.8, "10-Q"),    # 前三季 YTD（須忽略）
        ("2024-01-01", "2024-12-31", 2.6, "10-K"),    # FY → Q4 = 0.8
    ])
    val, _, _ = USStockProvider._eps_ttm(gaap)
    # 全年四季（Q1+Q2+Q3+Q4）= FY = 2.6；若誤納 YTD 會爆掉
    assert val == 2.6


def test_eps_ttm_none_when_insufficient_quarters():
    # 只有一個 FY、無單季 → 無法合計四季 → None（寧缺勿錯）
    gaap = _eps_entries([("2024-01-01", "2024-12-31", 2.6, "10-K")])
    assert USStockProvider._eps_ttm(gaap) is None


def test_eps_ttm_none_when_quarters_not_contiguous():
    # 四季中間缺一季（Q2 缺）→ 不相鄰 → None，不把不連續季硬加
    gaap = _eps_entries([
        ("2024-01-01", "2024-03-31", 0.5, "10-Q"),   # Q1
        ("2024-07-01", "2024-09-30", 0.7, "10-Q"),   # Q3（缺 Q2）
        ("2024-10-01", "2024-12-31", 0.8, "10-Q"),   # Q4
        ("2025-01-01", "2025-03-31", 0.9, "10-Q"),   # Q1'25
    ])
    assert USStockProvider._eps_ttm(gaap) is None


def test_eps_ttm_picks_revised_value():
    # 同一季 end 出現多筆（10-Q/A、10-K restated 修訂常見）→ 後寫覆蓋＝取較新修訂值。
    # 此處 Q4'24 先 0.8、後修訂為 0.85，TTM 應採修訂值。
    gaap = _eps_entries([
        ("2024-01-01", "2024-03-31", 0.5, "10-Q"),
        ("2024-04-01", "2024-06-30", 0.6, "10-Q"),
        ("2024-07-01", "2024-09-30", 0.7, "10-Q"),
        ("2024-10-01", "2024-12-31", 0.8, "10-Q"),    # Q4 原值
        ("2024-10-01", "2024-12-31", 0.85, "10-Q/A"),  # Q4 修訂（同 end）
    ])
    val, _, _ = USStockProvider._eps_ttm(gaap)
    assert val == 0.5 + 0.6 + 0.7 + 0.85           # 採修訂後 0.85，非 0.8


def test_eps_ttm_emitted_as_evidence():
    # 四季齊全（含 FY 反推 Q4）→ _facts_to_evidence 應發 eps_ttm 欄位
    facts = _facts()
    facts["facts"]["us-gaap"]["EarningsPerShareDiluted"] = _eps_entries([
        ("2024-01-01", "2024-03-31", 0.5, "10-Q"),
        ("2024-04-01", "2024-06-30", 0.6, "10-Q"),
        ("2024-07-01", "2024-09-30", 0.7, "10-Q"),
        ("2024-01-01", "2024-12-31", 2.6, "10-K"),
    ])["EarningsPerShareDiluted"]
    m = {e.field: e for e in USStockProvider._facts_to_evidence(facts, "http://x")}
    assert "eps_ttm" in m and m["eps_ttm"].value == 2.6
    assert m["eps_ttm"].unit == "USD/shares"


# ---------- W2 P/E cross-check ----------

def _us_store(trailing_pe: float, eps_ttm: float = 2.0, price: float = 20.0) -> EvidenceStore:
    store = EvidenceStore(ticker="TEST", market="US")
    store.add_all([
        Evidence(category="quote", field="last_price", value=price, unit="USD", source="yf"),
        Evidence(category="quote", field="trailing_pe", value=trailing_pe, source="yfinance info (derived)"),
        Evidence(category="fundamentals", field="eps_ttm", value=eps_ttm,
                 unit="USD/shares", source="SEC", as_of=date.today()),
    ])
    return store


def test_pe_crosscheck_passes_when_aligned():
    # price 20 / eps_ttm 2 = implied P/E 10；yfinance 11 → 偏離 ~9% < 10%，無 finding
    findings = deterministic_checks(_us_store(trailing_pe=11.0), AuditConfig())
    assert not [f for f in findings if f.check == "cross_source" and "P/E" in f.message]


def test_pe_crosscheck_errors_on_large_divergence():
    # implied 10 vs yfinance 30 → 偏離 ~67% > 10% → error
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
    # 虧損公司 eps_ttm < 0 → P/E 無意義，須早退不發 finding（即便數值上偏離很大）
    assert not _has_pe_finding(_us_store(trailing_pe=30.0, eps_ttm=-2.0))


def test_pe_crosscheck_skips_nonpositive_yf_pe():
    # yfinance trailing_pe ≤ 0（虧損股 yfinance 常給 0/None）→ 不做比對
    assert not _has_pe_finding(_us_store(trailing_pe=0.0))


def test_pe_crosscheck_skips_when_eps_missing():
    store = EvidenceStore(ticker="TEST", market="US")
    store.add_all([
        Evidence(category="quote", field="last_price", value=20.0, unit="USD", source="yf"),
        Evidence(category="quote", field="trailing_pe", value=30.0, source="yfinance info (derived)"),
        # 無 eps_ttm → 不做比對（不退回年報，避免錯口徑誤報）
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


def test_short_interest_skips_mom_when_prior_zero():
    # sharesShortPriorMonth=0 → truthy 守衛跳過 MoM，避免 div/0 污染 evidence
    info = {"sharesShort": 100, "sharesShortPriorMonth": 0}
    m = {e.field for e in USStockProvider._short_interest_evidence(info, "u")}
    assert "short_interest_change_mom_pct" not in m
    assert "shares_short" in m
