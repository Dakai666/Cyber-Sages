"""可插拔資料來源介面 — 之後台股(FinMind)/Crypto(CoinGecko)各實作一個。"""

from __future__ import annotations

from typing import Protocol

from cyber_sages.data.evidence import Evidence


class MarketDataProvider(Protocol):
    market: str  # "US", "TW", "CRYPTO"

    async def get_quote(self, ticker: str) -> list[Evidence]: ...

    async def get_history(self, ticker: str) -> list[Evidence]: ...

    async def get_fundamentals(self, ticker: str) -> list[Evidence]: ...

    async def get_news(self, ticker: str) -> list[Evidence]: ...
