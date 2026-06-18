"""確定性技術指標的純單元測試——不打 API，只餵 pandas Series 驗算。

重點：US/TW 共用同一函式，差異只在 `trading_days`（W10 校準），所以特別測年化因子
能被參數正確切換，避免「改了一邊壞另一邊」。
"""

from datetime import date

import pandas as pd

from cyber_sages.data.indicators import compute_indicator_evidence, short_history_evidence


def test_short_history_evidence_marks_ipo():
    # S5：0 < n < 30 → 發 trading_days_available 標記（profile），供 audit 辨識 IPO 特例
    evs = short_history_evidence(3, url="http://x", source="yf")
    assert len(evs) == 1
    assert evs[0].category == "profile" and evs[0].field == "trading_days_available"
    assert evs[0].value == 3


def test_short_history_evidence_empty_when_no_data():
    # n=0（完全抓不到）回 []——那是真缺資料/抓取失敗，仍走 error，不偽裝成 IPO
    assert short_history_evidence(0, url="http://x", source="yf") == []


def test_short_history_evidence_empty_when_enough_history():
    # #61-1 契約守衛：n>=30 應回 []——足量歷史走 compute_indicator_evidence 出真指標，
    # 不靠 caller 夾 if len<30。邊界 30 即回空。
    assert short_history_evidence(30, url="http://x", source="yf") == []
    assert short_history_evidence(252, url="http://x", source="yf") == []
    assert len(short_history_evidence(29, url="http://x", source="yf")) == 1  # 29 仍標記


def _linear_closes(n: int, start: float = 100.0, step: float = 1.0) -> pd.Series:
    """嚴格遞增的收盤序列：SMA / 報酬率可手算，pct_change 標準差仍 > 0（可算波動）。"""
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.Series([start + i * step for i in range(n)], index=idx)


def _volatile_closes(n: int) -> pd.Series:
    """鋸齒序列（100/103 交替）：波動夠大，年化波動率四捨五入後仍精確；且有跌日供 RSI。"""
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.Series([100.0 + 3.0 * (i % 2) for i in range(n)], index=idx)


def _evmap(evs) -> dict[str, float]:
    return {e.field: e.value for e in evs}


def test_atr_emitted_with_high_low():
    # C7：給 high/low → 算 ATR(14) + ATR%。用固定日內幅度的序列可手算 ATR。
    # n=80（避開既有 return_3m 用 iloc[-63] 的下限，與本測試無關）。
    close = _linear_closes(80)              # 100..179，每日 +1
    high = close + 2.0                      # 每日高 = close+2
    low = close - 2.0                       # 每日低 = close-2
    m = _evmap(compute_indicator_evidence(
        close, high=high, low=low, as_of=date.today(), url="u", source="s"))
    # TR 每日 = max(high-low=4, |high-prev_close|=3, |low-prev_close|=1) = 4 → ATR(14)=4
    assert m["atr_14"] == 4.0
    # ATR% = 4 / 179 × 100
    assert m["atr_pct"] == round(4.0 / 179.0 * 100, 2)


def test_atr_skipped_without_high_low():
    # 不給 high/low（如 TW 缺 max/min）→ 不算 ATR，其餘指標照常
    m = _evmap(compute_indicator_evidence(
        _linear_closes(80), as_of=date.today(), url="u", source="s"))
    assert "atr_14" not in m and "atr_pct" not in m
    assert "sma_20" in m  # 其餘指標不受影響


def test_atr_skipped_on_length_mismatch():
    # #64-7 防呆：high/low/close 不等長（refactor 風險）→ 靜默略過 ATR，不引入錯位 TR。
    close = _linear_closes(80)
    high = (close + 2.0).iloc[:-1]   # 少一筆 → 長度不齊
    low = close - 2.0
    m = _evmap(compute_indicator_evidence(
        close, high=high, low=low, as_of=date.today(), url="u", source="s"))
    assert "atr_14" not in m and "atr_pct" not in m
    assert "sma_20" in m  # 其餘指標照常


def test_atr_skipped_on_index_misalignment():
    # #66-4 防呆：等長但 index 錯位（如 close 是 2024 dates、high 是 2025 dates）→ 略過 ATR。
    # combine 按 index 對齊會引入 NaN，僅比長度擋不住，故用 index.equals。
    close = _linear_closes(80)
    shifted_idx = pd.date_range("2025-06-01", periods=80, freq="D")  # 同長度、不同 index
    high = pd.Series((close + 2.0).values, index=shifted_idx)
    low = pd.Series((close - 2.0).values, index=shifted_idx)
    m = _evmap(compute_indicator_evidence(
        close, high=high, low=low, as_of=date.today(), url="u", source="s"))
    assert "atr_14" not in m and "atr_pct" not in m
    assert "sma_20" in m


def test_rs_vs_benchmark_emitted():
    # C7：個股 3 月報酬 − 大盤 3 月報酬。個股漲、大盤平 → RS 正。
    close = _linear_closes(80)                 # 100..179，3 月報酬 = 179/116-1
    bench = pd.Series([100.0] * 80, index=close.index)  # 大盤完全持平 → bench_3m=0
    m = compute_indicator_evidence(
        close, benchmark_close=bench, benchmark_name="S&P 500 (^GSPC)",
        as_of=date.today(), url="u", source="s")
    rs = next(e for e in m if e.field == "rs_vs_benchmark_3m_pct")
    expected = (close.iloc[-1] / close.iloc[-63] - 1) * 100  # 個股 3m，bench=0
    assert rs.value == round(expected, 2)
    assert "S&P 500 (^GSPC)" in rs.note  # #64-3：benchmark 名進 note


def test_rs_skipped_without_benchmark():
    m = _evmap(compute_indicator_evidence(
        _linear_closes(80), as_of=date.today(), url="u", source="s"))
    assert "rs_vs_benchmark_3m_pct" not in m


def test_too_short_returns_empty():
    assert compute_indicator_evidence(
        _linear_closes(29), as_of=date.today(), url=None, source="t"
    ) == []


def test_basic_indicator_values():
    evs = compute_indicator_evidence(
        _linear_closes(250), as_of=date.today(), url="u", source="s", price_unit="USD"
    )
    m = _evmap(evs)
    # 全部歸在 history 類別、value 皆四捨五入到兩位
    assert all(e.category == "history" for e in evs)
    assert all(round(float(e.value), 2) == e.value for e in evs)
    # 線性序列 100..349：可手算的錨點
    assert m["sma_20"] == 339.5                 # mean(330..349)
    assert m["high_52w"] == 349.0
    assert m["low_52w"] == 100.0
    assert m["pct_below_52w_high"] == 0.0       # 現價即為高點
    assert m["pct_above_52w_low"] == 249.0      # (349-100)/100*100
    # 長序列才有的欄位
    assert "sma_200" in m and "return_1y_pct" in m
    assert "macd_histogram" in m


def test_rsi_present_and_bounded_when_prices_oscillate():
    # 鋸齒序列才有跌日（loss>0），RSI 才會被算出且落在 0..100；
    # 長度需 ≥63（return_3m_pct 取 iloc[-63]）。
    close = _volatile_closes(80)
    m = _evmap(compute_indicator_evidence(close, as_of=date.today(), url=None, source="s"))
    assert "rsi_14" in m
    assert 0.0 <= m["rsi_14"] <= 100.0


def test_trading_days_scales_annualized_volatility():
    """同一序列，US(252) vs TW(245) 的年化波動率應差 sqrt(252/245) 倍——W10 核心。"""
    close = _volatile_closes(120)
    us = _evmap(compute_indicator_evidence(
        close, as_of=date.today(), url=None, source="s", trading_days=252))
    tw = _evmap(compute_indicator_evidence(
        close, as_of=date.today(), url=None, source="s", trading_days=245))
    ratio = us["volatility_30d_annualized_pct"] / tw["volatility_30d_annualized_pct"]
    assert abs(ratio - (252 / 245) ** 0.5) < 1e-3


def test_default_trading_days_is_252():
    close = _volatile_closes(120)
    default = _evmap(compute_indicator_evidence(
        close, as_of=date.today(), url=None, source="s"))
    explicit = _evmap(compute_indicator_evidence(
        close, as_of=date.today(), url=None, source="s", trading_days=252))
    assert default["volatility_30d_annualized_pct"] == explicit["volatility_30d_annualized_pct"]
