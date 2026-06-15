"""Raschke 專屬確定性技能（skill pass）：趨勢方向（均線排列）+ 回檔深度（對 SMA20 乖離）。

計算邏輯共用自 personas.skills_lib；此處宣告 requires 契約 + 套 @skill。缺欄位（歷史不足、
無即時報價 last_price）→ Runtime 記 not_evaluable，Raschke 在 SOP pass 誠實說節奏讀不出來。
"""

from __future__ import annotations

from cyber_sages.personas.skill import skill
from cyber_sages.personas.skills_lib import (
    pullback_depth_to_sma20 as _pullback_depth_to_sma20,
    trend_alignment_score as _trend_alignment_score,
)

trend_alignment_score = skill(
    requires=["sma_20", "sma_50", "sma_200"],
)(_trend_alignment_score)

pullback_depth_to_sma20 = skill(
    requires=["last_price", "sma_20"],
)(_pullback_depth_to_sma20)
