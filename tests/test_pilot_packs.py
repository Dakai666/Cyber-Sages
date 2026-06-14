"""Spec E1 PR3 — Buffett-2019 / Munger-2019 手工 Pack 整合測試。

不打網路：載入**真實 Pack 檔**（rules.yaml/sop.yaml/skills.py），用合成 evidence 跑
Sage Runtime 三段，驗 skill 真的算、rules 真的觸發、clamp 生效、台股式資料降 not_evaluable。
LLM 以 mock gateway 取代（SOP pass 的語意推理不在單元測試範圍）。
"""

from __future__ import annotations

from types import SimpleNamespace

from cyber_sages.agents.council import run_council
from cyber_sages.agents.schemas import SageSignal, SopStepResult
from cyber_sages.data.evidence import Evidence, EvidenceStore
from cyber_sages.personas.pack import Persona, load_personas
from cyber_sages.personas.rules import clamp_confidence, evaluate_rules, rule_values
from cyber_sages.personas.skill import run_skills


def _pack(key: str) -> Persona:
    return next(p for p in load_personas() if p.key == key)


def _fund(field, value, category="fundamentals", unit=None):
    return Evidence(category=category, field=field, value=value, unit=unit, source="test")


def _wonderful_us_store() -> EvidenceStore:
    """寬護城河、低槓桿、可預測盈餘的美股——Buffett 的甜蜜點。"""
    store = EvidenceStore(ticker="WIDE", market="US")
    store.add_all([
        _fund("net_income_annual", 1000.0, unit="USD"),
        _fund("depreciation_amortization_annual", 200.0, unit="USD"),
        _fund("capex_annual", 100.0, unit="USD"),
        _fund("market_cap", 11000.0, category="quote", unit="USD"),
        _fund("debt_to_equity", 0.4),
        _fund("interest_coverage", 15.0, unit="x"),
        _fund("roe_5y_avg", 35.0, unit="%"),
        _fund("gross_margin_trend_5y", 2.0, unit="%/yr"),
        _fund("earnings_stability_5y", 0.8),
    ])
    return store


# ---------- 包的 pilot 載入 ----------


def test_both_pilots_loaded_as_packs_no_legacy_duplicate():
    ps = {p.key: p for p in load_personas()}
    assert ps["buffett"].is_pack and ps["buffett"].epoch == 2019
    assert ps["munger"].is_pack and ps["munger"].epoch == 2019
    assert len(load_personas()) == 10  # 8 legacy + 2 pack，無重複
    # 舊單檔已退役（migrate 成目錄 Pack）
    names = [p.name for p in load_personas()]
    assert names.count("Warren Buffett") == 1 and names.count("Charlie Munger") == 1


# ---------- skill pass：真實 skills.py 確定性計算 ----------


def test_buffett_skills_compute_owner_earnings():
    buffett = _pack("buffett")
    private, not_eval = run_skills(buffett.pack.skills, _wonderful_us_store(), key="buffett")
    by_id = {e.id: e for e in private}
    assert not_eval == []
    # owner earnings = 1000 + 200 − 100 = 1100
    assert by_id["S-buffett-owner_earnings"].value == 1100.0
    # owner earnings yield = 1100 / 11000 × 100 = 10.0%
    assert by_id["S-buffett-owner_earnings_yield"].value == 10.0
    assert "NI + D&A" in by_id["S-buffett-owner_earnings"].note


def test_pilot_skills_not_evaluable_on_tw_data():
    # 台股無 capex/D&A/market_cap → owner earnings 系列降 not_evaluable（誠實揭露，不硬湊）
    tw = EvidenceStore(ticker="2330", market="TW")
    tw.add_all([_fund("roe_5y_avg", 28.0, unit="%"), _fund("debt_to_equity", 0.2)])
    for key in ("buffett", "munger"):
        private, not_eval = run_skills(_pack(key).pack.skills, tw, key=key)
        assert private == []
        assert any("owner_earnings" in n for n in not_eval)


# ---------- rule pass：真實 rules.yaml ----------


def test_buffett_rules_fire_on_wonderful_business():
    buffett = _pack("buffett")
    outcomes = {o.rule_id: o for o in evaluate_rules(
        buffett.pack.hard_rules, rule_values(_wonderful_us_store()))}
    assert outcomes["wide-moat"].triggered          # roe_5y_avg 35>20 且 trend 2>=0
    assert outcomes["predictable-earnings"].triggered  # earnings_stability_5y 0.8>=0.7
    assert not outcomes["no-leverage"].triggered    # d/e 0.4 < 1.0
    assert not outcomes["thin-interest-coverage"].triggered  # coverage 15 >= 4


def test_munger_avoids_mediocre_business():
    munger = _pack("munger")
    store = EvidenceStore(ticker="MEH", market="US")
    store.add_all([_fund("roe_5y_avg", 7.0, unit="%"), _fund("debt_to_equity", 0.3),
                   _fund("earnings_stability_5y", 0.9)])
    outcomes = {o.rule_id: o for o in evaluate_rules(
        munger.pack.hard_rules, rule_values(store))}
    assert outcomes["avoid-mediocre"].triggered          # roe_5y_avg 7 < 10 → cap 0.4
    assert not outcomes["quality-compounder"].triggered  # roe 7 不>18


def test_buffett_moat_floor_clamps_low_confidence_up():
    buffett = _pack("buffett")
    outcomes = evaluate_rules(buffett.pack.hard_rules, rule_values(_wonderful_us_store()))
    conf, conflicts = clamp_confidence("bullish", 0.3, outcomes)
    assert conf == 0.6 and conflicts == []  # wide-moat floor 0.6 > 0.3


# ---------- 三段執行整合（run_council 的 pack 路徑，真實 Pack 檔） ----------


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


async def test_buffett_pack_end_to_end_clamp_and_citecheck(monkeypatch):
    buffett = _pack("buffett")
    monkeypatch.setattr("cyber_sages.agents.council.load_personas", lambda limit=None: [buffett])
    # LLM 引用 private skill 輸出，sop_trace 數字對得上（1,100 = owner_earnings 真值）
    llm = SageSignal(
        stance="bullish", confidence=0.3, thesis="寬護城河的好生意",
        what_would_change_my_mind="護城河變窄",
        sop_trace=[SopStepResult(step="owner-earnings", conclusion="owner earnings 約 1,100",
                                 evidence_ids=["S-buffett-owner_earnings"]),
                   SopStepResult(step="margin-of-safety", conclusion="owner earnings 殖利率 10.0%",
                                 evidence_ids=["S-buffett-owner_earnings_yield"])])
    council = await run_council(_wonderful_us_store(), [], _settings(), _gateway(llm), n_sages=1)
    [s] = council.signals
    assert s.sage == "Warren Buffett"
    assert s.confidence == 0.6   # wide-moat floor 把 0.3 抬到 0.6
    assert s.unverified == []    # sop_trace 數字對得上 private/derived evidence
    assert s.not_evaluable == []  # 寬護城河美股，skill 與 rule 全評得出
