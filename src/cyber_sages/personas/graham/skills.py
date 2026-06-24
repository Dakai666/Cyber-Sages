"""Graham 專屬確定性技能（skill pass）——資產背書與清算價值的確定性計算。

Graham 的安全邊際靠資產面量化：net-net 清算價對市值的折溢價、流動比。兩者都是「雙欄比較／
比率」，DSL 的 {field, op, value} 表達不了（只能單欄對常數），故先用 skill 確定性算成單一
衍生欄位，rules.yaml 再對常數判定（見 forge-sage skill §5 rule↔skill pattern）。

缺欄位（台股無 market_cap、或無年度流動資產）→ Runtime 記 not_evaluable，Graham 在 SOP
誠實說「這次看不到清算價折價／流動比」，不假裝算得出。
"""

from __future__ import annotations

from cyber_sages.personas.skill import SkillResult, skill


@skill(requires=["net_net_value", "market_cap"])
def net_net_discount_pct(ev) -> SkillResult:
    """net-net 清算價對市值的折溢價（%）= (net_net_value − market_cap) / market_cap × 100。

    net_net_value = 流動資產 − 總負債（清算保守估計）。
    > 0：市值低於清算價＝買進低於清算價值的資產（Graham 最深的安全邊際，罕見）；
    < 0：市值高於清算價（絕大多數現代股票的常態）。
    """
    nn, mc = ev["net_net_value"], ev["market_cap"]
    return SkillResult(
        value=(nn - mc) / mc * 100,
        formula="(net_net_value − market_cap) / market_cap × 100",
        unit="%",
    )


@skill(requires=["current_assets_annual", "current_liabilities_annual"])
def current_ratio(ev) -> SkillResult:
    """流動比 = 流動資產 / 流動負債。Graham 防禦型準則要求 ≥ 2（短期清償能力的安全墊）。

    供 SOP pass 引用評估財務強度；未下成 hard rule（清算/負債紅線已由 net-net 與
    debt_to_equity 規則涵蓋），但登錄為可溯源 private evidence 讓 Graham 在流程中引用。
    """
    ca, cl = ev["current_assets_annual"], ev["current_liabilities_annual"]
    return SkillResult(
        value=ca / cl,
        formula="current_assets_annual / current_liabilities_annual",
        unit="x",
    )
