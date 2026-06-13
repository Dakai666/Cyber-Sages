"""分析師前瞻共識（estimate 類別）——US 與 TW 共用，來源為 yfinance `.info` 聚合。

刻意與第一手 quote/fundamentals 分流：這些是分析師預估的「共識值」，二手且前瞻，
evidence 一律標 source 含「(estimate)」、走 estimate 類別。下游 cite-check 照常可引用，
audit 不對 estimate 做 freshness error（見 data_audit 與 decision 4）。
"""

from __future__ import annotations

import logging
from datetime import date

from cyber_sages.data.evidence import Evidence

logger = logging.getLogger(__name__)

SOURCE = "yfinance analyst consensus (estimate)"


def fetch_estimates(symbols: list[str], *, currency: str, url: str) -> list[Evidence]:
    """依序試 symbols（如 ['2330.TW','2330.TWO']），第一個取得 info 的就用。

    回傳 estimate 類別 Evidence；任何欄位缺值即略過該欄位（寧缺勿造）。同步函式，
    由 provider 以 asyncio.to_thread 包起來。"""
    import yfinance as yf

    info: dict = {}
    for sym in symbols:
        try:
            info = yf.Ticker(sym).info or {}
        except Exception as e:
            logger.warning("estimates: yfinance .info failed for %s: %s", sym, e)
            info = {}
        if info.get("numberOfAnalystOpinions") or info.get("forwardEps"):
            break  # 拿到有意義的共識資料即停
    if not info:
        return []

    today = date.today()

    def ev(field, value, unit, note) -> Evidence:
        return Evidence(
            category="estimate", field=field, value=value, unit=unit,
            source=SOURCE, url=url, as_of=today, note=note,
        )

    evs: list[Evidence] = []
    fe = info.get("forwardEps") if info.get("forwardEps") is not None else info.get("epsForward")
    if fe is not None:
        evs.append(ev("forward_eps", round(float(fe), 3), f"{currency}/share",
                      "分析師共識前瞻 EPS（next FY）；前瞻估計值，非實際財報"))
    if info.get("targetMeanPrice") is not None:
        evs.append(ev("target_mean_price", round(float(info["targetMeanPrice"]), 2), currency,
                      "分析師平均目標價（共識估計值）"))
    if info.get("earningsGrowth") is not None:
        evs.append(ev("earnings_growth_est_pct", round(float(info["earningsGrowth"]) * 100, 2), "%",
                      "分析師共識預估盈餘成長（前瞻；Lynch PEG / 成長分析可用）"))
    if info.get("revenueGrowth") is not None:
        evs.append(ev("revenue_growth_est_pct", round(float(info["revenueGrowth"]) * 100, 2), "%",
                      "分析師共識預估營收成長（前瞻）"))
    if info.get("numberOfAnalystOpinions") is not None:
        evs.append(ev("analyst_count", int(info["numberOfAnalystOpinions"]), "analysts",
                      "貢獻共識的分析師人數（估計的可信度脈絡）"))
    if info.get("recommendationKey"):
        evs.append(ev("analyst_recommendation", str(info["recommendationKey"]), None,
                      "分析師共識評等（strong_buy/buy/hold/sell…）"))
    return evs
