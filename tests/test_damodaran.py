"""Damodaran 產業 multiples（reference 類別）的純單元測試——讀 vendored CSV，不打網路。"""

import math

from cyber_sages.data.damodaran import (
    industry_benchmark_evidence,
    map_industry,
    _industries,
)


def test_no_nan_values_emitted():
    # 快照含 nan 儲存格，loader 須跳過——任何發出的 reference 值都應為有限數
    for ind in ["Semiconductors", "Consumer Electronics", "Banks—Diversified", None]:
        for e in industry_benchmark_evidence(ind, "u"):
            assert isinstance(e.value, (int, float)) and not math.isnan(float(e.value))


def test_vendored_csv_loads():
    table = _industries()
    assert table  # 非空
    assert "Semiconductor" in table
    assert "Total Market" in table
    assert table["Semiconductor"]["trailing_pe"] > 0


def test_map_industry_known_and_unknown():
    assert map_industry("Semiconductors") == "Semiconductor"
    assert map_industry("SEMICONDUCTORS") == "Semiconductor"     # 大小寫無關
    assert map_industry("Drug Manufacturers—General") == "Drugs (Pharmaceutical)"
    assert map_industry("Totally Made Up Industry") is None
    assert map_industry(None) is None


def test_benchmark_evidence_known_industry():
    evs = industry_benchmark_evidence("Semiconductors", "u")
    m = {e.field: e for e in evs}
    # 產業欄位 + 全市場 baseline 都在
    assert "industry_pe_trailing" in m
    assert "industry_pe_forward" in m
    assert "industry_peg" in m
    assert "market_pe_trailing" in m
    assert all(e.category == "reference" for e in evs)
    assert all("Damodaran" in e.source for e in evs)
    assert "Semiconductor" in m["industry_pe_trailing"].note


def test_benchmark_evidence_unknown_industry_still_gives_market_baseline():
    # 產業對映不到 → 不發產業欄位，但仍發全市場 baseline（US 標的至少有對照）
    m = {e.field for e in industry_benchmark_evidence("Totally Made Up", "u")}
    assert m == {"market_pe_trailing"}


def test_benchmark_evidence_none_industry():
    m = {e.field for e in industry_benchmark_evidence(None, "u")}
    assert m == {"market_pe_trailing"}  # 無 industry 仍給市場基準


def test_benchmark_emits_growth_and_units():
    # I-2：5y growth 要 expose；n-1：P/E/PEG 帶 unit="x"、growth 帶 "%"
    m = {e.field: e for e in industry_benchmark_evidence("Semiconductors", "u")}
    assert "industry_growth_5y_pct" in m
    assert m["industry_pe_trailing"].unit == "x"
    assert m["industry_peg"].unit == "x"
    assert m["industry_growth_5y_pct"].unit == "%"
    assert m["market_pe_trailing"].unit == "x"


def test_benchmark_skips_nan_fields_for_consumer_electronics():
    # Electronics (Consumer & Office) 的 trailing_pe / peg 在快照為 nan，只有 forward_pe 有值
    fields = {e.field for e in industry_benchmark_evidence("Consumer Electronics", "u")}
    assert "industry_pe_trailing" not in fields   # nan → 跳過
    assert "industry_peg" not in fields           # nan → 跳過
    assert "industry_pe_forward" in fields        # 有值
    assert "market_pe_trailing" in fields
