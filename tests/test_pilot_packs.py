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
    # PR3 起 Livermore/Minervini/Raschke 亦為交易型 Pack（無 epoch）
    assert ps["livermore"].is_pack and ps["livermore"].epoch is None
    assert ps["minervini"].is_pack and ps["raschke"].is_pack
    assert ps["trump"].is_pack and ps["trump"].epoch == 2025  # 催化劑型 Pack（無 rules，純 SOP）
    # Phase 4.5 Batch 1：Chanos（鑑識空頭，有 rules）+ Icahn（行動派，純 SOP）
    assert ps["chanos"].is_pack and len(ps["chanos"].pack.hard_rules) == 2
    assert ps["icahn"].is_pack and ps["icahn"].epoch is None
    # Phase 4.5 Batch 2：Soros（反身性宏觀）/ Roaring Kitty（散戶情緒，有軋空 rules）/ Son（賭徒）
    assert ps["soros"].is_pack and ps["soros"].epoch is None
    assert ps["roaringkitty"].is_pack and ps["roaringkitty"].epoch == 2021
    assert len(ps["roaringkitty"].pack.hard_rules) == 2
    assert ps["son"].is_pack and ps["son"].epoch is None
    assert len(load_personas()) >= 18  # 加 persona 不該壞此測試（PR#57 review #4）
    # 必載名單：守住 key 不被 typo 改掉（比脆性總數斷言更耐 roster 成長）
    for k in ("buffett", "munger", "graham", "damodaran", "lynch", "burry", "wood",
              "taleb", "druckenmiller", "livermore", "minervini", "raschke",
              "trump", "chanos", "icahn", "soros", "roaringkitty", "son"):
        assert k in ps, f"persona {k} 必載"
    # 舊單檔已退役（migrate 成目錄 Pack）
    names = [p.name for p in load_personas()]
    assert names.count("Warren Buffett") == 1 and names.count("Charlie Munger") == 1
    assert names.count("Jesse Livermore") == 1  # livermore.yaml 已刪、不與目錄 Pack 重複


def test_sop_only_personas_have_wellformed_sop():
    # Trump / Icahn 純靠 sop.yaml（無 rules/skills）；Chanos 亦有 SOP。驗 SOP 載入成形、
    # 每步有 step+ask、look_at 引用的 evidence 類別合法——contract 守門：防 SOP step 引用
    # 不存在的 category 溜進 production（look_at 在 council 僅 render 成 prompt 提示字串）。
    known_categories = {"quote", "fundamentals", "history", "news", "profile", "chips", "macro"}
    ps = {p.key: p for p in load_personas()}
    for key in ("trump", "icahn", "chanos", "soros", "son", "roaringkitty"):
        sop = ps[key].pack.sop
        assert len(sop) >= 3, f"{key} SOP 應至少 3 步"
        assert all(s.step and s.ask for s in sop), f"{key} 每步應有 step 名與 ask"
        for s in sop:
            for ref in s.look_at:
                cat = ref.split(".")[0]
                assert cat in known_categories, f"{key}/{s.step} look_at 引用未知類別：{ref}"


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


# ---------- Chanos rules.yaml：鑑識空頭硬規則行為（Phase 4.5）----------


def _distressed_us_store() -> EvidenceStore:
    """連利息都付不起、極端槓桿的結構性窘迫美股——Chanos 的獵物。"""
    store = EvidenceStore(ticker="ROT", market="US")
    store.add_all([
        _fund("interest_coverage", 0.4, unit="x"),
        _fund("debt_to_equity", 4.5),
    ])
    return store


def _healthy_us_store() -> EvidenceStore:
    store = EvidenceStore(ticker="OK", market="US")
    store.add_all([
        _fund("interest_coverage", 15.0, unit="x"),
        _fund("debt_to_equity", 0.4),
    ])
    return store


def test_chanos_rules_fire_on_distressed_business():
    chanos = _pack("chanos")
    outcomes = {o.rule_id: o for o in evaluate_rules(
        chanos.pack.hard_rules, rule_values(_distressed_us_store()))}
    assert outcomes["cannot-cover-interest"].triggered   # coverage 0.4 < 1
    assert outcomes["extreme-leverage"].triggered        # d/e 4.5 > 3
    assert outcomes["cannot-cover-interest"].action == "bearish_floor"


def test_chanos_rules_silent_on_healthy_business():
    chanos = _pack("chanos")
    outcomes = {o.rule_id: o for o in evaluate_rules(
        chanos.pack.hard_rules, rule_values(_healthy_us_store()))}
    assert not outcomes["cannot-cover-interest"].triggered  # coverage 15 >= 1
    assert not outcomes["extreme-leverage"].triggered       # d/e 0.4 < 3


def test_chanos_interest_rule_not_evaluable_on_tw_data():
    # 台股缺 interest_coverage（FinMind 現金流量 YTD 累計、刻意不發償債欄位）→
    # cannot-cover-interest 降 not_evaluable（驗 rules.yaml 開頭 D3 註解真實落地）；
    # debt_to_equity 兩市皆可 → extreme-leverage 照常求值。
    tw = EvidenceStore(ticker="2330", market="TW")
    tw.add_all([_fund("debt_to_equity", 4.5)])  # 無 interest_coverage
    outcomes = {o.rule_id: o for o in evaluate_rules(
        _pack("chanos").pack.hard_rules, rule_values(tw))}
    assert outcomes["cannot-cover-interest"].not_evaluable
    assert outcomes["extreme-leverage"].triggered           # d/e 4.5 > 3 仍可判


def test_chanos_bearish_floor_clamps_low_confidence_up():
    chanos = _pack("chanos")
    outcomes = evaluate_rules(chanos.pack.hard_rules, rule_values(_distressed_us_store()))
    # 同向（bearish）→ floor 把信心從 0.3 抬到 0.6（cannot-cover-interest floor 0.6 為兩條中較高）
    conf, conflicts = clamp_confidence("bearish", 0.3, outcomes)
    assert conf == 0.6 and conflicts == []
    # 反向（bullish）→ 不翻轉、兩條 bearish 規則皆記入 rule_conflicts 揭露
    conf2, conflicts2 = clamp_confidence("bullish", 0.7, outcomes)
    assert conf2 == 0.7 and len(conflicts2) == 2


# ---------- Roaring Kitty rules.yaml：軋空火藥硬規則行為（Phase 4.5 Batch 2）----------


def _squeeze_us_store() -> EvidenceStore:
    """高 short% of float、回補天數長的軋空 setup 美股——Roaring Kitty 的火藥庫。"""
    store = EvidenceStore(ticker="MEME", market="US")
    store.add_all([
        _fund("short_percent_of_float", 35.0, unit="%"),
        _fund("short_ratio", 8.0, unit="days"),
    ])
    return store


def test_roaringkitty_rules_fire_on_squeeze_setup():
    kitty = _pack("roaringkitty")
    outcomes = {o.rule_id: o for o in evaluate_rules(
        kitty.pack.hard_rules, rule_values(_squeeze_us_store()))}
    assert outcomes["heavy-short-interest"].triggered   # short% 35 > 20
    assert outcomes["many-days-to-cover"].triggered      # short_ratio 8 > 5
    assert outcomes["heavy-short-interest"].action == "bullish_floor"


def test_roaringkitty_bullish_floor_only_clamps_when_aligned():
    kitty = _pack("roaringkitty")
    outcomes = evaluate_rules(kitty.pack.hard_rules, rule_values(_squeeze_us_store()))
    # 同向（bullish）→ floor 把 0.3 抬到 0.55（heavy-short-interest floor 0.55 為兩條中較高）
    conf, conflicts = clamp_confidence("bullish", 0.3, outcomes)
    assert conf == 0.55 and conflicts == []
    # 反向（bearish，如 Chanos 對同一高 short% 讀成「空方是對的」）→ 不翻轉、記 2 conflicts
    conf2, conflicts2 = clamp_confidence("bearish", 0.6, outcomes)
    assert conf2 == 0.6 and len(conflicts2) == 2


def test_roaringkitty_rules_not_evaluable_on_tw_data():
    # 台股以融資券（chips）表達、不發 short_percent_of_float / short_ratio → 兩條規則 not_evaluable。
    tw = EvidenceStore(ticker="2330", market="TW")
    tw.add_all([_fund("debt_to_equity", 0.3)])  # 無 short interest 欄位
    outcomes = {o.rule_id: o for o in evaluate_rules(
        _pack("roaringkitty").pack.hard_rules, rule_values(tw))}
    assert outcomes["heavy-short-interest"].not_evaluable
    assert outcomes["many-days-to-cover"].not_evaluable


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
