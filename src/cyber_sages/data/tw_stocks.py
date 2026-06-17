"""台股資料源：FinMind（第一手）為主 + yfinance `.TW` 報價當跨源比對的第二條路徑。

口徑與美股對齊：技術指標仍在程式裡確定性計算；財報用 FinMind 綜合損益表/資產
負債表/月營收；台股特有的籌碼面（三大法人買賣超、融資融券）走獨立的 chips 類別。

FinMind 已驗證的欄位（2330 實測）：
- TaiwanStockPrice: date, close, Trading_Volume …
- TaiwanStockFinancialStatements: type ∈ {Revenue, GrossProfit, OperatingIncome,
  IncomeAfterTaxes, EPS, PreTaxIncome, CostOfGoodsSold …}, value 為元
- TaiwanStockBalanceSheet: type ∈ {TotalAssets, Liabilities, Equity …}
- TaiwanStockMonthRevenue: revenue, revenue_month, revenue_year
- TaiwanStockInstitutionalInvestorsBuySell: name ∈ {Foreign_Investor,
  Foreign_Dealer_Self, Investment_Trust, Dealer_self, Dealer_Hedging}, buy, sell
- TaiwanStockMarginPurchaseShortSale: MarginPurchaseTodayBalance, ShortSaleTodayBalance
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date

import httpx

from cyber_sages.data.base import is_tw_etf
from cyber_sages.data.estimates import fetch_estimates
from cyber_sages.data.evidence import Evidence
from cyber_sages.data.finmind import days_ago, finmind_get
from cyber_sages.data.indicators import compute_indicator_evidence, short_history_evidence
from cyber_sages.data.longterm import multiyear_fundamentals
from cyber_sages.data.retry import to_thread_with_timeout

logger = logging.getLogger(__name__)


def to_stock_id(ticker: str) -> str:
    """2330 / 2330.TW / 2330.TWO → 2330。"""
    return ticker.upper().split(".")[0]


# 綜合損益表是「單季」值；估值要用 TTM（近四季合計），單季 EPS×4 會放大成數倍的
# 本益比誤判，故確定性算出 TTM。模組常數：不需要實例狀態，測試可直接 import。
TTM_FIELDS: dict[str, tuple[str, str]] = {
    "EPS": ("eps_ttm", "TWD/share"),
    "Revenue": ("revenue_ttm", "TWD"),
    "IncomeAfterTaxes": ("net_income_ttm", "TWD"),  # ROE 用 TTM 淨利，單季會低估 4x
}


def _twse_url(stock_id: str) -> str:
    return f"https://tw.stock.yahoo.com/quote/{stock_id}.TW"


# 綜合損益表 type → (我們的欄位名, 單位)
INCOME_FIELDS: dict[str, tuple[str, str]] = {
    "Revenue": ("revenue_latest_quarter", "TWD"),
    "GrossProfit": ("gross_profit_latest_quarter", "TWD"),
    "OperatingIncome": ("operating_income_latest_quarter", "TWD"),
    "IncomeAfterTaxes": ("net_income_latest_quarter", "TWD"),
    "PreTaxIncome": ("pretax_income_latest_quarter", "TWD"),
    "EPS": ("eps_latest_quarter", "TWD/share"),
}
BALANCE_FIELDS: dict[str, tuple[str, str]] = {
    "TotalAssets": ("total_assets", "TWD"),
    "Liabilities": ("total_liabilities", "TWD"),
    "Equity": ("equity", "TWD"),
    "CurrentAssets": ("current_assets", "TWD"),
    "CurrentLiabilities": ("current_liabilities", "TWD"),
}

# FinMind 法人別 name → 三大法人口徑。模組常數（同 TTM_FIELDS 慣例：無實例狀態、
# 測試可直接 import）。目前累計趨勢只做 foreign 與三法人合計（issue #4 範圍）；
# trust_net_buy_5d / dealer_net_buy_5d 留待 Spec E 若有「法人分歧」skill 再補
# （合計會抵消投信買、外資賣的分歧訊號）。
INST_GROUPS: dict[str, list[str]] = {
    "foreign_net_buy": ["Foreign_Investor", "Foreign_Dealer_Self"],
    "trust_net_buy": ["Investment_Trust"],
    "dealer_net_buy": ["Dealer_self", "Dealer_Hedging"],
}


class TWStockProvider:
    market = "TW"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def _fm(self, dataset: str, stock_id: str, start_date: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=40) as client:
            return await finmind_get(client, dataset, stock_id, start_date=start_date)

    # ---------- quote（FinMind 收盤為主 + yfinance 即時為跨源第二路徑）----------

    async def get_quote(self, ticker: str) -> list[Evidence]:
        stock_id = to_stock_id(ticker)
        url = _twse_url(stock_id)
        evs: list[Evidence] = []

        rows = await self._fm("TaiwanStockPrice", stock_id, days_ago(14))
        # 剔除 0 量（未交易/未結算）尾 bar，取最新一筆真實成交日——與美股 drop_phantom_bars
        # 同理（見 SPCX 案例）：寧用稍舊但真實的收盤，不用最新但虛構的佔位值。
        rows = [r for r in rows if int(r.get("Trading_Volume") or 0) > 0]
        if rows:
            last = rows[-1]
            as_of = date.fromisoformat(last["date"])
            evs.append(Evidence(
                category="quote", field="latest_close", value=round(float(last["close"]), 2),
                unit="TWD", source="FinMind TaiwanStockPrice", url=url, as_of=as_of,
            ))
            evs.append(Evidence(
                category="quote", field="latest_volume", value=int(last["Trading_Volume"]),
                unit="shares", source="FinMind TaiwanStockPrice", url=url, as_of=as_of,
            ))

        # 第二條路徑：yfinance .TW fast_info，供 audit 跨源比對（best-effort）
        live = await to_thread_with_timeout(
            lambda: self._yf_last_price(stock_id), what=f"yfinance .TW price {stock_id}")
        if live is not None:
            evs.append(Evidence(
                category="quote", field="last_price", value=round(live, 2),
                unit="TWD", source="yfinance .TW fast_info",
                url=f"https://finance.yahoo.com/quote/{stock_id}.TW", as_of=date.today(),
            ))

        # 公司基本資料（名稱/產業）
        try:
            info = await self._fm("TaiwanStockInfo", stock_id, days_ago(3650))
            if info:
                row = info[-1]
                name = row.get("stock_name")
                industry = row.get("industry_category")
                if name:
                    evs.append(Evidence(
                        category="profile", field="company_name",
                        value=f"{name} ({stock_id})", source="FinMind TaiwanStockInfo", url=url,
                    ))
                if industry:
                    evs.append(Evidence(
                        category="profile", field="sector", value=industry,
                        source="FinMind TaiwanStockInfo", url=url,
                    ))
        except Exception as e:
            # profile 是非核心類別；失敗只少了公司名/產業，不該悶掉——記 log 供追蹤
            logger.warning("TW profile (TaiwanStockInfo) fetch failed for %s: %s", stock_id, e)
        return evs

    @staticmethod
    def _yf_last_price(stock_id: str) -> float | None:
        try:
            import yfinance as yf
            for suffix in (".TW", ".TWO"):
                fast = yf.Ticker(stock_id + suffix).fast_info
                last = getattr(fast, "last_price", None)
                if last:
                    return float(last)
        except Exception as e:
            # 第二條跨源比對路徑失敗不致命（FinMind 收盤仍在），但要記 log
            logger.warning("TW yfinance cross-source price failed for %s: %s", stock_id, e)
            return None
        return None

    # ---------- history + 確定性技術指標 ----------

    async def get_history(self, ticker: str) -> list[Evidence]:
        stock_id = to_stock_id(ticker)
        rows = await self._fm("TaiwanStockPrice", stock_id, days_ago(400))
        # 需有收盤且為真實成交日（0 量佔位 bar 會污染指標與 as_of，同 get_quote 處理）
        rows = [r for r in rows if r.get("close") and int(r.get("Trading_Volume") or 0) > 0]
        if len(rows) < 30:
            # S5：< 30 交易日 → IPO/新上市標記（audit 降 warning + 明說），不硬出指標
            return short_history_evidence(
                len(rows), url=f"https://tw.stock.yahoo.com/quote/{stock_id}.TW",
                source="FinMind daily (insufficient bars)")
        import pandas as pd

        close = pd.Series(
            [float(r["close"]) for r in rows],
            index=pd.to_datetime([r["date"] for r in rows]),
        )
        return compute_indicator_evidence(
            close, as_of=date.fromisoformat(rows[-1]["date"]),
            url=f"https://tw.stock.yahoo.com/quote/{stock_id}.TW/technical-analysis",
            source="computed from FinMind 1y daily closes", price_unit="TWD",
            trading_days=245,  # 台股年交易日約 245（W10）
        )

    # ---------- fundamentals（FinMind 綜合損益表 + 資產負債表 + 月營收）----------

    async def get_fundamentals(self, ticker: str) -> list[Evidence]:
        stock_id = to_stock_id(ticker)
        if is_tw_etf(stock_id):  # stock_id 已是台股純數字形式，語意直接
            return []  # ETF 無個股損益表/資產負債表/月營收，不發無謂的 FinMind 請求
        # 損益表/資產負債表抓 ~5.5 年（多年期 roe_5y_avg 等需要）：加寬 start_date 仍是
        # 同一個 FinMind 請求、不增配額；TTM/最新季的下游邏輯只取最近資料、不受影響。
        income, balance, month = await asyncio.gather(
            self._fm("TaiwanStockFinancialStatements", stock_id, days_ago(2000)),
            self._fm("TaiwanStockBalanceSheet", stock_id, days_ago(2000)),
            self._fm("TaiwanStockMonthRevenue", stock_id, days_ago(430)),
            return_exceptions=True,
        )
        evs: list[Evidence] = []
        base_url = f"https://mops.twse.com.tw/mops/web/t146sb05?stock_id={stock_id}"

        evs += self._statement_evidence(income, INCOME_FIELDS, base_url,
                                        "FinMind TaiwanStockFinancialStatements")
        evs += self._ttm_evidence(income, base_url)
        evs += self._statement_evidence(balance, BALANCE_FIELDS, base_url,
                                        "FinMind TaiwanStockBalanceSheet")
        evs += self._derived_fundamentals(income, balance, base_url)
        evs += self._multiyear_fundamentals(income, balance, base_url)
        if not isinstance(month, BaseException):
            evs += self._month_revenue_evidence(month, stock_id)
        return evs

    # FinMind 的綜合損益表是「單季」值（已驗證：2024 四季 EPS 加總 = FY 全年）。
    # 在此確定性算出近四季合計 TTM，讓估值分析師有正確的 P/E 錨點（見 TTM_FIELDS）。
    @staticmethod
    def _ttm_sum(rows, fm_type: str) -> tuple[float, list[tuple[str, float]]] | None:
        """近四季合計 (total, last4)；不足四季回 None（不硬湊，避免低估 TTM）。"""
        if isinstance(rows, BaseException) or not rows:
            return None
        quarters = sorted(
            ((r["date"], float(r["value"])) for r in rows
             if r["type"] == fm_type and r["value"] is not None),
            reverse=True,
        )
        if len(quarters) < 4:
            return None
        last4 = quarters[:4]
        return sum(v for _, v in last4), last4

    @classmethod
    def _ttm_evidence(cls, rows, url) -> list[Evidence]:
        out: list[Evidence] = []
        for fm_type, (field, unit) in TTM_FIELDS.items():
            res = cls._ttm_sum(rows, fm_type)
            if res is None:
                continue
            total, last4 = res
            span = f"{last4[-1][0]}…{last4[0][0]}"
            out.append(Evidence(
                category="fundamentals", field=field, value=round(total, 2),
                unit=unit, source="computed from FinMind TaiwanStockFinancialStatements",
                url=url, as_of=date.fromisoformat(last4[0][0]),
                note=f"近四季合計 TTM（{span}）；估值用 P/E 應以此為分母，勿用單季×4",
            ))
        return out

    @staticmethod
    def _latest_values(rows) -> tuple[dict[str, float], date | None]:
        """最新一季的 {type: value}（跳過 None）與其季別日期；無資料回 ({}, None)。"""
        if isinstance(rows, BaseException) or not rows:
            return {}, None
        latest = max(r["date"] for r in rows)
        vals = {r["type"]: float(r["value"]) for r in rows
                if r["date"] == latest and r["value"] is not None}
        return vals, date.fromisoformat(latest)

    @staticmethod
    def _statement_evidence(rows, field_map, url, source) -> list[Evidence]:
        if isinstance(rows, BaseException) or not rows:
            return []
        latest = max(r["date"] for r in rows)
        as_of = date.fromisoformat(latest)
        latest_rows = {r["type"]: r["value"] for r in rows if r["date"] == latest}
        out: list[Evidence] = []
        for fm_type, (field, unit) in field_map.items():
            if fm_type in latest_rows and latest_rows[fm_type] is not None:
                out.append(Evidence(
                    category="fundamentals", field=field,
                    value=round(float(latest_rows[fm_type]), 2),
                    unit=unit, source=source, url=url, as_of=as_of,
                    note=f"季別 {latest}（{fm_type}）",
                ))
        return out

    # 確定性衍生欄位（與美股 canonical 命名對齊）。資產負債表與單季損益是同口徑的
    # 時點/單季快照，可直接相除；現金流量表（FCF / 利息保障）因 FinMind 給的是 YTD
    # 累計值、與單季混算會跨期出錯，留待後續專案處理，這裡不發。
    @classmethod
    def _derived_fundamentals(cls, income, balance, url) -> list[Evidence]:
        inc, inc_as_of = cls._latest_values(income)
        bal, bal_as_of = cls._latest_values(balance)
        src = "computed from FinMind TaiwanStockBalanceSheet / FinancialStatements"
        out: list[Evidence] = []

        def emit(field, value, unit, formula, as_of):
            out.append(Evidence(
                category="fundamentals", field=field, value=round(float(value), 2),
                unit=unit, source=src, url=url, as_of=as_of,
                note=f"確定性衍生：{formula}",
            ))

        ca, cl = bal.get("CurrentAssets"), bal.get("CurrentLiabilities")
        liab, eq = bal.get("Liabilities"), bal.get("Equity")
        if ca is not None and cl is not None:
            emit("working_capital", ca - cl, "TWD", "流動資產 − 流動負債", bal_as_of)
        if ca is not None and liab is not None:
            emit("net_net_value", ca - liab, "TWD", "流動資產 − 總負債（Graham net-net）", bal_as_of)
        if liab is not None and eq:
            emit("debt_to_equity", liab / eq, None, "總負債 / 股東權益", bal_as_of)

        gp, rev = inc.get("GrossProfit"), inc.get("Revenue")
        if gp is not None and rev:
            emit("gross_margin_pct", gp / rev * 100, "%", "單季毛利 / 營收 × 100", inc_as_of)

        # ROE 用 TTM 淨利（單季 ÷ 權益會低估 ~4x）；不足四季則不發。
        ni_ttm = cls._ttm_sum(income, "IncomeAfterTaxes")
        if ni_ttm is not None and eq:
            emit("roe_pct", ni_ttm[0] / eq * 100, "%",
                 "近四季 TTM 稅後淨利 / 股東權益 × 100", bal_as_of)
        return out

    # ---------- 多年期指標（季度聚合成年度）----------
    @staticmethod
    def _annual_sums(rows, fm_type: str) -> dict[int, float]:
        """{年: 該年四季合計}；只收「該年恰有四季」者（不足四季不硬湊，避免年度值偏低）。"""
        if isinstance(rows, BaseException) or not rows:
            return {}
        by_year: dict[int, dict[str, float]] = {}
        for r in rows:
            if r["type"] == fm_type and r["value"] is not None:
                d = date.fromisoformat(r["date"])
                by_year.setdefault(d.year, {})[r["date"]] = float(r["value"])
        return {y: sum(q.values()) for y, q in by_year.items() if len(q) == 4}

    @staticmethod
    def _yearend_equity(rows) -> dict[int, tuple[date, float]]:
        """{年: (年底季別日, 股東權益)}；取該年 12 月（Q4）的權益快照。"""
        if isinstance(rows, BaseException) or not rows:
            return {}
        out: dict[int, tuple[date, float]] = {}
        for r in rows:
            if r["type"] == "Equity" and r["value"] is not None:
                d = date.fromisoformat(r["date"])
                if d.month == 12:
                    out[d.year] = (d, float(r["value"]))
        return out

    @classmethod
    def _multiyear_fundamentals(cls, income, balance, url) -> list[Evidence]:
        """多年期指標（roe_5y_avg / gross_margin_trend_5y / earnings_stability_5y）。

        台股財報年度＝曆年；FinMind 損益是單季值，逐年合計四季成年度損益，ROE 以該年底
        權益為分母（與單期 roe_pct 同口徑：年度淨利 / 年底權益）。"""
        rev = cls._annual_sums(income, "Revenue")
        gp = cls._annual_sums(income, "GrossProfit")
        ni = cls._annual_sums(income, "IncomeAfterTaxes")
        eq = cls._yearend_equity(balance)

        roe_series = [(eq[y][0], ni[y] / eq[y][1] * 100)
                      for y in sorted(ni.keys() & eq.keys()) if eq[y][1]]
        gm_series = [(date(y, 12, 31), gp[y] / rev[y] * 100)
                     for y in sorted(gp.keys() & rev.keys()) if rev[y]]
        ni_series = [(date(y, 12, 31), ni[y]) for y in sorted(ni)]
        return multiyear_fundamentals(
            roe_pct_series=roe_series, gross_margin_pct_series=gm_series,
            net_income_series=ni_series,
            source="computed from FinMind TaiwanStockFinancialStatements / BalanceSheet "
                   "(multi-year, 季度聚合)", url=url,
        )

    @staticmethod
    def _month_revenue_evidence(rows: list[dict], stock_id: str) -> list[Evidence]:
        rows = [r for r in rows if r.get("revenue") is not None]
        if not rows:
            return []
        latest = rows[-1]
        as_of = date.fromisoformat(latest["date"])
        url = f"https://mops.twse.com.tw/mops/web/t21lv01_e?stock_id={stock_id}"
        evs = [Evidence(
            category="fundamentals", field="monthly_revenue",
            value=float(latest["revenue"]), unit="TWD",
            source="FinMind TaiwanStockMonthRevenue", url=url, as_of=as_of,
            note=f"{latest['revenue_year']}/{latest['revenue_month']:02d} 月營收",
        )]
        # 年增率：找去年同月
        prior = next((r for r in rows
                      if r["revenue_year"] == latest["revenue_year"] - 1
                      and r["revenue_month"] == latest["revenue_month"]), None)
        if prior and float(prior["revenue"]) > 0:
            yoy = (float(latest["revenue"]) / float(prior["revenue"]) - 1) * 100
            evs.append(Evidence(
                category="fundamentals", field="revenue_yoy_pct",
                value=round(yoy, 2), unit="%",
                source="computed from FinMind TaiwanStockMonthRevenue", url=url, as_of=as_of,
                note=f"{latest['revenue_month']:02d}月 YoY vs {prior['revenue_year']}",
            ))
        return evs

    # ---------- chips：台股籌碼面（三大法人 + 融資融券）----------

    async def get_chips(self, ticker: str) -> list[Evidence]:
        stock_id = to_stock_id(ticker)
        # issue #4：法人要 20 交易日累計，視窗需 ~35 天（含假日約 25 交易日）；融資券只要
        # 5 日變化，12 天足夠。原 issue C 段「縮到 3 天省配額」與 A 段「20 日累計」互斥，
        # 取較高價值的趨勢需求（A），捨棄籌碼端的配額節省。
        inst, margin, sbl = await asyncio.gather(
            self._fm("TaiwanStockInstitutionalInvestorsBuySell", stock_id, days_ago(35)),
            self._fm("TaiwanStockMarginPurchaseShortSale", stock_id, days_ago(12)),
            self._fm("TaiwanDailyShortSaleBalances", stock_id, days_ago(12)),
            return_exceptions=True,
        )
        evs: list[Evidence] = []
        if not isinstance(inst, BaseException) and inst:
            evs += self._institutional_evidence(inst, stock_id)
        if not isinstance(margin, BaseException) and margin:
            evs += self._margin_evidence(margin, stock_id)
        if not isinstance(sbl, BaseException) and sbl:
            evs += self._securities_lending_evidence(sbl, stock_id)
        return evs

    @staticmethod
    def _securities_lending_evidence(rows: list[dict], stock_id: str) -> list[Evidence]:
        """借券賣出餘額（SBL）——與融券餘額合為台股 short interest proxy（決議 3）。

        台股無美式 short interest，借券賣出 + 融券是最接近的第一手定位指標；evidence
        note 明示「proxy，非美式 short interest」，避免被當成同口徑值。"""
        rows = sorted(rows, key=lambda r: r["date"])
        latest = rows[-1]
        bal = latest.get("SBLShortSalesCurrentDayBalance")
        if bal is None:
            return []
        return [Evidence(
            category="chips", field="securities_lending_short_balance",
            value=int(bal), unit="shares", source="FinMind TaiwanDailyShortSaleBalances",
            # 資料源是 TWSE 借券交易（TWT72U），非 Yahoo 融資券頁——指向 TWSE 借券報表
            url="https://www.twse.com.tw/zh/trading/historical/twt72u.html",
            as_of=date.fromisoformat(latest["date"]),
            note="借券賣出餘額；與 short_balance（融券餘額）並列為台股空方定位 proxy（非美式 "
                 "short interest）。⚠ 兩欄單位不同——本欄為股數、short_balance 為張(lots, ×1000 股)，"
                 "比較或合計前須換算，勿直接相加。",
        )]

    @staticmethod
    def _institutional_evidence(rows: list[dict], stock_id: str) -> list[Evidence]:
        url = f"https://tw.stock.yahoo.com/quote/{stock_id}.TW/institutional-trading"
        src = "FinMind TaiwanStockInstitutionalInvestorsBuySell"
        # 每個交易日、每個法人別的 net buy（買-賣），供單日與 5/20 日累計共用。
        by_date: dict[str, dict[str, float]] = {}
        for r in rows:
            day = by_date.setdefault(r["date"], {})
            day[r["name"]] = day.get(r["name"], 0.0) + float(r["buy"]) - float(r["sell"])
        dates_desc = sorted(by_date, reverse=True)
        latest = dates_desc[0]
        as_of = date.fromisoformat(latest)
        net = by_date[latest]  # 單日（最新一日）

        groups = INST_GROUPS
        evs: list[Evidence] = []
        zh = {"foreign_net_buy": "外資", "trust_net_buy": "投信", "dealer_net_buy": "自營商"}
        for field, names in groups.items():
            val = sum(net.get(n, 0.0) for n in names)
            evs.append(Evidence(
                category="chips", field=field, value=round(val, 0), unit="shares",
                source=src, url=url, as_of=as_of, note=f"{zh[field]}單日買賣超（正=買超）",
            ))
        # 合計直接由所有 name 加總（= 三大法人合計），對 FinMind 改 schema 健壯；
        # 若出現未納入分群的新 name，於 note 標出以免靜默漏算。
        known = {n for names in groups.values() for n in names}
        unmapped = sorted(set(net) - known)
        total = sum(net.values())
        note = "三大法人合計單日買賣超（正=買超）"
        if unmapped:
            note += f"；⚠ FinMind 出現未分群類別 {unmapped}，已計入合計但未獨立呈現"
        evs.append(Evidence(
            category="chips", field="institutional_net_buy_total", value=round(total, 0),
            unit="shares", source=src, url=url, as_of=as_of, note=note,
        ))

        # issue #4 — 累計趨勢：外資 5/20 日、三法人合計 5 日。確定性加總，不足天數不發。
        def cum(names, n: int) -> float:
            return sum(by_date[d].get(nm, 0.0) for d in dates_desc[:n] for nm in names)

        all_names = sorted({nm for d in by_date.values() for nm in d})
        n_days = len(dates_desc)
        foreign = groups["foreign_net_buy"]
        for field, names, window, label in [
            ("foreign_net_buy_5d", foreign, 5, "外資累計買賣超"),
            ("foreign_net_buy_20d", foreign, 20, "外資累計買賣超"),
            ("institutional_net_buy_5d", all_names, 5, "三大法人累計買賣超"),
        ]:
            if n_days < window:
                continue  # 視窗內交易日不足，不發半截累計（避免誤導趨勢）
            evs.append(Evidence(
                category="chips", field=field, value=round(cum(names, window), 0),
                unit="shares", source=f"computed from {src}", url=url, as_of=as_of,
                # 明寫「N 個交易日」而非僅日期區間：window 內含假日時 calendar gap > 交易日數，
                # 避免 LLM 把 {start}…{latest} 當成日曆天誤算「近月/近週」。
                note=f"{label}（{window} 個交易日 {dates_desc[window - 1]}…{latest}；正=買超）",
            ))
        return evs

    @staticmethod
    def _margin_evidence(rows: list[dict], stock_id: str) -> list[Evidence]:
        rows = sorted(rows, key=lambda r: r["date"])  # 防 FinMind 非升冪
        latest = rows[-1]
        as_of = date.fromisoformat(latest["date"])
        url = f"https://tw.stock.yahoo.com/quote/{stock_id}.TW/margin-trading"
        src = "FinMind TaiwanStockMarginPurchaseShortSale"
        # 5 交易日前那一筆（index -6）；不足 6 筆則無法算 5 日變化。
        prior5 = rows[-6] if len(rows) >= 6 else None
        out: list[Evidence] = []
        for key, field, note, chg_field, zh in [
            ("MarginPurchaseTodayBalance", "margin_balance", "融資餘額（張）",
             "margin_balance_change_5d_pct", "融資餘額"),
            ("ShortSaleTodayBalance", "short_balance", "融券餘額（張）",
             "short_balance_change_5d_pct", "融券餘額"),
        ]:
            if latest.get(key) is not None:
                out.append(Evidence(
                    category="chips", field=field, value=int(latest[key]), unit="lots",
                    source=src, url=url, as_of=as_of, note=note,
                ))
                # issue #4 — 5 日餘額變化 %：區分多殺多趨勢 vs 當日沖銷殘留（程式算）。
                if prior5 and prior5.get(key) not in (None, 0):
                    chg = (float(latest[key]) / float(prior5[key]) - 1) * 100
                    out.append(Evidence(
                        category="chips", field=chg_field, value=round(chg, 2), unit="%",
                        source=f"computed from {src}", url=url, as_of=as_of,
                        note=f"{zh} 近 5 交易日變化（正=增加；vs {prior5['date']}）",
                    ))
        return out

    # ---------- estimates（分析師前瞻共識，estimate 類別）----------

    async def get_estimates(self, ticker: str) -> list[Evidence]:
        stock_id = to_stock_id(ticker)
        if is_tw_etf(stock_id):
            return []  # ETF 無個股盈餘共識
        # yfinance 對台股以 .TW / .TWO 兩種字尾掛牌，依序試（同 _yf_last_price 範式）
        return await to_thread_with_timeout(
            lambda: fetch_estimates(
                [f"{stock_id}.TW", f"{stock_id}.TWO"], currency="TWD",
                url=f"https://finance.yahoo.com/quote/{stock_id}.TW/analysis"),
            what=f"yfinance estimates {stock_id}", default=[])

    # ---------- news ----------

    async def get_news(self, ticker: str) -> list[Evidence]:
        stock_id = to_stock_id(ticker)
        try:
            rows = await self._fm("TaiwanStockNews", stock_id, days_ago(14))
        except Exception as e:
            logger.warning("TW news (TaiwanStockNews) fetch failed for %s: %s", stock_id, e)
            return []
        rows = sorted(rows, key=lambda r: r.get("date", ""), reverse=True)[:10]
        evs: list[Evidence] = []
        for i, r in enumerate(rows):
            title = r.get("title")
            if not title:
                continue
            as_of = None
            raw = r.get("date", "")
            if raw:
                try:
                    as_of = date.fromisoformat(raw[:10])
                except ValueError:
                    pass
            evs.append(Evidence(
                category="news", field=f"headline_{i + 1}", value=title,
                source=f"FinMind TaiwanStockNews ({r.get('source', '?')})",
                url=r.get("link"), as_of=as_of,
            ))
        return evs
