"""美股資料源：yfinance（行情/歷史）+ SEC EDGAR（第一手財報）+ Finnhub（新聞，選用）。

原則：
- 技術指標在程式裡確定性計算，不讓 LLM 算數。
- 報價同時取 fast_info 與日線收盤兩條路徑，供 audit 閘門跨源比對。
- 財報數字以 SEC EDGAR companyfacts 為準（真正第一手），yfinance 衍生值標明來源。
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone

import httpx

from cyber_sages.data.damodaran import industry_benchmark_evidence
from cyber_sages.data.estimates import fetch_estimates
from cyber_sages.data.evidence import Evidence
from cyber_sages.data.indicators import compute_indicator_evidence
from cyber_sages.data.longterm import multiyear_fundamentals
from cyber_sages.data.retry import to_thread_with_timeout, with_retry

logger = logging.getLogger(__name__)

EDGAR_TICKER_MAP = "https://www.sec.gov/files/company_tickers.json"
EDGAR_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# 概念名 → (我們的欄位名, 候選 us-gaap 概念, 單位)
GAAP_FIELDS: list[tuple[str, list[str], str]] = [
    ("revenue", ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"], "USD"),
    ("net_income", ["NetIncomeLoss"], "USD"),
    ("eps_diluted", ["EarningsPerShareDiluted"], "USD/shares"),
    ("total_assets", ["Assets"], "USD"),
    ("total_liabilities", ["Liabilities"], "USD"),
    ("stockholders_equity", ["StockholdersEquity"], "USD"),
    ("operating_cash_flow", ["NetCashProvidedByUsedInOperatingActivities"], "USD"),
    # 以下為 Spec A P0 衍生欄位的原始輸入（owner earnings / 安全邊際 / 槓桿 / 利息保障）。
    ("gross_profit", ["GrossProfit"], "USD"),
    ("operating_income", ["OperatingIncomeLoss"], "USD"),
    ("current_assets", ["AssetsCurrent"], "USD"),
    ("current_liabilities", ["LiabilitiesCurrent"], "USD"),
    ("capex", ["PaymentsToAcquirePropertyPlantAndEquipment",
               "PaymentsToAcquireProductiveAssets"], "USD"),
    ("depreciation_amortization", ["DepreciationDepletionAndAmortization",
                                   "DepreciationAmortizationAndAccretionNet",
                                   "DepreciationAndAmortization"], "USD"),
    ("interest_expense", ["InterestExpense", "InterestExpenseNonoperating"], "USD"),
]

# 確定性衍生欄位（絕不讓 LLM 算數）：以年報值為輸入，公式與輸入欄位寫進 note 供 cite-check。
# 每筆 (欄位名, 單位, 公式說明, 計算函式 fn(vals)->float|None)。fn 回 None 表示輸入缺/不合法則略過。
def _safe_div(num: float, den: float) -> float | None:
    return num / den if den else None


DERIVED_FUNDAMENTALS: list[tuple[str, str | None, str, "callable"]] = [
    ("free_cash_flow_annual", "USD", "OCF − CapEx",
     lambda v: v["operating_cash_flow_annual"] - v["capex_annual"]),
    ("working_capital", "USD", "流動資產 − 流動負債",
     lambda v: v["current_assets_annual"] - v["current_liabilities_annual"]),
    ("net_net_value", "USD", "流動資產 − 總負債（Graham net-net）",
     lambda v: v["current_assets_annual"] - v["total_liabilities_annual"]),
    ("debt_to_equity", None, "總負債 / 股東權益",
     lambda v: _safe_div(v["total_liabilities_annual"], v["stockholders_equity_annual"])),
    ("interest_coverage", "x", "營業利益 / 利息費用",
     lambda v: _safe_div(v["operating_income_annual"], abs(v["interest_expense_annual"]))),
    ("gross_margin_pct", "%", "毛利 / 營收 × 100",
     lambda v: _safe_div(v["gross_profit_annual"] * 100, v["revenue_annual"])),
    ("roe_pct", "%", "稅後淨利 / 股東權益 × 100",
     lambda v: _safe_div(v["net_income_annual"] * 100, v["stockholders_equity_annual"])),
]


def _ua() -> str:
    return os.environ.get("EDGAR_USER_AGENT", "Cyber-Sages research bot contact@example.com")


class USStockProvider:
    market = "US"

    # ---------- quote ----------

    async def get_quote(self, ticker: str) -> list[Evidence]:
        return await to_thread_with_timeout(
            lambda: self._quote_sync(ticker), what=f"yfinance quote {ticker}", default=[])

    def _quote_sync(self, ticker: str) -> list[Evidence]:
        import yfinance as yf

        t = yf.Ticker(ticker)
        evs: list[Evidence] = []
        url = f"https://finance.yahoo.com/quote/{ticker}"

        fast = t.fast_info
        last = getattr(fast, "last_price", None)
        if last:
            evs.append(Evidence(
                category="quote", field="last_price", value=round(float(last), 2),
                unit="USD", source="yfinance fast_info", url=url, as_of=date.today(),
            ))
        mcap = getattr(fast, "market_cap", None)
        if mcap:
            evs.append(Evidence(
                category="quote", field="market_cap", value=float(mcap),
                unit="USD", source="yfinance fast_info", url=url, as_of=date.today(),
            ))

        # 第二條路徑：日線最後收盤，供 audit 跨源比對
        hist = t.history(period="5d", auto_adjust=False).dropna(subset=["Close"])
        if len(hist) > 0:
            last_row = hist.iloc[-1]
            close_date = hist.index[-1].date()
            evs.append(Evidence(
                category="quote", field="latest_close", value=round(float(last_row["Close"]), 2),
                unit="USD", source="yfinance daily history", url=url, as_of=close_date,
            ))
            evs.append(Evidence(
                category="quote", field="latest_volume", value=int(last_row["Volume"]),
                unit="shares", source="yfinance daily history", url=url, as_of=close_date,
            ))

        info = {}
        try:
            info = t.info or {}
        except Exception as e:
            # info 失敗只少了 PE/股利/公司名（非核心），但 schema 變更會悶掉整欄——記 log
            logger.warning("US yfinance .info failed for %s: %s", ticker, e)
        for field, key in [("trailing_pe", "trailingPE"), ("forward_pe", "forwardPE"),
                           ("dividend_yield_pct", "dividendYield")]:
            if info.get(key) is not None:
                evs.append(Evidence(
                    category="quote", field=field, value=round(float(info[key]), 3),
                    source="yfinance info (derived)", url=url, as_of=date.today(),
                ))
        if info.get("longName"):
            evs.append(Evidence(
                category="profile", field="company_name", value=info["longName"],
                source="yfinance info", url=url,
            ))
        if info.get("sector"):
            evs.append(Evidence(
                category="profile", field="sector",
                value=f"{info.get('sector')} / {info.get('industry', '?')}",
                source="yfinance info", url=url,
            ))
        # 空頭部位（short interest）——複用上面已抓的 info，不重打一次 .info
        evs += self._short_interest_evidence(info, url)
        # 產業估值 benchmark（Damodaran，US-only）——同樣複用 info 的 industry
        evs += industry_benchmark_evidence(info.get("industry"), url)
        return evs

    @staticmethod
    def _short_interest_evidence(info: dict, url: str) -> list[Evidence]:
        """yfinance 的 short interest（二手：源自 FINRA 半月報，yfinance 轉手）。歸 chips
        類別讓技術/籌碼分析師讀為空方定位訊號；source 明標 second-hand。"""
        src = "yfinance short interest (FINRA, second-hand)"
        as_of = None
        ts = info.get("dateShortInterest")
        if isinstance(ts, (int, float)):
            as_of = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        out: list[Evidence] = []
        if info.get("shortPercentOfFloat") is not None:
            out.append(Evidence(
                category="chips", field="short_percent_of_float",
                value=round(float(info["shortPercentOfFloat"]) * 100, 2), unit="%",
                source=src, url=url, as_of=as_of, note="流通股中被放空比例（二手，FINRA 半月報）"))
        if info.get("sharesShort") is not None:
            out.append(Evidence(
                category="chips", field="shares_short", value=int(info["sharesShort"]),
                unit="shares", source=src, url=url, as_of=as_of, note="放空股數（二手）"))
        if info.get("shortRatio") is not None:
            out.append(Evidence(
                category="chips", field="short_ratio", value=round(float(info["shortRatio"]), 2),
                unit="days", source=src, url=url, as_of=as_of,
                note="days-to-cover：放空股數 / 日均量（二手）"))
        # MoM 趨勢：放空是否擴大（確定性算，Burry 看空方累積）
        cur, prior = info.get("sharesShort"), info.get("sharesShortPriorMonth")
        if cur is not None and prior:  # truthy 守衛擋 prior=0/None，避免 div/0

            out.append(Evidence(
                category="chips", field="short_interest_change_mom_pct",
                value=round((float(cur) / float(prior) - 1) * 100, 2), unit="%",
                source=f"computed from {src}", url=url, as_of=as_of,
                note="放空股數較上月變化（正=空方擴大）"))
        return out

    # ---------- history + 確定性技術指標 ----------

    async def get_history(self, ticker: str) -> list[Evidence]:
        return await to_thread_with_timeout(
            lambda: self._history_sync(ticker), what=f"yfinance history {ticker}", default=[])

    def _history_sync(self, ticker: str) -> list[Evidence]:
        import yfinance as yf

        t = yf.Ticker(ticker)
        hist = t.history(period="1y", auto_adjust=True).dropna(subset=["Close"])
        if len(hist) < 30:
            return []
        return compute_indicator_evidence(
            hist["Close"], as_of=hist.index[-1].date(),
            url=f"https://finance.yahoo.com/quote/{ticker}/history",
            source="computed from yfinance 1y daily closes", price_unit="USD",
        )

    # ---------- fundamentals (SEC EDGAR 第一手) ----------

    async def get_fundamentals(self, ticker: str) -> list[Evidence]:
        headers = {"User-Agent": _ua(), "Accept-Encoding": "gzip"}
        async with httpx.AsyncClient(headers=headers, timeout=30) as client:
            cik = await self._resolve_cik(client, ticker)
            if cik is None:
                return []

            async def _fetch() -> httpx.Response:
                r = await client.get(EDGAR_FACTS.format(cik=cik))
                r.raise_for_status()
                return r

            resp = await with_retry(_fetch, what=f"EDGAR companyfacts {ticker}")
            facts = resp.json()

        filing_base = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik:010d}"
        return self._facts_to_evidence(facts, filing_base)

    @classmethod
    def _facts_to_evidence(cls, facts: dict, filing_base: str) -> list[Evidence]:
        """純解析：SEC companyfacts JSON → Evidence（raw 年報/季報 + 確定性衍生）。

        抽成純函式以便不打網路測試；網路只負責取得 `facts`。"""
        evs: list[Evidence] = []
        gaap = facts.get("facts", {}).get("us-gaap", {})

        # 衍生欄位要用同一份年報數，邊發 raw evidence 邊記下年度值與其 end 日期。
        annual_vals: dict[str, float] = {}
        annual_as_of: date | None = None
        for field, concepts, unit in GAAP_FIELDS:
            # 公司會換 XBRL 標籤（如 NVDA 的營收概念）：
            # 不能用「第一個有資料的概念」，要跨概念取 end 日期最新的一筆。
            annual: dict | None = None
            quarterly: dict | None = None
            for concept in concepts:
                entries = gaap.get(concept, {}).get("units", {}).get(unit, [])
                if not entries:
                    continue
                a = cls._latest(entries, form_prefix="10-K",
                                min_duration_days=300 if unit == "USD" and field != "eps_diluted" else None)
                q = cls._latest(entries, form_prefix="10-Q")
                if a and (annual is None or a["end"] > annual["end"]):
                    annual = {**a, "_concept": concept}
                if q and (quarterly is None or q["end"] > quarterly["end"]):
                    quarterly = {**q, "_concept": concept}
            if annual:
                evs.append(Evidence(
                    category="fundamentals", field=f"{field}_annual",
                    value=annual["val"], unit=unit, source="SEC EDGAR companyfacts (10-K)",
                    url=filing_base, as_of=date.fromisoformat(annual["end"]),
                    note=f"FY{annual.get('fy')} {annual['_concept']}",
                ))
                annual_vals[f"{field}_annual"] = float(annual["val"])
                end = date.fromisoformat(annual["end"])
                annual_as_of = end if annual_as_of is None or end > annual_as_of else annual_as_of
            if quarterly and (not annual or quarterly["end"] > annual["end"]):
                evs.append(Evidence(
                    category="fundamentals", field=f"{field}_latest_quarter",
                    value=quarterly["val"], unit=unit, source="SEC EDGAR companyfacts (10-Q)",
                    url=filing_base, as_of=date.fromisoformat(quarterly["end"]),
                    note=f"{quarterly.get('fy')}{quarterly.get('fp', '')} {quarterly['_concept']}",
                ))
        evs += cls._derived_fundamentals(annual_vals, annual_as_of, filing_base)
        evs += cls._multiyear_fundamentals(gaap, filing_base)

        # eps_ttm（issue #19）：最近四季合計，讓 P/E 與 yfinance trailing 同口徑（TTM-vs-TTM）
        ttm = cls._eps_ttm(gaap)
        if ttm:
            val, as_of, note = ttm
            evs.append(Evidence(
                category="fundamentals", field="eps_ttm", value=val, unit="USD/shares",
                source="computed from SEC EDGAR companyfacts (10-K/10-Q)",
                url=filing_base, as_of=as_of, note=note,
            ))

        dei = facts.get("facts", {}).get("dei", {})
        shares = dei.get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares", [])
        latest_shares = cls._latest(shares, form_prefix="10-")
        if latest_shares:
            evs.append(Evidence(
                category="fundamentals", field="shares_outstanding",
                value=latest_shares["val"], unit="shares", source="SEC EDGAR companyfacts (dei)",
                url=filing_base, as_of=date.fromisoformat(latest_shares["end"]),
            ))
        return evs

    @staticmethod
    def _derived_fundamentals(
        annual_vals: dict[str, float], as_of: date | None, url: str
    ) -> list[Evidence]:
        """從年報原始值確定性算出衍生欄位（FCF / 槓桿 / 利息保障 / 毛利率 / ROE / net-net）。

        缺任一輸入或除以零則該欄位略過——寧缺勿錯，下游 persona skill 會把缺值記為
        not_evaluable（見 Spec E），不假裝有值。"""
        out: list[Evidence] = []
        for field, unit, formula, fn in DERIVED_FUNDAMENTALS:
            try:
                val = fn(annual_vals)
            except KeyError:
                continue  # 缺原始輸入
            if val is None:
                continue
            out.append(Evidence(
                category="fundamentals", field=field, value=round(float(val), 2),
                unit=unit, source="computed from SEC EDGAR companyfacts (10-K)",
                url=url, as_of=as_of, note=f"確定性衍生：{formula}",
            ))
        return out

    @staticmethod
    def _annual_map(gaap: dict, concepts: list[str], unit: str) -> dict[str, float]:
        """逐年度的 {fiscal-year-end(iso): val}（跨候選概念合併、全年 10-K）。

        companyfacts 已聚合多年（一份 10-K 含 3 年比較期），故 dedup by end 即得多年序列；
        流量科目（含 start）只收 ~全年 duration，排除季/半年累計；資產負債科目（無 start）全收。
        同一 end 多筆修訂後寫覆蓋（取較新申報的最終值）。"""
        out: dict[str, float] = {}
        for concept in concepts:
            for e in gaap.get(concept, {}).get("units", {}).get(unit, []):
                if not e.get("form", "").startswith("10-K") or "end" not in e or "val" not in e:
                    continue
                if "start" in e:
                    dur = (date.fromisoformat(e["end"]) - date.fromisoformat(e["start"])).days
                    if dur < 300:
                        continue
                out[e["end"]] = float(e["val"])
        return out

    @classmethod
    def _multiyear_fundamentals(cls, gaap: dict, url: str) -> list[Evidence]:
        """多年期指標（roe_5y_avg / gross_margin_trend_5y / earnings_stability_5y）。"""
        concepts = {field: c for field, c, _unit in GAAP_FIELDS}
        ni = cls._annual_map(gaap, ["NetIncomeLoss"], "USD")
        eq = cls._annual_map(gaap, ["StockholdersEquity"], "USD")
        gp = cls._annual_map(gaap, concepts["gross_profit"], "USD")
        rev = cls._annual_map(gaap, concepts["revenue"], "USD")

        roe_series = [(date.fromisoformat(end), ni[end] / eq[end] * 100)
                      for end in sorted(ni.keys() & eq.keys()) if eq[end]]
        gm_series = [(date.fromisoformat(end), gp[end] / rev[end] * 100)
                     for end in sorted(gp.keys() & rev.keys()) if rev[end]]
        ni_series = [(date.fromisoformat(end), ni[end]) for end in sorted(ni)]
        return multiyear_fundamentals(
            roe_pct_series=roe_series, gross_margin_pct_series=gm_series,
            net_income_series=ni_series,
            source="computed from SEC EDGAR companyfacts (10-K, multi-year)", url=url,
        )

    async def _resolve_cik(self, client: httpx.AsyncClient, ticker: str) -> int | None:
        async def _fetch() -> httpx.Response:
            r = await client.get(EDGAR_TICKER_MAP)
            r.raise_for_status()
            return r

        resp = await with_retry(_fetch, what="EDGAR ticker map")
        for entry in resp.json().values():
            if entry["ticker"].upper() == ticker.upper():
                return int(entry["cik_str"])
        return None

    @staticmethod
    def _eps_ttm(gaap: dict) -> tuple[float, date, str] | None:
        """US trailing-12-month 稀釋 EPS：確定性合計最近四季（issue #19）。

        SEC 不發 Q4 的 10-Q：fiscal Q4 EPS = FY 年報(10-K) − (Q1+Q2+Q3)。10-Q 同時報
        「單季」與「YTD 累計」，用 duration(~一季) 篩出單季值、排除 6/9 個月累計。
        回 (eps_ttm, as_of=最近一季 end, note)，季度不足或四季不相鄰則回 None（寧缺勿錯）。"""
        entries = gaap.get("EarningsPerShareDiluted", {}).get("units", {}).get("USD/shares", [])
        quarters: dict[str, float] = {}   # end(iso) → 單季 EPS（後寫覆蓋＝取較新修訂）
        annuals: list[dict] = []
        for e in entries:
            if "start" not in e or "end" not in e or "val" not in e:
                continue  # 時點值/缺欄位，EPS 本該是 duration 型
            dur = (date.fromisoformat(e["end"]) - date.fromisoformat(e["start"])).days
            if 60 <= dur <= 100:
                quarters[e["end"]] = float(e["val"])
            elif 350 <= dur <= 380:
                annuals.append(e)
        # 用 FY 反推缺漏的 fiscal Q4：FY − 該會計年度區間內已知的三個單季
        recon: set[str] = set()
        for a in annuals:
            if a["end"] in quarters:
                continue  # 該季已有單季值（少見），不覆蓋
            within = [v for end, v in quarters.items() if a["start"] < end < a["end"]]
            if len(within) == 3:
                quarters[a["end"]] = float(a["val"]) - sum(within)
                recon.add(a["end"])
        if len(quarters) < 4:
            return None
        last4 = sorted(quarters.items())[-4:]            # 取 end 最新的四季
        ends = [date.fromisoformat(k) for k, _ in last4]
        if any(not 80 <= (ends[i + 1] - ends[i]).days <= 100 for i in range(3)):
            return None  # 四季不相鄰（中間缺季），TTM 不可靠
        ttm = sum(v for _, v in last4)
        q4_note = "；fiscal Q4 由 FY−(Q1+Q2+Q3) 反推" if recon & {k for k, _ in last4} else ""
        note = f"trailing-12M = Σ最近四季 ({last4[0][0]}…{last4[-1][0]}){q4_note}"
        return round(ttm, 4), ends[-1], note

    @staticmethod
    def _latest(entries: list[dict], form_prefix: str, min_duration_days: int | None = None) -> dict | None:
        """取指定 form 的最新一筆。min_duration_days 用來區分年度值與季度值（duration 型概念）。"""
        best: dict | None = None
        for e in entries:
            if not e.get("form", "").startswith(form_prefix) or "end" not in e:
                continue
            if min_duration_days is not None and "start" in e:
                dur = (date.fromisoformat(e["end"]) - date.fromisoformat(e["start"])).days
                if dur < min_duration_days:
                    continue
            if best is None or e["end"] > best["end"]:
                best = e
        return best

    # ---------- estimates（分析師前瞻共識，estimate 類別）----------

    async def get_estimates(self, ticker: str) -> list[Evidence]:
        return await to_thread_with_timeout(
            lambda: fetch_estimates(
                [ticker], currency="USD",
                url=f"https://finance.yahoo.com/quote/{ticker}/analysis"),
            what=f"yfinance estimates {ticker}", default=[])

    # ---------- news ----------

    async def get_news(self, ticker: str) -> list[Evidence]:
        key = os.environ.get("FINNHUB_API_KEY")
        if key:
            try:
                return await self._finnhub_news(ticker, key)
            except Exception as e:
                logger.warning("US finnhub news failed for %s, falling back to yfinance: %s",
                               ticker, e)
        return await to_thread_with_timeout(
            lambda: self._yf_news_sync(ticker), what=f"yfinance news {ticker}", default=[])

    async def _finnhub_news(self, ticker: str, key: str) -> list[Evidence]:
        to = date.today()
        frm = to - timedelta(days=14)
        async with httpx.AsyncClient(timeout=30) as client:
            async def _fetch() -> httpx.Response:
                r = await client.get(
                    "https://finnhub.io/api/v1/company-news",
                    params={"symbol": ticker, "from": frm.isoformat(),
                            "to": to.isoformat(), "token": key},
                )
                r.raise_for_status()
                return r

            resp = await with_retry(_fetch, what=f"finnhub news {ticker}")
            items = resp.json()[:10]
        return [
            Evidence(
                category="news", field=f"headline_{i + 1}",
                value=f"{it.get('headline', '')} — {(it.get('summary') or '')[:200]}",
                source=f"finnhub ({it.get('source', '?')})", url=it.get("url"),
                as_of=datetime.fromtimestamp(it["datetime"], tz=timezone.utc).date()
                if it.get("datetime") else None,
            )
            for i, it in enumerate(items)
        ]

    def _yf_news_sync(self, ticker: str) -> list[Evidence]:
        import yfinance as yf

        evs: list[Evidence] = []
        try:
            news = yf.Ticker(ticker).news or []
        except Exception as e:
            logger.warning("US yfinance news failed for %s: %s", ticker, e)
            return evs
        for i, raw in enumerate(news[:10]):
            content = raw.get("content", raw)  # 新舊版 yfinance 結構不同
            title = content.get("title")
            if not title:
                continue
            url = (content.get("canonicalUrl") or {}).get("url") or raw.get("link")
            provider = (content.get("provider") or {}).get("displayName") or raw.get("publisher", "?")
            pub = content.get("pubDate") or content.get("displayTime")
            as_of = None
            if isinstance(pub, str):
                try:
                    as_of = datetime.fromisoformat(pub.replace("Z", "+00:00")).date()
                except ValueError:
                    pass
            elif isinstance(raw.get("providerPublishTime"), (int, float)):
                as_of = datetime.fromtimestamp(raw["providerPublishTime"], tz=timezone.utc).date()
            evs.append(Evidence(
                category="news", field=f"headline_{i + 1}", value=title,
                source=f"yfinance news ({provider})", url=url, as_of=as_of,
            ))
        return evs
