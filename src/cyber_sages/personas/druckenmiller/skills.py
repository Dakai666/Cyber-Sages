"""Druckenmiller 專屬確定性技能（skill pass）：均線排列分數＝趨勢方向的客觀代理。

計算邏輯共用自 personas.skills_lib（多位交易型大師都用趨勢排列）；此處只宣告 Druckenmiller
對 canonical 欄位的 requires 契約 + 套上 @skill。順勢是他的核心，trend_alignment_score
（−2..+2）把「多/空頭排列」量化成單一衍生欄位，供 rules 對常數判定（DSL 不能比兩欄）。

缺均線欄位（歷史不足 30 日）→ Runtime 記 not_evaluable，Druckenmiller 在 SOP 誠實說
「這檔盤面我還讀不出趨勢」。US/TW 皆可（sma_* 由 indicators 兩市場共用）。
"""

from __future__ import annotations

from cyber_sages.personas.skill import skill
from cyber_sages.personas.skills_lib import trend_alignment_score as _trend_alignment_score

trend_alignment_score = skill(
    requires=["sma_20", "sma_50", "sma_200"],
)(_trend_alignment_score)
