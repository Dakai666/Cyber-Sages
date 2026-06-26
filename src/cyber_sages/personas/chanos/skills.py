"""Chanos 專屬確定性技能（skill pass）——盈餘品質的鑑識比率：營業現金流 / 淨利。

Chanos 的招牌是「信現金流量表、不信損益表」：帳面獲利若不被營業現金流支撐（OCF 遠低於 NI），
就是應計項目灌水（aggressive revenue recognition、應收帳款堆積、channel stuffing）的鑑識紅旗。
OCF/NI 是雙欄比值，DSL 只能單欄對常數、表達不了，故先用 skill 算成單一衍生欄位，rules.yaml
再對常數判定（見 forge-sage skill §5 rule↔skill pattern）。

口徑：健康公司 OCF 通常 ≥ NI（折舊攤銷加回），故 OCF/NI ≥ 1 為常態；< 1 代表獲利的現金含量
偏低，明顯偏低（規則設 < 0.8）才當紅旗——刻意保守，避免誤傷高成長股的應收帳款時間差
（如 NVDA 0.855：高成長下的營運資金佔用，非造假）。NI ≤ 0 時比值無意義，由規則的 NI>0 guard 排除。

缺 operating_cash_flow_annual 或 net_income_annual（台股 FinMind 現金流量 YTD 去累計後年度口徑
可得，但部分情形缺）→ Runtime 記 not_evaluable，Chanos 在 SOP 改以單季現金流 vs 淨利定性比對。
"""

from __future__ import annotations

from cyber_sages.personas.skill import SkillResult, skill


@skill(requires=["operating_cash_flow_annual", "net_income_annual"])
def ocf_to_ni_ratio(ev) -> SkillResult:
    """盈餘的現金含量 = 營業現金流 / 淨利。≥ 1 健康；明顯 < 1（規則設 < 0.8）為應計灌水紅旗。"""
    ocf, ni = ev["operating_cash_flow_annual"], ev["net_income_annual"]
    return SkillResult(
        value=ocf / ni,
        formula="operating_cash_flow_annual / net_income_annual",
        unit="x",
    )
