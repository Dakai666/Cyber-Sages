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


# ---- 交易型共用 skill（趨勢 / 動能 / 延伸度，多位短線大師共用）----
# 技術欄位（sma_* / last_price 等）由 data/indicators.py 確定性產出、US/TW 共用；DSL 的
# `{field, op, value}` 只能拿欄位比常數，無法直接比兩個欄位（如 SMA20>SMA50），故把這類
# 跨欄位比較**先算成單一 derived 欄位**再交給 rules——這正是 skill pass 的用途。


def trend_alignment_score(ev: EvidenceAccessor) -> SkillResult:
    """趨勢排列分數（line of least resistance）：均線多/空頭排列的淨方向，範圍 −2..+2。

    +2＝SMA20>SMA50>SMA200 完美多頭排列；−2＝完美空頭排列；0＝糾結無方向。
    Livermore 的「最小阻力線」、Minervini Stage-2 的客觀代理。
    requires（由 Pack 宣告）：sma_20 / sma_50 / sma_200。
    """
    up = int(ev["sma_20"] > ev["sma_50"]) + int(ev["sma_50"] > ev["sma_200"])
    down = int(ev["sma_20"] < ev["sma_50"]) + int(ev["sma_50"] < ev["sma_200"])
    return SkillResult(value=float(up - down),
                       formula="trend score = (SMA20>SMA50)+(SMA50>SMA200) − 反向兩項")


def extension_above_sma50(ev: EvidenceAccessor) -> SkillResult:
    """價格相對 SMA50 的延伸度（%）= (last_price − SMA50) / SMA50 × 100。

    正且過大＝追高、進場風險報酬差（Minervini 忌「extended」）；負＝跌破中期均線。
    requires：last_price / sma_50。
    """
    last, sma = ev["last_price"], ev["sma_50"]
    return SkillResult(value=(last - sma) / sma * 100, unit="%",
                       formula="extension = (last_price − SMA50) / SMA50 × 100")


def pullback_depth_to_sma20(ev: EvidenceAccessor) -> SkillResult:
    """價格相對 SMA20 的乖離（%）= (last_price − SMA20) / SMA20 × 100。

    Raschke「Holy Grail」回檔買點：強勢股回測 20MA 附近（小正乖離）是順勢進場節奏點；
    大負＝跌破短均、節奏轉弱。requires：last_price / sma_20。
    """
    last, sma = ev["last_price"], ev["sma_20"]
    return SkillResult(value=(last - sma) / sma * 100, unit="%",
                       formula="pullback = (last_price − SMA20) / SMA20 × 100")
