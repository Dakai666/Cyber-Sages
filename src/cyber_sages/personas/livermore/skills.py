"""Livermore 專屬確定性技能（skill pass）：最小阻力線＝均線排列分數。

計算邏輯共用自 personas.skills_lib（多位交易型大師都用趨勢排列）；此處只宣告 Livermore
對 canonical 欄位的 requires 契約 + 套上 @skill。缺均線欄位（歷史不足 30 日）→ Runtime
記 not_evaluable，Livermore 在 SOP pass 誠實說「這檔盤面我還讀不出來」。
"""

from __future__ import annotations

from cyber_sages.personas.skill import skill
from cyber_sages.personas.skills_lib import trend_alignment_score as _trend_alignment_score

trend_alignment_score = skill(
    requires=["sma_20", "sma_50", "sma_200"],
)(_trend_alignment_score)
