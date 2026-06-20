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
class EstimateProvider(Protocol):
    """分析師前瞻共識（forward EPS / 目標價 / 成長率）。獨立能力協議（同 ChipsProvider
    範式）：來源是二手聚合的估計值，與第一手 quote/fundamentals 分流到 estimate 類別。"""

    async def get_estimates(self, ticker: str) -> list[Evidence]: ...


@runtime_checkable
class MacroProvider(Protocol):
    """市場無關的總經背景（FRED 利率 / 殖利率曲線 / CPI / 就業）。

    刻意與 `MarketDataProvider` 分立而非併入其協議：`get_macro` 不綁 ticker、全市場
    抓一次——若硬塞進個股協議，每個 provider 都得多一個 no-op 方法，仍需獨立的 FRED
    來源，反而更亂。沿用 `ChipsProvider` 的「額外能力＝獨立 runtime_checkable 協議」範式。
    """

    market: str  # 來源標籤（如 "MACRO"）；列入協議，未來加第二個總經源不會漏設而漂移

    async def get_macro(self) -> list[Evidence]: ...


# 真正異源（與 Yahoo 系 feed 不相關）的報價來源標記。跨源把關只有「兩條不相關 feed」才
# 有意義（Spec F S2 correlated-failure：兩條 Yahoo 路徑會一起 stale 而漏接）。集中定義避免
# 散落各處的命名巧合——us_stocks 發 evidence 的 source 字串、audit 的獨立性判斷共用同一份
# 真相；未來加 IEX 等新異源只需在此登記一行（#67-4）。
_INDEPENDENT_SOURCE_MARKERS = ("finnhub",)


def is_independent_source(source: str | None) -> bool:
    """evidence 的 source 是否標示「真正異源」（非 Yahoo 系）的報價來源。子字串、大小寫不敏感。
    供跨源把關判斷「手上的第二價格源是否真獨立」，不靠 field 命名巧合（如 last_price_finnhub）。"""
    s = (source or "").lower()
    return any(marker in s for marker in _INDEPENDENT_SOURCE_MARKERS)


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


class _MergedMacroProvider:
    """多個總經來源的合併視圖——讓 pipeline 仍只面對單一 `MacroProvider.get_macro()`。
    各源獨立 best-effort：任一源拋例外只丟棄該源結果、不拖垮其餘（與 collect 層 gather 同調）。"""

    market = "MACRO"

    def __init__(self, providers: list[MacroProvider]) -> None:
        self._providers = providers

    async def get_macro(self) -> list[Evidence]:
        import asyncio

        results = await asyncio.gather(
            *(p.get_macro() for p in self._providers), return_exceptions=True)
        evs: list[Evidence] = []
        for r in results:
            if not isinstance(r, BaseException):
                evs.extend(r)
        return evs


def make_macro_provider(market: str | None = None) -> MacroProvider | None:
    """總經來源工廠：合併所有可用源——FRED（全市場：利率/殖利率曲線/CPI/就業 + TWD/USD FX）
    + 台灣專屬本土源（**僅 TW 市場併入**，對美股無關故不污染）：央行重貼現率（政策利率）
    與主計總處 CPI 年增率（通膨）。

    把「可用性」收斂到工廠：無可用源回 None、單一源直接回該 provider、多源回合併視圖。
    呼叫端只需判斷 `is not None` 並呼叫 `get_macro()`，與各能力協議的偵測語意一致。"""
    from cyber_sages.data.macro import FredMacroProvider

    providers: list[MacroProvider] = []
    if FredMacroProvider.available():
        providers.append(FredMacroProvider())
    if market == "TW":
        from cyber_sages.data.tw_macro import TWCpiProvider, TWMacroProvider

        if TWMacroProvider.available():
            providers.append(TWMacroProvider())
        if TWCpiProvider.available():
            providers.append(TWCpiProvider())
    if not providers:
        return None
    return providers[0] if len(providers) == 1 else _MergedMacroProvider(providers)
