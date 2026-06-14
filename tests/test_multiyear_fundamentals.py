"""Spec E1 PR2 — 多年期基本面欄位：共享純計算 + US/TW 逐年序列抽取。

不打網路：純函式 + 合成 companyfacts / FinMind rows。
"""

from __future__ import annotations

from datetime import date

from cyber_sages.data.longterm import multiyear_fundamentals
from cyber_sages.data.tw_stocks import TWStockProvider
from cyber_sages.data.us_stocks import USStockProvider


def _series(values: list[float], start_year: int = 2020):
    return [(date(start_year + i, 12, 31), v) for i, v in enumerate(values)]


# ---------- 共享純計算 ----------


def test_multiyear_metrics_arithmetic():
    evs = {e.field: e for e in multiyear_fundamentals(
        roe_pct_series=_series([20, 21, 22, 23, 24]),
        gross_margin_pct_series=_series([40, 41, 42, 43, 44]),
        net_income_series=_series([200, 210, 220, 230, 240]),
        source="test", url=None)}
    assert evs["roe_5y_avg"].value == 22.0 and evs["roe_5y_avg"].unit == "%"
    assert evs["gross_margin_trend_5y"].value == 1.0  # 完美線性 +1%點/年
    assert evs["gross_margin_trend_5y"].unit == "%/yr"
    # 穩定度 = 1 − σ/μ；σ(200..240)=14.14, μ=220 → ≈0.936
    assert evs["earnings_stability_5y"].value == 0.936
    assert evs["roe_5y_avg"].as_of == date(2024, 12, 31)


def test_declining_margin_has_negative_trend():
    [gm] = [e for e in multiyear_fundamentals(
        roe_pct_series=[], gross_margin_pct_series=_series([50, 48, 46, 44]),
        net_income_series=[], source="t", url=None) if e.field == "gross_margin_trend_5y"]
    assert gm.value == -2.0  # 每年 −2%點


def test_earnings_stability_zero_when_mean_not_positive():
    [st] = [e for e in multiyear_fundamentals(
        roe_pct_series=[], gross_margin_pct_series=[],
        net_income_series=_series([100, -200, 50]), source="t", url=None)
        if e.field == "earnings_stability_5y"]
    assert st.value == 0.0  # μ≤0 視為不穩


def test_below_min_years_emits_nothing():
    assert multiyear_fundamentals(
        roe_pct_series=_series([20, 21]),  # 只有 2 年 < MIN_YEARS(3)
        gross_margin_pct_series=_series([40, 41]),
        net_income_series=_series([200, 210]), source="t", url=None) == []


def test_exactly_min_years_emits():
    # MIN_YEARS=3 邊界：恰 3 年就發（C6）
    evs = {e.field for e in multiyear_fundamentals(
        roe_pct_series=_series([20, 22, 24]), gross_margin_pct_series=_series([40, 41, 42]),
        net_income_series=_series([200, 210, 220]), source="t", url=None)}
    assert evs == {"roe_5y_avg", "gross_margin_trend_5y", "earnings_stability_5y"}


def test_non_finite_year_is_dropped(monkeypatch):
    # C2：某年 inf/nan 不得污染指標——壞年當缺年丟棄，剩 2 年 < MIN_YEARS → 不發
    assert multiyear_fundamentals(
        roe_pct_series=_series([20, float("inf"), 24]),
        gross_margin_pct_series=_series([40, float("nan"), 44]),
        net_income_series=_series([200, float("inf"), 240]), source="t", url=None) == []
    # 壞年丟掉後仍有 3 個有限年 → 正常發，且不含壞值
    [roe] = [e for e in multiyear_fundamentals(
        roe_pct_series=_series([20, 22, float("inf"), 24, 26]),
        gross_margin_pct_series=[], net_income_series=[], source="t", url=None)
        if e.field == "roe_5y_avg"]
    assert roe.value == 23.0  # mean(20,22,24,26)，inf 已剔除


def test_takes_only_most_recent_five_years():
    # 給 7 年，roe_5y_avg 只用最近 5 年（2018,2019 被丟）
    [roe] = [e for e in multiyear_fundamentals(
        roe_pct_series=_series([1, 1, 10, 20, 30, 40, 50], start_year=2016),
        gross_margin_pct_series=[], net_income_series=[], source="t", url=None)
        if e.field == "roe_5y_avg"]
    assert roe.value == 30.0 and roe.as_of == date(2022, 12, 31)  # mean(10,20,30,40,50)


# ---------- US：companyfacts 多年抽取 ----------


def _annual(val, end):  # 全年 10-K 流量
    return {"form": "10-K", "start": f"{end[:4]}-01-01", "end": end, "val": val}


def _instant(val, end):  # 資產負債時點 10-K
    return {"form": "10-K", "end": end, "val": val}


def _multi_facts() -> dict:
    years = [2020, 2021, 2022, 2023, 2024]
    ni = [200, 210, 220, 230, 240]
    gp = [400, 410, 420, 430, 440]
    g = {
        "NetIncomeLoss": {"units": {"USD": [_annual(v, f"{y}-12-31") for y, v in zip(years, ni)]}},
        "StockholdersEquity": {"units": {"USD": [_instant(1000, f"{y}-12-31") for y in years]}},
        "GrossProfit": {"units": {"USD": [_annual(v, f"{y}-12-31") for y, v in zip(years, gp)]}},
        "Revenues": {"units": {"USD": [_annual(1000, f"{y}-12-31") for y in years]}},
    }
    return {"facts": {"us-gaap": g}}


def test_us_multiyear_from_companyfacts():
    evs = {e.field: e for e in USStockProvider._multiyear_fundamentals(
        _multi_facts()["facts"]["us-gaap"], "http://x")}
    assert evs["roe_5y_avg"].value == 22.0  # ni/1000*100 → [20..24] avg 22
    assert evs["gross_margin_trend_5y"].value == 1.0
    assert "SEC EDGAR" in evs["roe_5y_avg"].source


def test_us_annual_map_dedup_and_duration_filter():
    g = {"NetIncomeLoss": {"units": {"USD": [
        _annual(200, "2023-12-31"),
        _annual(210, "2024-12-31"),
        {"form": "10-K", "start": "2024-10-01", "end": "2024-12-31", "val": 55},  # 季 duration 排除
        {"form": "10-Q", "start": "2024-01-01", "end": "2024-03-31", "val": 99},  # 非 10-K 排除
    ]}}}
    m = USStockProvider._annual_map(g, ["NetIncomeLoss"], "USD")
    assert m == {"2023-12-31": 200.0, "2024-12-31": 210.0}  # 季值與 10-Q 都被濾掉


def test_us_annual_map_same_end_date_revision_overrides(monkeypatch):
    # C6：同一 fiscal-year-end 多筆（修訂）→ 後寫覆蓋（取較新申報的最終值）
    g = {"NetIncomeLoss": {"units": {"USD": [
        _annual(200, "2024-12-31"),   # 初報
        _annual(215, "2024-12-31"),   # 修訂 → 覆蓋
    ]}}}
    assert USStockProvider._annual_map(g, ["NetIncomeLoss"], "USD") == {"2024-12-31": 215.0}


# ---------- TW：FinMind 季度聚合成年度 ----------


def _q(fm_type, d, val):
    return {"type": fm_type, "date": d, "value": val}


def _tw_income():
    rows = []
    for y, ni, gp, rev in [(2022, 100, 400, 1000), (2023, 110, 420, 1000), (2024, 120, 440, 1000)]:
        for q_end in (f"{y}-03-31", f"{y}-06-30", f"{y}-09-30", f"{y}-12-31"):
            rows += [_q("IncomeAfterTaxes", q_end, ni / 4),
                     _q("GrossProfit", q_end, gp / 4), _q("Revenue", q_end, rev / 4)]
    return rows


def _tw_balance():
    return [_q("Equity", f"{y}-12-31", 1000) for y in (2022, 2023, 2024)]


def test_tw_annual_sums_requires_four_quarters():
    rows = _tw_income() + [_q("IncomeAfterTaxes", "2025-03-31", 30)]  # 2025 只有 1 季
    sums = TWStockProvider._annual_sums(rows, "IncomeAfterTaxes")
    assert sums == {2022: 100.0, 2023: 110.0, 2024: 120.0}  # 2025 不足四季被排除


def test_tw_multiyear_from_finmind():
    evs = {e.field: e for e in TWStockProvider._multiyear_fundamentals(
        _tw_income(), _tw_balance(), "http://x")}
    # roe = 年度淨利 / 年底權益 ×100 → [10, 11, 12]，avg=11
    assert evs["roe_5y_avg"].value == 11.0
    # 毛利率 [40, 42, 44] → slope +2%點/年
    assert evs["gross_margin_trend_5y"].value == 2.0
    assert "FinMind" in evs["roe_5y_avg"].source
