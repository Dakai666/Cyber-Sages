"""可插拔資料來源介面 + 市場路由。

各市場各實作一個 provider（美股 yfinance/SEC、台股 FinMind…），共用同一介面；
台股額外有 get_chips（籌碼面），管線以 hasattr 偵測後選用。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from cyber_sages.data.evidence import Evidence


class MarketDataProvider(Protocol):
    market: str  # "US", "TW", "CRYPTO"

    async def get_quote(self, ticker: str) -> list[Evidence]: ...

    async def get_history(self, ticker: str) -> list[Evidence]: ...

    async def get_fundamentals(self, ticker: str) -> list[Evidence]: ...

    async def get_news(self, ticker: str) -> list[Evidence]: ...


@runtime_checkable
class ChipsProvider(Protocol):
    """台股特有：三大法人買賣超 / 融資融券。"""

    async def get_chips(self, ticker: str) -> list[Evidence]: ...


def detect_market(ticker: str) -> str:
    """從代號推斷市場。台股：`.TW`/`.TWO` 字尾，或 4-6 位純數字（含 ETF 00xxxx）。"""
    t = ticker.upper().strip()
    if t.endswith(".TW") or t.endswith(".TWO"):
        return "TW"
    core = t.split(".")[0]
    # 台股代號為 4-6 位數字，可帶單一字母尾碼（槓桿 ETF 00631L、特別股 2841A）
    digits = core[:-1] if core[-1:].isalpha() else core
    if digits.isdigit() and 4 <= len(digits) <= 6:
        return "TW"
    return "US"


def is_tw_etf(ticker: str) -> bool:
    """台股 ETF 代號以 00 開頭（0050 / 00878 / 00631L / 00403A）。
    ETF 無個股損益表，財報相關要求需據此豁免。"""
    core = ticker.upper().split(".")[0]
    return detect_market(ticker) == "TW" and core.startswith("00")


def detect_instrument(ticker: str) -> str:
    return "etf" if is_tw_etf(ticker) else "stock"


def make_provider(market: str) -> MarketDataProvider:
    if market == "TW":
        from cyber_sages.data.tw_stocks import TWStockProvider
        return TWStockProvider()
    from cyber_sages.data.us_stocks import USStockProvider
    return USStockProvider()
