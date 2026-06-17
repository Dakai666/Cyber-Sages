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
