"""共用 skill 計算（Persona Pack 之間可重用的確定性計算）。

E1 實作決議 (a)：每個 Pack 的 `skills.py` 各自一份、自行宣告 `requires` 與 `@skill`，
但共用的計算邏輯（owner earnings 等多位大師都用）放這裡當 importable helper，避免重複。

這裡只放「純計算函式」（`fn(ev) -> SkillResult`），不掛 `@skill` 裝飾器——由各 Pack 的
skills.py 套上 `@skill(requires=...)`，因為 requires 的 canonical 欄位名是 per-pack 的契約。
"""

from __future__ import annotations

from cyber_sages.personas.skill import EvidenceAccessor, SkillResult


def owner_earnings(ev: EvidenceAccessor) -> SkillResult:
    """Buffett owner earnings：稅後淨利 + 折舊攤銷 − 資本支出（年度口徑）。

    Buffett 對「真實獲利」的定義——比帳面 EPS 更接近股東能拿走的現金。
    requires（由 Pack 宣告）：net_income_annual / depreciation_amortization_annual / capex_annual。
    """
    val = (ev["net_income_annual"]
           + ev["depreciation_amortization_annual"]
           - ev["capex_annual"])
    return SkillResult(value=val, formula="owner earnings = NI + D&A − CapEx")


def owner_earnings_yield(ev: EvidenceAccessor) -> SkillResult:
    """Owner earnings 殖利率（%）= owner earnings / 市值 × 100——安全邊際的確定性代理。

    越高＝同樣市值買到越多真實獲利＝邊際越厚（Buffett/Munger 都用此判現價貴賤）。
    requires 另需 market_cap（台股等無 market_cap 來源時 → not_evaluable，誠實揭露）。
    """
    oe = (ev["net_income_annual"]
          + ev["depreciation_amortization_annual"]
          - ev["capex_annual"])
    return SkillResult(value=oe / ev["market_cap"] * 100, unit="%",
                       formula="owner earnings yield = (NI + D&A − CapEx) / market_cap × 100")
