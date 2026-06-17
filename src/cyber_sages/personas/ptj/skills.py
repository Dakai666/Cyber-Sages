"""PTJ 專屬確定性技能（skill pass）——把「現價離 200 日均線多遠」算成單一衍生欄位。

DSL 只能比「單欄 vs 常數」，表達不了 `last_price < sma_200` 這種雙欄比較；故先用 skill
確定性算出 price_vs_sma_200_pct（>0 在年線之上、<0 跌破），rules.yaml 再對 0 比較。
缺 last_price / sma_200（極少見）→ Runtime 記 not_evaluable，PTJ 在 SOP 誠實說看不到年線。
"""

from __future__ import annotations

from cyber_sages.personas.skill import SkillResult, skill


@skill(requires=["last_price", "sma_200"])
def price_vs_sma_200_pct(ev) -> SkillResult:
    last, sma = ev["last_price"], ev["sma_200"]
    return SkillResult(
        value=(last / sma - 1) * 100,
        formula="(last_price / sma_200 - 1) * 100",
        unit="%",
    )
