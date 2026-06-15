"""Spec C v2 PR3 — Livermore / Minervini / Raschke 交易型 Pack 整合測試。

不打網路：載入**真實 Pack 檔**（rules.yaml/sop.yaml/skills.py），用合成技術面 evidence
跑 Sage Runtime 三段，驗 skill 真的算（趨勢排列/延伸度/回檔深度）、rules 真的觸發、clamp
生效、歷史不足時降 not_evaluable。LLM 以 mock gateway 取代（SOP pass 語意推理不在單元範圍）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cyber_sages.agents.council import run_council
from cyber_sages.agents.schemas import SageSignal
from cyber_sages.data.evidence import Evidence, EvidenceStore
from cyber_sages.personas.pack import Persona, load_personas
from cyber_sages.personas.rules import clamp_confidence, evaluate_rules
from cyber_sages.personas.skill import run_skills


def _pack(key: str) -> Persona:
    return next(p for p in load_personas() if p.key == key)


def _tech(field, value, category="history", unit=None):
    return Evidence(category=category, field=field, value=value, unit=unit, source="test")


def _uptrend_store() -> EvidenceStore:
    """均線完美多頭排列、貼近 52 週高點、動能為正的強勢股——交易庭的甜蜜點。"""
    store = EvidenceStore(ticker="STRONG", market="US")
    store.add_all([
        _tech("last_price", 112.0, category="quote", unit="USD"),
        _tech("sma_20", 110.0, unit="USD"),
        _tech("sma_50", 100.0, unit="USD"),
        _tech("sma_200", 90.0, unit="USD"),
        _tech("return_3m_pct", 15.0, unit="%"),
        _tech("pct_below_52w_high", 2.0, unit="%"),
        _tech("macd_histogram", 0.5),
        _tech("rsi_14", 65.0),
    ])
    return store


# ---------- Pack 載入 ----------


def test_trading_packs_loaded_as_packs_trading_only():
    ps = {p.key: p for p in load_personas()}
    for key in ("livermore", "minervini", "raschke"):
        assert ps[key].is_pack, f"{key} 應為目錄 Pack"
        assert ps[key].horizons == ["trading"], f"{key} 應只適用 trading horizon"
        assert ps[key].epoch is None  # 交易型大師無 epoch 鎖定


# ---------- skill pass：真實 skills.py 確定性計算 ----------


def test_trading_skills_compute_on_uptrend():
    store = _uptrend_store()
    # Livermore：最小阻力線 = +2（SMA20>SMA50>SMA200 完美多頭）
    private, ne = run_skills(_pack("livermore").pack.skills, store, key="livermore")
    by = {e.id: e for e in private}
    assert ne == [] and by["S-livermore-trend_alignment_score"].value == 2.0

    # Minervini：趨勢排列 +2、對 SMA50 延伸度 = (112−100)/100×100 = 12%
    private, ne = run_skills(_pack("minervini").pack.skills, store, key="minervini")
    by = {e.id: e for e in private}
    assert ne == [] and by["S-minervini-trend_alignment_score"].value == 2.0
    assert by["S-minervini-extension_above_sma50"].value == pytest.approx(12.0)

    # Raschke：回檔深度 = (112−110)/110×100 ≈ 1.82%（Holy-Grail 小乖離）
    private, ne = run_skills(_pack("raschke").pack.skills, store, key="raschke")
    by = {e.id: e for e in private}
    assert ne == []
    assert by["S-raschke-pullback_depth_to_sma20"].value == pytest.approx(1.818, abs=0.01)


def test_trading_skills_not_evaluable_without_history():
    # 只有基本面、無價格/均線歷史（如剛上市 <30 日）→ 技術 skill 全降 not_evaluable
    bare = EvidenceStore(ticker="NEW", market="US")
    bare.add(_tech("net_income_annual", 100.0, category="fundamentals"))
    for key in ("livermore", "minervini", "raschke"):
        private, ne = run_skills(_pack(key).pack.skills, bare, key=key)
        assert private == []
        assert any("trend_alignment_score" in n for n in ne)


# ---------- rule pass：真實 rules.yaml（規則引用 derived 欄位，直接餵 rule_values）----------


def test_livermore_rules_fire_on_uptrend():
    vals = {"trend_alignment_score": 2.0, "return_3m_pct": 15.0,
            "pct_below_52w_high": 2.0, "macd_histogram": 0.5, "rsi_14": 65.0}
    o = {r.rule_id: r for r in evaluate_rules(_pack("livermore").pack.hard_rules, vals)}
    assert o["trend-up-least-resistance"].triggered    # 完美多頭 + 季線翻揚
    assert o["pivotal-breakout"].triggered              # 貼近高點 + 動能為正
    assert not o["trend-down-least-resistance"].triggered
    assert not o["climax-no-chase"].triggered           # rsi 65 < 80


def test_minervini_caps_when_not_stage2_and_extended():
    # 空頭排列（trend −2 ≤ 0）且延伸過大（40%>25）→ 兩條 cap 同時觸發
    vals = {"trend_alignment_score": -2.0, "return_3m_pct": -10.0,
            "pct_below_52w_high": 40.0, "extension_above_sma50": 40.0}
    o = {r.rule_id: r for r in evaluate_rules(_pack("minervini").pack.hard_rules, vals)}
    assert o["not-stage2"].triggered and o["too-extended-from-pivot"].triggered
    assert not o["stage2-uptrend"].triggered
    assert not o["leadership-near-high"].triggered      # 距高 40% 不 <15
    conf, conflicts = clamp_confidence("bullish", 0.9, evaluate_rules(
        _pack("minervini").pack.hard_rules, vals))
    assert conf == 0.5 and conflicts == []              # 兩 ceiling 取低（0.5）硬收


def test_raschke_momentum_rules_and_overbought_cap():
    # 順勢動能：trend ≥1 且 macd>0 → bullish_floor；但 rsi 76>75 → 過熱 cap 0.55
    vals = {"trend_alignment_score": 2.0, "macd_histogram": 0.3, "rsi_14": 76.0}
    rules = _pack("raschke").pack.hard_rules
    o = {r.rule_id: r for r in evaluate_rules(rules, vals)}
    assert o["momentum-with-trend"].triggered and o["overbought-exhaustion"].triggered
    # 同向 floor 0.55 與 ceiling 0.55 並存 → 收斂到 0.55
    conf, _ = clamp_confidence("bullish", 0.9, evaluate_rules(rules, vals))
    assert conf == 0.55


def test_trading_rules_not_evaluable_when_indicators_missing():
    # 規則引用的技術欄位缺值（歷史不足）→ 整條 not_evaluable，不假裝通過
    o = {r.rule_id: r for r in evaluate_rules(_pack("livermore").pack.hard_rules, {})}
    assert all(out.not_evaluable for out in o.values())
    assert not any(out.triggered for out in o.values())


# ---------- 三段執行整合（run_council 的 pack 路徑，真實 Pack 檔，trading horizon） ----------


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        defaults=SimpleNamespace(sages=10),
        roles={"sage": SimpleNamespace(provider="p")},
        providers={"p": SimpleNamespace(has=lambda feat: False)},
        citation=SimpleNamespace(numeric_tolerance_pct=1.0),
    )


def _gateway(signal: SageSignal):
    class G:
        async def structured(self, role, *, system, prompt, schema, **kw):
            return signal.model_copy(deep=True)
    return G()


async def test_minervini_pack_end_to_end_floor_clamps_up(monkeypatch):
    minervini = _pack("minervini")
    monkeypatch.setattr("cyber_sages.agents.council.load_personas",
                        lambda limit=None: [minervini])
    # 空 sop_trace → 跳過 cite-check；只驗 skill→rule→clamp 在 trading horizon 串起來
    llm = SageSignal(stance="bullish", confidence=0.3, thesis="Stage-2 領導股",
                     what_would_change_my_mind="跌破 SMA50")
    council = await run_council(_uptrend_store(), [], _settings(), _gateway(llm),
                                n_sages=1, horizon="trading")
    [s] = council.signals
    assert s.sage == "Mark Minervini"
    assert s.confidence == 0.6      # stage2-uptrend floor 0.6 把 0.3 抬上來
    assert s.not_evaluable == []    # 強勢股技術面齊全，skill 與 rule 全評得出
    assert council.abstained == []  # 單獨座 minervini，無人 abstain
