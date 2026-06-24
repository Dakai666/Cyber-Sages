"""Burry 專屬確定性技能（skill pass）——自由現金流殖利率（深度價值的現金回報代理）。

Burry 的便宜不看帳面 EPS，看「這門生意每年吐多少真實現金、相對我付的價」。FCF 殖利率
= free_cash_flow / market_cap，是雙欄相除，DSL 表達不了（只能單欄對常數），故先用 skill
確定性算成單一衍生欄位，rules.yaml 再對常數判定（見 forge-sage skill §5 rule↔skill pattern）。

缺 market_cap（台股無）或 free_cash_flow → Runtime 記 not_evaluable，Burry 在 SOP 誠實說
「這次算不出 FCF 殖利率」，改以資產負債表現實定性判斷。
"""

from __future__ import annotations

from cyber_sages.personas.skill import SkillResult, skill


@skill(requires=["free_cash_flow_annual", "market_cap"])
def fcf_yield(ev) -> SkillResult:
    """自由現金流殖利率（%）= free_cash_flow_annual / market_cap × 100。

    越高＝同樣市值買到越多真實現金（深度價值）；負值＝燒現金（規則端只在 > 門檻才觸發 floor，
    負值天然不觸發，故不需額外 guard）。
    """
    fcf, mc = ev["free_cash_flow_annual"], ev["market_cap"]
    return SkillResult(
        value=fcf / mc * 100,
        formula="free_cash_flow_annual / market_cap × 100",
        unit="%",
    )
