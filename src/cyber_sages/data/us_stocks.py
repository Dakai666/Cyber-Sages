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
from cyber_sages.data.indicators import compute_indicator_evidence, short_history_evidence
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


def drop_phantom_bars(hist):
    """剔除 yfinance「未結算佔位」日線 bar：Volume=0（美股交易日幾乎不存在真實 0 量日，
    0 量＝資料源尚未結算的 artifact）。

    SPCX 2026-06-16 實例：日線回 OHLC 全 = 前一日收 192.5、Volume = 0，但同日 1 分線
    真實收 $202、成交 5.4 億股。舊碼直接取 `iloc[-1]` 把這個幻覺值當第一手 latest_close/
    latest_volume 發出，整份 brief 圍繞「零成交量＝懸浮標價」立論。取最新報價/算指標前
    一律剔除這種 bar，寧可用稍舊但真實的 bar，也不用最新但虛構的值。純函式以便不打網路測試。

    語意：`Volume > 0` 同時剔除 Volume=0、NaN（pandas 比較為 False）與負值（理論上不該出現）。
    後人若改成 `>=` 會 silently 放行 0 量幽靈 bar——勿改。"""
    if hasattr(hist, "columns") and "Volume" in hist.columns:
        return hist[hist["Volume"] > 0]
    return hist


class USStockProvider:
    market = "US"

    # ---------- quote ----------

    async def get_quote(self, ticker: str) -> list[Evidence]:
        evs = await to_thread_with_timeout(
            lambda: self._quote_sync(ticker), what=f"yfinance quote {ticker}", default=[])
        # D4：Finnhub /quote 當「真正異源」的當前價（非 Yahoo 系）——讓跨源 fatal 檢查有真牙齒。
        # best-effort：缺 FINNHUB_API_KEY 或回 0（無資料）則略過，退回 yfinance intraday 比對。
        evs += await self._finnhub_quote(ticker)
        return evs

    async def _finnhub_quote(self, ticker: str) -> list[Evidence]:
        key = os.environ.get("FINNHUB_API_KEY")
        if not key:
            return []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                async def _fetch() -> httpx.Response:
                    r = await client.get("https://finnhub.io/api/v1/quote",
                                         params={"symbol": ticker, "token": key})
                    r.raise_for_status()
                    return r
                resp = await with_retry(_fetch, what=f"finnhub quote {ticker}")
            return self._finnhub_quote_evidence(resp.json(), f"https://finnhub.io/quote/{ticker}")
        except Exception as e:
            logger.warning("US finnhub quote failed for %s: %s", ticker, e)
            return []

    @staticmethod
    def _finnhub_quote_evidence(data: dict, url: str) -> list[Evidence]:
        """Finnhub /quote → 獨立當前價 evidence。`c`=current price、`t`=unix ts。

        Finnhub 對未知 symbol 回 c=0（非 error）——故 c<=0 視為無資料、回 []（優雅降級到
        yfinance intraday 比對）。抽純函式以便不打網路測試（同 _facts_to_evidence 範式）。"""
        c = data.get("c")
        if c is None or float(c) <= 0:
            return []
        as_of = None
        ts = data.get("t")
        if isinstance(ts, (int, float)) and ts > 0:
            as_of = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        return [Evidence(
            category="quote", field="last_price_finnhub", value=round(float(c), 2),
            unit="USD", source="finnhub /quote (independent of Yahoo)", url=url, as_of=as_of,
            note="真正異源（Finnhub，非 Yahoo 系）的當前價讀數，供跨源比對——D4",
        )]

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
        # drop_phantom_bars：剔除未結算的 0 量佔位 bar（見 SPCX 案例），避免幻覺價/量入帳
        raw_hist = t.history(period="5d", auto_adjust=False).dropna(subset=["Close"])
        hist = drop_phantom_bars(raw_hist)
        # #3 可回溯性：剔除了幾根、哪幾天——事後 debug（如 SPCX 幽靈 bar 在 6/16）才查得到
        dropped = len(raw_hist) - len(hist)
        if dropped > 0:
            dates = [str(d.date()) for d in raw_hist.index if d not in hist.index]
            evs.append(Evidence(
                category="quote", field="phantom_bars_dropped", value=dropped, unit="bars",
                source="drop_phantom_bars", url=url, as_of=date.today(),
                note=f"剔除 {dropped} 根 0 量幽靈 bar（{', '.join(dates)}）—資料源未結算佔位，未入帳",
            ))
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

        # 第三條路徑（獨立於 fast_info/daily 的「當前價」讀數）：盤中最後成交。供 audit 比對
        # 兩條同一時點的當前價——SPCX 案例 fast_info/daily 一起 stale 於 192.5、盤中真實 202。
        evs += self._intraday_evidence(t, url)

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
    def _intraday_evidence(t, url: str) -> list[Evidence]:
        """盤中最後成交價 + 當日真實累計量。

        這是獨立於 fast_info 與 daily-history 的第三條「當前價」讀數：同源 Yahoo 但走
        不同 endpoint（chart 1m），經驗上能抓到未結算 feed 的 staleness——SPCX 2026-06-16
        fast_info/daily 一起卡在 192.5，而 1m 線真實收 $202。真正異源的第二 provider 為
        P2 follow-up（D4）。盤後/假日回最近 session 末筆，與 fast_info 同值故不誤報。"""
        try:
            intr = t.history(period="1d", interval="1m").dropna(subset=["Close"])
        except Exception as e:
            logger.warning("US intraday fetch failed: %s", e)
            return []
        if len(intr) == 0:
            return []
        last_ts = intr.index[-1]
        # 新鮮度守衛：盤前/週末/長假時 1m 只回「上個 session」，last_ts 會落後。拿陳舊的
        # 末筆當「當前價」與 fast_info 比會誤判（盤前指示價 vs 昨日盤中末筆 → 假背離 fatal）。
        # 以 bar 自身時區計年齡（避免從台灣跑美股時 date.today() 的時區錯位——SPCX 即此情境，
        # last_ts=06-16 ET、本地已 06-17；改比時間差 ~11.8h 仍算當前）。逾 16h 視為非當前 → 不發，
        # 交給 audit 的 cross_source「跳過」warning 如實揭露缺獨立源。
        try:
            now = datetime.now(last_ts.tzinfo) if last_ts.tzinfo else datetime.now()
            age_h = (now - last_ts.to_pydatetime()).total_seconds() / 3600
        except Exception:
            age_h = 0.0
        if age_h > 16:
            logger.info("US intraday last bar %s is %.1fh old; skip as non-current", last_ts, age_h)
            return []
        evs = [Evidence(
            category="quote", field="last_price_intraday",
            value=round(float(intr.iloc[-1]["Close"]), 2), unit="USD",
            source="yfinance intraday (1m last trade)", url=url, as_of=last_ts.date(),
            note="盤中最後成交價（獨立於 fast_info/daily 的當前價讀數，供跨源比對）",
        )]
        # C1：盤中 1m 累計量——讓「成交量」有第一手值，不再受 daily 佔位 bar 的 0 量誤導。
        # ⚠ 1m 盤中跑只回「目前為止」的 bars，故此值盤後跑＝當日總量、盤中跑＝部分累計。
        # note 據實標明，避免被當成「日 total」誤用（目前無人引用，預留 P1+ 量價/flow 分析 C7）。
        vol = int(intr["Volume"].sum())
        if vol > 0:
            evs.append(Evidence(
                category="quote", field="intraday_volume", value=vol, unit="shares",
                source="yfinance intraday (1m volume sum)", url=url, as_of=last_ts.date(),
                note="盤中 1m 累計成交量；盤後跑＝當日總量，盤中跑＝部分累計（非當日 total）",
            ))
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
        # 同 quote：剔除 0 量佔位 bar，避免幽靈尾 bar 污染 SMA/RSI/MACD 與 as_of 日期
        hist = drop_phantom_bars(
            t.history(period="1y", auto_adjust=True).dropna(subset=["Close"]))
        if len(hist) < 30:
            # S5：< 30 交易日 → 發 IPO/新上市標記（讓 audit 降為 warning + 明說），不硬出指標
            return short_history_evidence(
                len(hist), url=f"https://finance.yahoo.com/quote/{ticker}/history",
                source="yfinance 1y daily (insufficient bars)")
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
                # C2：無 SEC CIK（ADR/新上市）→ yfinance 二手 fallback。嚴格隔離：source 明標
                # second-hand、audit 會把 fundamentals 維度標「二手降級」（見 data_audit provenance
                # 檢查），不讓二手值被當第一手。寧可二手降級揭露，也不讓基本面整片空白。
                return await to_thread_with_timeout(
                    lambda: self._yf_fundamentals_sync(ticker),
                    what=f"yfinance fundamentals fallback {ticker}", default=[])

            async def _fetch() -> httpx.Response:
                r = await client.get(EDGAR_FACTS.format(cik=cik))
                r.raise_for_status()
                return r

            resp = await with_retry(_fetch, what=f"EDGAR companyfacts {ticker}")
            facts = resp.json()

        filing_base = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik:010d}"
        return self._facts_to_evidence(facts, filing_base)

    # ---------- fundamentals 二手 fallback（C2：無 SEC CIK 時）----------

    def _yf_fundamentals_sync(self, ticker: str) -> list[Evidence]:
        import yfinance as yf

        try:
            info = yf.Ticker(ticker).info or {}
        except Exception as e:
            logger.warning("US yfinance fundamentals fallback failed for %s: %s", ticker, e)
            return []
        return self._yf_fundamentals_from_info(
            info, f"https://finance.yahoo.com/quote/{ticker}/financials")

    @staticmethod
    def _yf_fundamentals_from_info(info: dict, url: str) -> list[Evidence]:
        """yfinance `info` → canonical fundamentals evidence（**二手**，無 SEC CIK 時的 fallback）。

        嚴格隔離（決議：做但隔離二手）：source 一律標 `(second-hand)`、note 明示「非 SEC 第一手」，
        讓 audit 的 provenance 檢查把 fundamentals 維度標二手降級、下游不誤當第一手。抽純函式
        以便不打網路測試（同 `_facts_to_evidence` / `_short_interest_evidence` 範式）。

        欄位口徑：totalRevenue/netIncomeToCommon 為 yfinance 的 TTM 聚合值（非單一年報），故給
        canonical `*_annual` 名以滿足下游與 audit 必要欄位，但 note 標明二手 TTM 口徑。"""
        src = "yfinance financials (second-hand)"
        note = "二手（yfinance），非 SEC 第一手——信心已降級，僅供基本面補充"
        out: list[Evidence] = []
        mapping = [
            ("revenue_annual", "totalRevenue", "USD"),
            ("net_income_annual", "netIncomeToCommon", "USD"),
            ("eps_ttm", "trailingEps", "USD/shares"),
            ("total_assets", "totalAssets", "USD"),
        ]
        for field, key, unit in mapping:
            v = info.get(key)
            if v is not None:
                out.append(Evidence(
                    category="fundamentals", field=field, value=round(float(v), 2),
                    unit=unit, source=src, url=url, as_of=None, note=note,
                ))
        return out

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
