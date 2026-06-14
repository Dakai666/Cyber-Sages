"""Buffett 專屬確定性技能（skill pass）。

計算邏輯共用自 personas.skills_lib（多位大師都用 owner earnings 框架）；此處只宣告
Buffett 對 canonical 欄位的 requires 契約 + 套上 @skill。缺欄位（如台股無 capex/D&A、
無 market_cap）→ Runtime 記 not_evaluable，Buffett 在 SOP pass 誠實說「這次看不到」。
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
