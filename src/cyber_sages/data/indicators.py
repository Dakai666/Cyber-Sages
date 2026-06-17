"""確定性技術指標 — 從一條收盤價序列算出指標 Evidence。

刻意不讓 LLM 算數：所有指標（SMA / 報酬率 / 波動 / RSI / MACD）都在這裡用程式算，
美股與台股 provider 共用同一套邏輯，只差價格單位與來源字串。
"""

from __future__ import annotations

from datetime import date

from cyber_sages.data.evidence import Evidence


def short_history_evidence(n_days: int, *, url: str | None, source: str) -> list[Evidence]:
    """歷史不足以算技術指標（< 30 交易日，疑新上市 IPO）時的標記 evidence。

    讓 audit 能把「缺技術指標」辨識為 IPO/新上市特例（降為 warning + 明說承認），而非當成
    資料錯誤而降級（Spec F S5 / D1：歷史過短屬特例，不 fatal、不腰斬）。n_days<=0（完全抓不到）
    回 []——那是真的缺資料/抓取失敗，仍走 error。"""
    if n_days <= 0:
        return []
    return [Evidence(
        category="profile", field="trading_days_available", value=n_days, unit="days",
        source=source, url=url, note="歷史交易日數不足 30（疑新上市），技術指標無法計算",
    )]


def compute_indicator_evidence(
    close,                      # pandas.Series of closing prices, 時間升冪
    *,
    high=None,                  # pandas.Series 最高價（可選；給了才算 ATR）
    low=None,                   # pandas.Series 最低價
    benchmark_close=None,       # pandas.Series 大盤收盤（可選；給了才算相對強弱 RS）
    as_of: date,
    url: str | None,
    source: str,
    price_unit: str = "USD",
    trading_days: int = 252,    # 年化波動率用的交易日數：美股 252、台股 245（W10 校準）
) -> list[Evidence]:
    """至少需要 ~30 個交易日；不足回傳空 list。

    `trading_days` 控制波動率年化的根號因子。美股全年約 252 個交易日，台股因農曆年
    等假期約 245，沿用 252 會略高估台股波動率，故由各 provider 傳入正確值。"""
    if len(close) < 30:
        return []

    def ev(field: str, value: float, unit: str | None = None,
           note: str | None = None) -> Evidence:
        return Evidence(
            category="history", field=field, value=round(float(value), 2),
            unit=unit, source=source, url=url, as_of=as_of, note=note,
        )

    last = close.iloc[-1]
    evs = [
        ev("sma_20", close.rolling(20).mean().iloc[-1], price_unit),
        ev("sma_50", close.rolling(50).mean().iloc[-1], price_unit),
        ev("return_1m_pct", (close.iloc[-1] / close.iloc[-21] - 1) * 100, "%"),
        ev("return_3m_pct", (close.iloc[-1] / close.iloc[-63] - 1) * 100, "%"),
        ev("high_52w", close.max(), price_unit),
        ev("low_52w", close.min(), price_unit),
        # 距高/低點百分比：確定性算好讓分析師直接引用，避免自己算「距高點 X%」時
        # 把 (高-現)/高 寫成正值卻對不上驗證層的帶號變動率（源頭給值，不放寬驗證）。
        ev("pct_below_52w_high", (close.max() - last) / close.max() * 100, "%",
           note="距 52 週高點（正=低於高點）"),
        ev("pct_above_52w_low", (last - close.min()) / close.min() * 100, "%",
           note="距 52 週低點（正=高於低點）"),
        ev("volatility_30d_annualized_pct",
           close.pct_change().tail(30).std() * (trading_days ** 0.5) * 100, "%"),
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

    # ATR(14)（C7）：短線大師用來定停損距離與波動尺度。需 high/low（OHLC），給了才算。
    # True Range = max(high-low, |high-prev_close|, |low-prev_close|)；無 pandas import，
    # 用 Series.combine 逐元素取 max。emit 絕對 ATR 與 ATR%（佔現價，跨標的可比的波動尺度）。
    if high is not None and low is not None and len(close) >= 15:
        prev_close = close.shift(1)
        tr = (high - low).combine((high - prev_close).abs(), max) \
                         .combine((low - prev_close).abs(), max)
        atr = tr.rolling(14).mean().iloc[-1]
        if atr == atr and last:  # atr==atr 濾 NaN
            evs.append(ev("atr_14", atr, price_unit, note="14 日平均真實波幅（停損距離參考）"))
            evs.append(ev("atr_pct", atr / last * 100, "%",
                          note="ATR 佔現價百分比（波動尺度，跨標的可比；停損常取 1.5-3×ATR）"))

    # 相對強弱 RS（C7）：個股 3 月報酬 − 大盤 3 月報酬。正＝跑贏大盤（Minervini/動能選股核心）。
    # 各自用自身序列的 iloc[-63]（≈3 月）算報酬，避免兩序列交易日對不齊；需大盤 ≥64 筆。
    if benchmark_close is not None and len(benchmark_close) >= 64 and len(close) >= 64:
        stock_3m = (close.iloc[-1] / close.iloc[-63] - 1) * 100
        bench_3m = (benchmark_close.iloc[-1] / benchmark_close.iloc[-63] - 1) * 100
        evs.append(ev("rs_vs_benchmark_3m_pct", stock_3m - bench_3m, "%",
                      note="3 月相對強弱：個股報酬 − 大盤報酬（正=跑贏大盤）"))
    return evs
