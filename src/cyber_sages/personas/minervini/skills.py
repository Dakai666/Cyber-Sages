"""Minervini 專屬確定性技能（skill pass）：趨勢排列（Stage-2 代理）+ 對 SMA50 延伸度。

計算邏輯共用自 personas.skills_lib；此處宣告 requires 契約 + 套 @skill。缺欄位（歷史不足
200 日無 SMA200、無即時報價 last_price）→ Runtime 記 not_evaluable，Minervini 在 SOP pass
誠實說「這檔還看不出 Stage-2 / 進場樞紐」。
"""

from __future__ import annotations

from cyber_sages.personas.skill import skill
from cyber_sages.personas.skills_lib import (
    extension_above_sma50 as _extension_above_sma50,
    trend_alignment_score as _trend_alignment_score,
)

trend_alignment_score = skill(
    requires=["sma_20", "sma_50", "sma_200"],
)(_trend_alignment_score)

extension_above_sma50 = skill(
    requires=["last_price", "sma_50"],
)(_extension_above_sma50)
