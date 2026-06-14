"""多年期基本面衍生欄位（roe_5y_avg / gross_margin_trend_5y / earnings_stability_5y）。

跨市場共用的**純計算**：各 provider 自行從第一手來源抽出「逐年對齊」的序列——US 由 SEC
companyfacts 的多年 10-K、TW 由 FinMind 季度聚合成年度——再交給此處算出多年指標，emit 為
同名 canonical evidence（單位口徑與單期 roe_pct / gross_margin_pct 一致：百分比即 35.0=35%）。

這是 Spec E1（PR2）「補齊 pilot 規則所需多年欄位」的落地：Buffett/Munger 招牌規則要看
「5 年 ROE 平均」「毛利越/趨勢」「盈餘穩定性」這類跨年訊號，單期值看不出護城河變寬變窄。

兩條一貫鐵律：
- **絕不讓 LLM 算數**：公式與逐年序列寫進 note，可被 cite-check 溯源。
- **寧缺勿錯**：序列年數 < MIN_YEARS 則該指標不發；下游 persona rule/skill 會記為
  not_evaluable（Spec E），不假裝有值。
"""

from __future__ import annotations

import math
import statistics
from datetime import date

from cyber_sages.data.evidence import Evidence

MAX_YEARS = 5   # 取最近這麼多個會計年度
MIN_YEARS = 3   # 不足這麼多年則該指標不發（趨勢/平均/穩定度都需要足夠樣本）

Series = list[tuple[date, float]]


def _recent(series: Series) -> Series:
    # 先濾掉 NaN/Inf（C2）：上游某年壞值（除零殘留、髒資料）不能混進平均/斜率/σ——
    # 否則 earnings_stability_5y 會假報 1.0 等危險訊號，與「寧缺勿錯」相反。壞年當缺年丟棄，
    # 剩餘不足 MIN_YEARS 則該指標不發（→ 下游 not_evaluable）。
    finite = [(d, v) for d, v in series if math.isfinite(v)]
    return sorted(finite)[-MAX_YEARS:]


def _ols_slope(ys: list[float]) -> float:
    """最小平方法線性斜率（x = 0,1,2…），單位為 y/年。n>=2 時分母必為正。"""
    n = len(ys)
    mx = (n - 1) / 2
    my = sum(ys) / n
    den = sum((x - mx) ** 2 for x in range(n))
    return sum((x - mx) * (y - my) for x, y in zip(range(n), ys)) / den


def _span(series: Series) -> str:
    return f"{series[0][0].year}–{series[-1][0].year}"


def multiyear_fundamentals(
    *,
    roe_pct_series: Series,
    gross_margin_pct_series: Series,
    net_income_series: Series,
    source: str,
    url: str | None,
) -> list[Evidence]:
    """從逐年對齊序列算出多年指標。各序列已是 (會計年度結束日, 該年值)，由 provider 提供。"""
    out: list[Evidence] = []

    roe = _recent(roe_pct_series)
    if len(roe) >= MIN_YEARS:
        avg = sum(v for _, v in roe) / len(roe)
        detail = ", ".join(f"{d.year}={v:.1f}%" for d, v in roe)
        out.append(Evidence(
            category="fundamentals", field="roe_5y_avg", value=round(avg, 2), unit="%",
            source=source, url=url, as_of=roe[-1][0],
            note=f"近{len(roe)}年 ROE 平均（{_span(roe)}）：{detail}",
        ))

    gm = _recent(gross_margin_pct_series)
    if len(gm) >= MIN_YEARS:
        slope = _ols_slope([v for _, v in gm])
        detail = ", ".join(f"{d.year}={v:.1f}%" for d, v in gm)
        out.append(Evidence(
            category="fundamentals", field="gross_margin_trend_5y", value=round(slope, 3),
            unit="%/yr", source=source, url=url, as_of=gm[-1][0],
            note=f"近{len(gm)}年毛利率線性趨勢（OLS 斜率，%點/年；>0=變寬）（{_span(gm)}）：{detail}",
        ))

    ni = _recent(net_income_series)
    if len(ni) >= MIN_YEARS:
        vals = [v for _, v in ni]
        mean = statistics.fmean(vals)
        # 穩定度 = max(0, 1 − σ/μ)（變異係數的補數），1=完全穩定；均值≤0（持續虧損/虧損年
        # 拖累）視為不穩 → 0。LLM/規則只讀此 0~1 分數，門檻在 persona rule 設。
        stability = 0.0 if mean <= 0 else max(0.0, 1 - statistics.pstdev(vals) / mean)
        detail = ", ".join(f"{d.year}={v:,.0f}" for d, v in ni)
        out.append(Evidence(
            category="fundamentals", field="earnings_stability_5y", value=round(stability, 3),
            unit=None, source=source, url=url, as_of=ni[-1][0],
            note=f"近{len(ni)}年淨利穩定度 = max(0, 1 − σ/μ)（1=最穩）（{_span(ni)}）：{detail}",
        ))

    return out
