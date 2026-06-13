"""分析師前瞻共識（estimate 類別）的純單元測試——以假的 yfinance Ticker 取代網路。

驗證欄位解析、缺值略過、symbol 依序 fallback、estimate 類別與 (estimate) 來源標記，
以及 US/TW provider 確實滿足 EstimateProvider 協議。
"""

import pytest

from cyber_sages.data.base import EstimateProvider
from cyber_sages.data.estimates import fetch_estimates
from cyber_sages.data.tw_stocks import TWStockProvider
from cyber_sages.data.us_stocks import USStockProvider

FULL_INFO = {
    "forwardEps": 9.59499,
    "targetMeanPrice": 312.716,
    "earningsGrowth": 0.218,
    "revenueGrowth": 0.166,
    "numberOfAnalystOpinions": 43,
    "recommendationKey": "buy",
}


class _FakeTicker:
    """以 symbol→info 對照表假裝 yfinance.Ticker。"""
    table: dict[str, dict] = {}

    def __init__(self, symbol):
        self.symbol = symbol

    @property
    def info(self):
        return _FakeTicker.table.get(self.symbol, {})


@pytest.fixture
def fake_yf(monkeypatch):
    def _set(table):
        _FakeTicker.table = table
        monkeypatch.setattr("yfinance.Ticker", _FakeTicker)
    return _set


def test_fetch_estimates_parses_all_fields(fake_yf):
    fake_yf({"AAPL": FULL_INFO})
    evs = fetch_estimates(["AAPL"], currency="USD", url="u")
    m = {e.field: e for e in evs}
    assert m["forward_eps"].value == 9.595
    assert m["forward_eps"].unit == "USD/share"
    assert m["target_mean_price"].value == 312.72
    assert m["earnings_growth_est_pct"].value == 21.8     # 0.218 × 100
    assert m["revenue_growth_est_pct"].value == 16.6
    assert m["analyst_count"].value == 43
    assert m["analyst_recommendation"].value == "buy"
    # 全部歸 estimate 類別、來源標明 (estimate)
    assert all(e.category == "estimate" for e in evs)
    assert all("estimate" in e.source for e in evs)


def test_fetch_estimates_skips_missing_fields(fake_yf):
    fake_yf({"AAPL": {"forwardEps": 5.0, "numberOfAnalystOpinions": 10}})
    m = {e.field for e in fetch_estimates(["AAPL"], currency="USD", url="u")}
    assert m == {"forward_eps", "analyst_count"}


def test_fetch_estimates_falls_back_to_eps_forward(fake_yf):
    # forwardEps 缺、epsForward 在 → 仍取得 forward_eps
    fake_yf({"X": {"epsForward": 7.0, "numberOfAnalystOpinions": 3}})
    m = {e.field: e.value for e in fetch_estimates(["X"], currency="USD", url="u")}
    assert m["forward_eps"] == 7.0


def test_fetch_estimates_symbol_fallback(fake_yf):
    # 第一個 symbol 無共識資料 → 試第二個
    fake_yf({"2330.TW": {}, "2330.TWO": FULL_INFO})
    m = {e.field for e in fetch_estimates(["2330.TW", "2330.TWO"], currency="TWD", url="u")}
    assert "forward_eps" in m and "analyst_count" in m


def test_fetch_estimates_empty_when_no_data(fake_yf):
    fake_yf({})  # 任何 symbol 都回空 info
    assert fetch_estimates(["NOPE"], currency="USD", url="u") == []


def test_providers_satisfy_estimate_protocol():
    assert isinstance(USStockProvider(), EstimateProvider)
    assert isinstance(TWStockProvider(), EstimateProvider)


async def test_tw_etf_estimates_short_circuit(monkeypatch):
    # ETF 無個股共識：get_estimates early return []，不碰 yfinance
    called = False

    def boom(*a, **k):
        nonlocal called
        called = True
        raise AssertionError("should not fetch")

    monkeypatch.setattr("cyber_sages.data.tw_stocks.fetch_estimates", boom)
    assert await TWStockProvider().get_estimates("0050") == []
    assert not called
