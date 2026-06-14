"""Munger 專屬確定性技能（skill pass）。

Munger 與 Buffett 共用 owner earnings 分析框架（計算來自 personas.skills_lib），差異在
rules / SOP / 判斷風格而非工具本身——他用同樣的尺，量出不同的結論。requires 缺欄位 →
not_evaluable，Munger 在 SOP pass 會直說資料不足、不硬湊。
"""

from __future__ import annotations

from cyber_sages.personas.skill import skill
from cyber_sages.personas.skills_lib import (
    owner_earnings as _owner_earnings,
    owner_earnings_yield as _owner_earnings_yield,
)

owner_earnings = skill(
    requires=["net_income_annual", "depreciation_amortization_annual", "capex_annual"],
)(_owner_earnings)

owner_earnings_yield = skill(
    requires=["net_income_annual", "depreciation_amortization_annual", "capex_annual",
              "market_cap"],
)(_owner_earnings_yield)
