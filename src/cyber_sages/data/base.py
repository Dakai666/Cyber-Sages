"""可插拔資料來源介面 + 市場路由。

各市場各實作一個 provider（美股 yfinance/SEC、台股 FinMind…），共用同一 `MarketDataProvider`
介面。額外能力走各自的 runtime_checkable 協議，管線以 `isinstance` 偵測後選用：
- 台股 `ChipsProvider`（三大法人 / 融資融券，綁個股 ticker）
- 市場無關的 `MacroProvider`（FRED 總經，不綁 ticker、全市場抓一次）
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from cyber_sages.data.evidence import Evidence


@runtime_checkable
class MarketDataProvider(Protocol):
    market: str  # "US", "TW", "CRYPTO"

    async def get_quote(self, ticker: str) -> list[Evidence]: ...

    async def get_history(self, ticker: str) -> list[Evidence]: ...

    async def get_fundamentals(self, ticker: str) -> list[Evidence]: ...

    async def get_news(self, ticker: str) -> list[Evidence]: ...


@runtime_checkable
class ChipsProvider(Protocol):
    """台股特有：三大法人買賣超 / 融資融券（綁個股 ticker）。"""

    async def get_chips(self, ticker: str) -> list[Evidence]: ...


@runtime_checkable
class MacroProvider(Protocol):
    """市場無關的總經背景（FRED 利率 / 殖利率曲線 / CPI / 就業）。

    刻意與 `MarketDataProvider` 分立而非併入其協議：`get_macro` 不綁 ticker、全市場
    抓一次——若硬塞進個股協議，每個 provider 都得多一個 no-op 方法，仍需獨立的 FRED
    來源，反而更亂。沿用 `ChipsProvider` 的「額外能力＝獨立 runtime_checkable 協議」範式。
    """

    async def get_macro(self) -> list[Evidence]: ...


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


def make_macro_provider() -> MacroProvider | None:
    """總經來源工廠：有 FRED_API_KEY 才回 provider，否則 None（管線據此跳過總經、如實標註）。

    把「可用性」收斂到工廠，呼叫端只需判斷 `is not None`，與各能力協議的偵測語意一致。"""
    from cyber_sages.data.macro import FredMacroProvider
    return FredMacroProvider() if FredMacroProvider.available() else None
