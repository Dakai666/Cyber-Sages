"""共用測試設施：TW provider 的 mock fixture（動態日期，新鮮度檢查永不漂移）。"""

from datetime import date, timedelta

import pytest

from cyber_sages.data.evidence import Evidence
from cyber_sages.data.tw_stocks import TWStockProvider

YAHOO = "https://tw.stock.yahoo.com/quote/2330.TW"
MOPS = "https://mops.twse.com.tw/mops/web/t146sb05?stock_id=2330"


def _d(days: int) -> date:
    return date.today() - timedelta(days=days)


def _quote_and_profile(name: str, sector: str, close: float, live: float) -> list[Evidence]:
    return [
        Evidence(category="quote", field="latest_close", value=close, unit="TWD",
                 source="FinMind TaiwanStockPrice", url=YAHOO, as_of=_d(1)),
        Evidence(category="quote", field="latest_volume", value=21_000_000, unit="shares",
                 source="FinMind TaiwanStockPrice", url=YAHOO, as_of=_d(1)),
        # 跨源第二路徑：與 latest_close 偏離 < 2%，audit 跨源一致性必須通過
        Evidence(category="quote", field="last_price", value=live, unit="TWD",
                 source="yfinance .TW fast_info", url=YAHOO, as_of=_d(0)),
        Evidence(category="profile", field="company_name", value=name,
                 source="FinMind TaiwanStockInfo", url=YAHOO),
        Evidence(category="profile", field="sector", value=sector,
                 source="FinMind TaiwanStockInfo", url=YAHOO),
    ]


def _history() -> list[Evidence]:
    src = "computed from FinMind 1y daily closes"
    url = f"{YAHOO}/technical-analysis"
    fields = [("sma_20", 1062.5), ("sma_50", 1031.0), ("sma_200", 980.4),
              ("rsi_14", 58.3), ("volatility_30d_annualized_pct", 27.6)]
    return [Evidence(category="history", field=f, value=v,
                     unit="TWD" if f.startswith("sma") else None,
                     source=src, url=url, as_of=_d(1))
            for f, v in fields]


def _fundamentals_2330() -> list[Evidence]:
    q = _d(40)  # 最近一季財報：< max_fundamentals_age_days(120)
    src = "FinMind TaiwanStockFinancialStatements"
    return [
        Evidence(category="fundamentals", field="revenue_latest_quarter",
                 value=839_250_000_000, unit="TWD", source=src, url=MOPS, as_of=q,
                 note="季別（Revenue）"),
        Evidence(category="fundamentals", field="net_income_latest_quarter",
                 value=374_680_000_000, unit="TWD", source=src, url=MOPS, as_of=q,
                 note="季別（IncomeAfterTaxes）"),
        Evidence(category="fundamentals", field="eps_latest_quarter",
                 value=14.45, unit="TWD/share", source=src, url=MOPS, as_of=q,
                 note="季別（EPS）"),
        Evidence(category="fundamentals", field="eps_ttm", value=53.6, unit="TWD/share",
                 source=f"computed from {src}", url=MOPS, as_of=q,
                 note="近四季合計 TTM；估值用 P/E 應以此為分母，勿用單季×4"),
        Evidence(category="fundamentals", field="revenue_ttm",
                 value=3_141_000_000_000, unit="TWD",
                 source=f"computed from {src}", url=MOPS, as_of=q, note="近四季合計 TTM"),
        Evidence(category="fundamentals", field="total_assets",
                 value=6_690_000_000_000, unit="TWD",
                 source="FinMind TaiwanStockBalanceSheet", url=MOPS, as_of=q),
        Evidence(category="fundamentals", field="monthly_revenue",
                 value=285_960_000_000, unit="TWD",
                 source="FinMind TaiwanStockMonthRevenue", url=MOPS, as_of=_d(15),
                 note="上月營收"),
        Evidence(category="fundamentals", field="revenue_yoy_pct", value=33.1, unit="%",
                 source="computed from FinMind TaiwanStockMonthRevenue",
                 url=MOPS, as_of=_d(15)),
    ]


def _chips() -> list[Evidence]:
    src_inst = "FinMind TaiwanStockInstitutionalInvestorsBuySell"
    src_margin = "FinMind TaiwanStockMarginPurchaseShortSale"
    return [
        Evidence(category="chips", field="foreign_net_buy", value=-4_904_284,
                 unit="shares", source=src_inst, url=YAHOO, as_of=_d(1),
                 note="外資單日買賣超（正=買超）"),
        Evidence(category="chips", field="trust_net_buy", value=1_200_000,
                 unit="shares", source=src_inst, url=YAHOO, as_of=_d(1),
                 note="投信單日買賣超（正=買超）"),
        Evidence(category="chips", field="dealer_net_buy", value=-300_000,
                 unit="shares", source=src_inst, url=YAHOO, as_of=_d(1),
                 note="自營商單日買賣超（正=買超）"),
        Evidence(category="chips", field="institutional_net_buy_total", value=-4_004_284,
                 unit="shares", source=src_inst, url=YAHOO, as_of=_d(1),
                 note="三大法人合計單日買賣超（正=買超）"),
        Evidence(category="chips", field="margin_balance", value=23_500, unit="lots",
                 source=src_margin, url=YAHOO, as_of=_d(1), note="融資餘額（張）"),
        Evidence(category="chips", field="short_balance", value=1_870, unit="lots",
                 source=src_margin, url=YAHOO, as_of=_d(1), note="融券餘額（張）"),
    ]


def _news() -> list[Evidence]:
    return [Evidence(category="news", field="headline_1",
                     value="台積電法說會釋出樂觀展望，先進製程需求強勁",
                     source="FinMind TaiwanStockNews (經濟日報)", url=YAHOO, as_of=_d(2))]


def make_macro_evidence() -> list[Evidence]:
    fields = [("fed_funds_rate", 4.25, "聯邦資金利率（月）"),
              ("treasury_10y", 4.40, "10年期公債殖利率"),
              ("yield_curve_10y_2y", 0.35, "10年減2年利差"),
              ("cpi_yoy_pct", 2.6, "CPI 年增率（通膨）")]
    return [Evidence(category="macro", field=f, value=v, unit="%",
                     source="FRED TEST", url="https://fred.stlouisfed.org/series/TEST",
                     as_of=_d(20), note=note)
            for f, v, note in fields]


@pytest.fixture
def patch_tw_provider(monkeypatch):
    """回傳 patch(etf=...)：把 TWStockProvider 五路換成動態日期 fixture 資料。"""

    def _patch(*, etf: bool) -> None:
        if etf:
            quote = _quote_and_profile("元大台灣50 (0050)", "ETF", close=205.4, live=206.1)
            fundamentals: list[Evidence] = []  # 同真實 provider：ETF 無個股財報，early return
        else:
            quote = _quote_and_profile("台積電 (2330)", "半導體業", close=1075.0, live=1080.0)
            fundamentals = _fundamentals_2330()

        async def _ret(items):
            return items

        monkeypatch.setattr(TWStockProvider, "get_quote", lambda self, t: _ret(quote))
        monkeypatch.setattr(TWStockProvider, "get_history", lambda self, t: _ret(_history()))
        monkeypatch.setattr(TWStockProvider, "get_fundamentals",
                            lambda self, t: _ret(fundamentals))
        monkeypatch.setattr(TWStockProvider, "get_news", lambda self, t: _ret(_news()))
        monkeypatch.setattr(TWStockProvider, "get_chips", lambda self, t: _ret(_chips()))

    return _patch
