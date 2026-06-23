"""Lynch 專屬確定性技能（skill pass）——PEG 比率的確定性計算。

Lynch 的招牌數字 PEG = P/E ÷ 盈餘成長率，是「雙欄相除」：DSL 的 {field, op, value} 只能單欄
對常數、表達不了，故先用 skill 算成單一衍生欄位 peg_ratio，rules.yaml 再對常數判定
（見 forge-sage skill §5 rule↔skill pattern）。

欄位：trailing_pe（quote，yfinance）÷ earnings_growth_est_pct（estimate，分析師共識年成長 %）。
缺任一（台股常無 forward 共識成長、或無 P/E）→ Runtime 記 not_evaluable，Lynch 在 SOP 誠實說
「這次算不出 PEG」，改以盈餘軌跡定性判斷。

口徑：earnings_growth_est_pct 以百分點計（25.0＝25%），故 PEG = P/E / 25——P/E 25、成長 25%
得 PEG 1.0（公平定價），與 Lynch 原始定義一致。
"""

from __future__ import annotations

from cyber_sages.personas.skill import SkillResult, skill


@skill(requires=["trailing_pe", "earnings_growth_est_pct"])
def peg_ratio(ev) -> SkillResult:
    """PEG = trailing P/E ÷ 預估盈餘年成長率（%）。< 1 便宜、≈1 公平、> 2 為完美定價。

    註：負成長（earnings_growth_est_pct < 0）會得負 PEG——規則端以 earnings_growth > 0 與
    peg > 0 雙重把關，避免「虧損衰退」被誤讀成「便宜」。
    """
    pe, growth = ev["trailing_pe"], ev["earnings_growth_est_pct"]
    return SkillResult(
        value=pe / growth,
        formula="trailing_pe / earnings_growth_est_pct",
        unit="x",
    )
