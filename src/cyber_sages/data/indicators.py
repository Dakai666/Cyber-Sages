"""確定性技術指標 — 從一條收盤價序列算出指標 Evidence。

刻意不讓 LLM 算數：所有指標（SMA / 報酬率 / 波動 / RSI / MACD）都在這裡用程式算，
美股與台股 provider 共用同一套邏輯，只差價格單位與來源字串。
"""

from __future__ import annotations

from datetime import date

from cyber_sages.data.evidence import Evidence


def compute_indicator_evidence(
    close,                      # pandas.Series of closing prices, 時間升冪
    *,
    as_of: date,
    url: str | None,
    source: str,
    price_unit: str = "USD",
) -> list[Evidence]:
    """至少需要 ~30 個交易日；不足回傳空 list。"""
    if len(close) < 30:
        return []

    def ev(field: str, value: float, unit: str | None = None,
           note: str | None = None) -> Evidence:
        return Evidence(
            category="history", field=field, value=round(float(value), 2),
            unit=unit, source=source, url=url, as_of=as_of, note=note,
        )

    evs = [
        ev("sma_20", close.rolling(20).mean().iloc[-1], price_unit),
        ev("sma_50", close.rolling(50).mean().iloc[-1], price_unit),
        ev("return_1m_pct", (close.iloc[-1] / close.iloc[-21] - 1) * 100, "%"),
        ev("return_3m_pct", (close.iloc[-1] / close.iloc[-63] - 1) * 100, "%"),
        ev("high_52w", close.max(), price_unit),
        ev("low_52w", close.min(), price_unit),
        ev("volatility_30d_annualized_pct",
           close.pct_change().tail(30).std() * (252 ** 0.5) * 100, "%"),
    ]
    if len(close) >= 200:
        evs.append(ev("sma_200", close.rolling(200).mean().iloc[-1], price_unit))
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
