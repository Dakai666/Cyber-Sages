"""美股資料源：yfinance（行情/歷史）+ SEC EDGAR（第一手財報）+ Finnhub（新聞，選用）。

原則：
- 技術指標在程式裡確定性計算，不讓 LLM 算數。
- 報價同時取 fast_info 與日線收盤兩條路徑，供 audit 閘門跨源比對。
- 財報數字以 SEC EDGAR companyfacts 為準（真正第一手），yfinance 衍生值標明來源。
"""

from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timedelta, timezone

import httpx

from cyber_sages.data.evidence import Evidence

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
]


def _ua() -> str:
    return os.environ.get("EDGAR_USER_AGENT", "Cyber-Sages research bot contact@example.com")


class USStockProvider:
    market = "US"

    # ---------- quote ----------

    async def get_quote(self, ticker: str) -> list[Evidence]:
        return await asyncio.to_thread(self._quote_sync, ticker)

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
        except Exception:
            pass
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
        return evs

    # ---------- history + 確定性技術指標 ----------

    async def get_history(self, ticker: str) -> list[Evidence]:
        return await asyncio.to_thread(self._history_sync, ticker)

    def _history_sync(self, ticker: str) -> list[Evidence]:
        import yfinance as yf

        t = yf.Ticker(ticker)
        hist = t.history(period="1y", auto_adjust=True).dropna(subset=["Close"])
        if len(hist) < 30:
            return []
        close = hist["Close"]
        as_of = hist.index[-1].date()
        url = f"https://finance.yahoo.com/quote/{ticker}/history"

        def ev(field: str, value: float, unit: str | None = None, note: str | None = None) -> Evidence:
            return Evidence(
                category="history", field=field, value=round(float(value), 2),
                unit=unit, source="computed from yfinance 1y daily closes",
                url=url, as_of=as_of, note=note,
            )

        evs = [
            ev("sma_20", close.rolling(20).mean().iloc[-1], "USD"),
            ev("sma_50", close.rolling(50).mean().iloc[-1], "USD"),
            ev("return_1m_pct", (close.iloc[-1] / close.iloc[-21] - 1) * 100, "%"),
            ev("return_3m_pct", (close.iloc[-1] / close.iloc[-63] - 1) * 100, "%"),
            ev("high_52w", close.max(), "USD"),
            ev("low_52w", close.min(), "USD"),
            ev("volatility_30d_annualized_pct",
               close.pct_change().tail(30).std() * (252 ** 0.5) * 100, "%"),
        ]
        if len(close) >= 200:
            evs.append(ev("sma_200", close.rolling(200).mean().iloc[-1], "USD"))
            evs.append(ev("return_1y_pct", (close.iloc[-1] / close.iloc[0] - 1) * 100, "%"))

        # RSI(14)
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean().iloc[-1]
        loss = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
        if loss > 0:
            evs.append(ev("rsi_14", 100 - 100 / (1 + gain / loss)))

        # MACD(12,26,9) histogram
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        evs.append(ev("macd_histogram", (macd - signal).iloc[-1],
                      note="positive = bullish momentum"))
        return evs

    # ---------- fundamentals (SEC EDGAR 第一手) ----------

    async def get_fundamentals(self, ticker: str) -> list[Evidence]:
        headers = {"User-Agent": _ua(), "Accept-Encoding": "gzip"}
        async with httpx.AsyncClient(headers=headers, timeout=30) as client:
            cik = await self._resolve_cik(client, ticker)
            if cik is None:
                return []
            resp = await client.get(EDGAR_FACTS.format(cik=cik))
            resp.raise_for_status()
            facts = resp.json()

        evs: list[Evidence] = []
        gaap = facts.get("facts", {}).get("us-gaap", {})
        filing_base = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik:010d}"

        for field, concepts, unit in GAAP_FIELDS:
            # 公司會換 XBRL 標籤（如 NVDA 的營收概念）：
            # 不能用「第一個有資料的概念」，要跨概念取 end 日期最新的一筆。
            annual: dict | None = None
            quarterly: dict | None = None
            for concept in concepts:
                entries = gaap.get(concept, {}).get("units", {}).get(unit, [])
                if not entries:
                    continue
                a = self._latest(entries, form_prefix="10-K",
                                 min_duration_days=300 if unit == "USD" and field != "eps_diluted" else None)
                q = self._latest(entries, form_prefix="10-Q")
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
            if quarterly and (not annual or quarterly["end"] > annual["end"]):
                evs.append(Evidence(
                    category="fundamentals", field=f"{field}_latest_quarter",
                    value=quarterly["val"], unit=unit, source="SEC EDGAR companyfacts (10-Q)",
                    url=filing_base, as_of=date.fromisoformat(quarterly["end"]),
                    note=f"{quarterly.get('fy')}{quarterly.get('fp', '')} {quarterly['_concept']}",
                ))
        dei = facts.get("facts", {}).get("dei", {})
        shares = dei.get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares", [])
        latest_shares = self._latest(shares, form_prefix="10-")
        if latest_shares:
            evs.append(Evidence(
                category="fundamentals", field="shares_outstanding",
                value=latest_shares["val"], unit="shares", source="SEC EDGAR companyfacts (dei)",
                url=filing_base, as_of=date.fromisoformat(latest_shares["end"]),
            ))
        return evs

    async def _resolve_cik(self, client: httpx.AsyncClient, ticker: str) -> int | None:
        resp = await client.get(EDGAR_TICKER_MAP)
        resp.raise_for_status()
        for entry in resp.json().values():
            if entry["ticker"].upper() == ticker.upper():
                return int(entry["cik_str"])
        return None

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

    # ---------- news ----------

    async def get_news(self, ticker: str) -> list[Evidence]:
        key = os.environ.get("FINNHUB_API_KEY")
        if key:
            try:
                return await self._finnhub_news(ticker, key)
            except Exception:
                pass  # 降級到 yfinance
        return await asyncio.to_thread(self._yf_news_sync, ticker)

    async def _finnhub_news(self, ticker: str, key: str) -> list[Evidence]:
        to = date.today()
        frm = to - timedelta(days=14)
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                "https://finnhub.io/api/v1/company-news",
                params={"symbol": ticker, "from": frm.isoformat(), "to": to.isoformat(), "token": key},
            )
            resp.raise_for_status()
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
        except Exception:
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
