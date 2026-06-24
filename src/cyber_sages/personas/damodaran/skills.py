"""Damodaran 專屬確定性技能（skill pass）——現價相對同業 multiple 的折溢價。

Damodaran 的相對估值錨在他自己維護的產業 multiples（reference 類別）。現價 P/E 相對產業
P/E 的折溢價 = (trailing_pe − industry_pe_trailing) / industry_pe_trailing，是雙欄相除，
DSL 表達不了，故先用 skill 算成單一衍生欄位，rules.yaml 再對常數判定（見 forge-sage §5）。

industry_pe_trailing 為 US-only（Damodaran 產業 CSV，sector 映射）→ 台股缺此欄位時 Runtime
記 not_evaluable，Damodaran 在 SOP 改以現金流內在價值與隱含預期定性判斷、誠實說明降級。
"""

from __future__ import annotations

from cyber_sages.personas.skill import SkillResult, skill


@skill(requires=["trailing_pe", "industry_pe_trailing"])
def pe_vs_industry_pct(ev) -> SkillResult:
    """現價 P/E 相對產業 P/E 的折溢價（%）= (trailing_pe − industry_pe_trailing) / industry_pe_trailing × 100。

    < 0：現價低於同業 multiple（相對便宜）；> 0：相對溢價。
    規則端以 trailing_pe > 0 guard，避免虧損股的負 P/E 給出假性「折價」。
    """
    pe, ind = ev["trailing_pe"], ev["industry_pe_trailing"]
    return SkillResult(
        value=(pe - ind) / ind * 100,
        formula="(trailing_pe − industry_pe_trailing) / industry_pe_trailing × 100",
        unit="%",
    )
