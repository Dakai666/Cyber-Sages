"""C3（issue #65）：TW 現金流量表 YTD 去累計化 → 單季 / TTM / FCF / 利息保障。

FinMind TaiwanStockCashFlowsStatement 是 YTD 累計值，與單季損益混算會跨期出錯。
本測試以合成 fixture 驗確定性去累計化與衍生欄位（純函式、不打網路）。
"""

from datetime import date

from cyber_sages.data.tw_stocks import TWStockProvider as P


def _row(fm_type: str, d: str, value: float) -> dict:
    return {"type": fm_type, "date": d, "value": value}


def _ocf_year_2025() -> list[dict]:
    # YTD 累計：Q1=100, H1=250, 9M=420, FY=600 → 單季 100/150/170/180（加總=600=FY）
    t = P._CF_OCF
    return [_row(t, "2025-03-31", 100), _row(t, "2025-06-30", 250),
            _row(t, "2025-09-30", 420), _row(t, "2025-12-31", 600)]


def test_decumulate_ytd_to_single_quarter():
    singles = P._decumulate(_ocf_year_2025(), P._CF_OCF)
    assert singles == [
        (date(2025, 3, 31), 100), (date(2025, 6, 30), 150),
        (date(2025, 9, 30), 170), (date(2025, 12, 31), 180),
    ]
    # 還原後四季加總 == FY YTD（去累計化正確性的不變式）
    assert sum(v for _, v in singles) == 600


def test_decumulate_q1_resets_across_year_boundary():
    # 隔年 Q1（3 月）是新年度 YTD 起點＝單季本身，不可減去前一年 FY 累計
    rows = [_row(P._CF_OCF, "2024-12-31", 500), _row(P._CF_OCF, "2025-03-31", 100)]
    singles = P._decumulate(rows, P._CF_OCF)
    assert (date(2025, 3, 31), 100) in singles  # 100，而非 100-500


def test_decumulate_skips_quarter_missing_prior():
    # 缺同年上一季（無 06-30）→ 09-30 無法還原 → 跳過（寧缺勿錯）
    rows = [_row(P._CF_OCF, "2025-03-31", 100), _row(P._CF_OCF, "2025-09-30", 420)]
    singles = P._decumulate(rows, P._CF_OCF)
    assert singles == [(date(2025, 3, 31), 100)]


def test_cf_ttm_needs_four_quarters():
    short = [_row(P._CF_OCF, "2025-03-31", 100), _row(P._CF_OCF, "2025-06-30", 250)]
    assert P._cf_ttm(short, P._CF_OCF) is None       # 不足四季不硬湊
    total, last4 = P._cf_ttm(_ocf_year_2025(), P._CF_OCF)
    assert total == 600 and len(last4) == 4


def _full_cashflow() -> list[dict]:
    rows = _ocf_year_2025()
    # capex（FinMind 負值＝流出）：YTD -40/-90/-150/-220 → 單季 -40/-50/-60/-70，TTM=-220
    cx = P._CF_CAPEX
    rows += [_row(cx, "2025-03-31", -40), _row(cx, "2025-06-30", -90),
             _row(cx, "2025-09-30", -150), _row(cx, "2025-12-31", -220)]
    # 折舊 YTD 10/20/30/40 → 單季 10 each → TTM=40
    dp = P._CF_DEP
    rows += [_row(dp, "2025-03-31", 10), _row(dp, "2025-06-30", 20),
             _row(dp, "2025-09-30", 30), _row(dp, "2025-12-31", 40)]
    # 利息費用 YTD 2/4/6/8 → 單季 2 each → TTM=8
    it = P._CF_INTEREST
    rows += [_row(it, "2025-03-31", 2), _row(it, "2025-06-30", 4),
             _row(it, "2025-09-30", 6), _row(it, "2025-12-31", 8)]
    return rows


def _income_op() -> list[dict]:
    # 損益表為單季值（非累計）：營業利益每季 200 → TTM=800
    return [_row("OperatingIncome", d, 200) for d in
            ("2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31")]


def test_cashflow_evidence_fields_and_signs():
    evs = {e.field: e for e in P._cashflow_evidence(_full_cashflow(), _income_op(), "url")}

    assert evs["operating_cash_flow_latest_quarter"].value == 180   # 最新單季
    assert evs["operating_cash_flow_ttm"].value == 600
    # capex 對齊美股正值慣例（FinMind 負值取絕對值）
    assert evs["capex_latest_quarter"].value == 70
    assert evs["capex_ttm"].value == 220
    # FCF = OCF − capex（capex TTM 內部為負，OCF + 負值 = 600 − 220）
    assert evs["free_cash_flow_ttm"].value == 380
    assert evs["depreciation_amortization_ttm"].value == 40
    assert evs["interest_expense_ttm"].value == 8           # 絕對值
    # 利息保障 = 營業利益 TTM 800 / 利息 TTM 8 = 100
    assert evs["interest_coverage"].value == 100
    assert evs["interest_coverage"].unit == "x"
    # 全部歸 fundamentals 類別、來源標明去累計化（可溯源）
    assert all(e.category == "fundamentals" for e in evs.values())
    assert "去累計化" in evs["free_cash_flow_ttm"].source


def test_cashflow_evidence_empty_on_missing_data():
    assert P._cashflow_evidence([], _income_op(), "url") == []
    assert P._cashflow_evidence(RuntimeError("fetch failed"), _income_op(), "url") == []


def test_cashflow_fcf_absent_without_full_ttm():
    # 只有 2 季 OCF、無 capex → 無 TTM → 不發 FCF（寧缺勿錯）
    rows = [_row(P._CF_OCF, "2025-03-31", 100), _row(P._CF_OCF, "2025-06-30", 250)]
    fields = {e.field for e in P._cashflow_evidence(rows, _income_op(), "url")}
    assert "free_cash_flow_ttm" not in fields
    assert "operating_cash_flow_latest_quarter" in fields  # 單季仍可發
