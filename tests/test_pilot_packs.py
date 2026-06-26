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
    # Phase 4.5 Batch 3：PTJ（保守型短線交易者，補最瘦的保守×短期象限；帶 skill + 200DMA rule）
    assert ps["ptj"].is_pack and ps["ptj"].horizons == ["trading"]
    assert ps["ptj"].aggression == ["conservative"]
    assert len(ps["ptj"].pack.skills) == 1 and len(ps["ptj"].pack.hard_rules) == 1
    # Phase 6 遷移（舊單檔 → 完整 Pack，epoch-less）：Graham（資產/net-net，3 rules + 2 skills）、
    # Lynch（GARP，PEG skill 走 rule↔skill pattern，1 rule）
    assert ps["graham"].is_pack and ps["graham"].epoch is None
    assert len(ps["graham"].pack.hard_rules) == 3 and len(ps["graham"].pack.skills) == 2
    assert ps["lynch"].is_pack and ps["lynch"].epoch is None
    assert len(ps["lynch"].pack.skills) == 1 and len(ps["lynch"].pack.hard_rules) == 1
    # Phase 6 遷移（續）：Burry（FCF 深度價值，3 rules + 1 skill）、Damodaran（相對估值，
    # 1 rule + 1 skill，刻意輕規則）、Druckenmiller（順勢，複用 trend_alignment_score，2 rules）
    assert ps["burry"].is_pack and ps["burry"].epoch is None
    assert len(ps["burry"].pack.hard_rules) == 3 and len(ps["burry"].pack.skills) == 1
    assert ps["damodaran"].is_pack and ps["damodaran"].epoch is None
    assert len(ps["damodaran"].pack.hard_rules) == 1 and len(ps["damodaran"].pack.skills) == 1
    assert ps["druckenmiller"].is_pack and ps["druckenmiller"].epoch is None
    assert len(ps["druckenmiller"].pack.hard_rules) == 2 and len(ps["druckenmiller"].pack.skills) == 1
    # Phase 6 遷移（收官 7/7）：Taleb（純 SOP，哲學使然——無 rules/skills，類比 Icahn）、
    # Wood（顛覆成長，1 floor rule、無 skill——Wright's Law/TAM 無法確定性算）
    assert ps["taleb"].is_pack and ps["taleb"].epoch is None
    assert ps["taleb"].pack.hard_rules == [] and ps["taleb"].pack.skills == []
    assert len(ps["taleb"].pack.sop) >= 3
    assert ps["wood"].is_pack and ps["wood"].epoch is None
    assert len(ps["wood"].pack.hard_rules) == 1 and ps["wood"].pack.skills == []
    assert len(load_personas()) >= 19  # 加 persona 不該壞此測試（PR#57 review #4）
    # 必載名單：守住 key 不被 typo 改掉（比脆性總數斷言更耐 roster 成長）
    for k in ("buffett", "munger", "graham", "damodaran", "lynch", "burry", "wood",
              "taleb", "druckenmiller", "livermore", "minervini", "raschke",
              "trump", "chanos", "icahn", "soros", "roaringkitty", "son", "ptj"):
        assert k in ps, f"persona {k} 必載"
    # 舊單檔已退役（migrate 成目錄 Pack）
    names = [p.name for p in load_personas()]
    assert names.count("Warren Buffett") == 1 and names.count("Charlie Munger") == 1
    assert names.count("Jesse Livermore") == 1  # livermore.yaml 已刪、不與目錄 Pack 重複
    # Phase 6：graham.yaml / lynch.yaml 退役，不與目錄 Pack 重複
    assert names.count("Benjamin Graham") == 1 and names.count("Peter Lynch") == 1
    # Phase 6（續）：burry / damodaran / druckenmiller 單檔退役
    assert names.count("Michael Burry") == 1 and names.count("Aswath Damodaran") == 1
    assert names.count("Stanley Druckenmiller") == 1
    # Phase 6（收官 7/7）：taleb / wood 單檔退役——全 7 舊單檔遷移完成、personas/ 下無 .yaml
    assert names.count("Nassim Taleb") == 1 and names.count("Cathie Wood") == 1
    import glob as _glob
    from cyber_sages.personas.pack import PERSONA_DIR
    assert _glob.glob(str(PERSONA_DIR / "*.yaml")) == [], "全員遷移後 personas/ 下不應再有單檔 yaml"


def test_sop_only_personas_have_wellformed_sop():
    # Trump / Icahn 純靠 sop.yaml（無 rules/skills）；Chanos 亦有 SOP。驗 SOP 載入成形、
    # 每步有 step+ask、look_at 引用的 evidence 類別合法——contract 守門：防 SOP step 引用
    # 不存在的 category 溜進 production（look_at 在 council 僅 render 成 prompt 提示字串）。
    # estimate（forward 共識）/ reference（Damodaran 產業 multiples）/ derived（skill 私有衍生）
    # 為 Spec A/F 後新增的合法類別。
    known_categories = {"quote", "fundamentals", "history", "news", "profile", "chips",
                        "macro", "estimate", "reference", "derived"}
    ps = {p.key: p for p in load_personas()}
    # graham/lynch + burry/damodaran/druckenmiller（Phase 6 遷移）一併納入 SOP 合法性守門
    for key in ("trump", "icahn", "chanos", "soros", "son", "roaringkitty", "ptj",
                "graham", "lynch", "burry", "damodaran", "druckenmiller", "taleb", "wood"):
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


# ---------- PTJ skill + rule：200DMA 防線（skill 算衍生欄位 → rule 對它判定，Phase 4.5 Batch 3）----------


def _price_store(last: float, sma_200: float) -> EvidenceStore:
    store = EvidenceStore(ticker="X", market="US")
    store.add_all([
        Evidence(category="quote", field="last_price", value=last, source="test"),
        Evidence(category="history", field="sma_200", value=sma_200, source="test"),
    ])
    return store


def test_ptj_skill_computes_distance_from_200dma():
    ptj = _pack("ptj")
    private, not_eval = run_skills(ptj.pack.skills, _price_store(110.0, 100.0), key="ptj")
    by_id = {e.id: e for e in private}
    assert not_eval == []
    assert round(by_id["S-ptj-price_vs_sma_200_pct"].value, 6) == 10.0   # (110/100 − 1)×100


def test_ptj_below_200dma_rule_fires_and_floors_bearish():
    ptj = _pack("ptj")
    # skill 先算 price_vs_sma_200_pct，併入 sage_store 後 rule 才看得到（同 council 三段執行）
    private, _ = run_skills(ptj.pack.skills, _price_store(90.0, 100.0), key="ptj")
    sage_store = EvidenceStore(ticker="X", market="US")
    sage_store.add_all([*_price_store(90.0, 100.0).items, *private])
    outcomes = evaluate_rules(ptj.pack.hard_rules, rule_values(sage_store))
    by_id = {o.rule_id: o for o in outcomes}
    assert by_id["below-200dma"].triggered          # 90 < 200DMA 100 → −10% < 0
    # 同向 bearish → floor 0.55；反向 bullish 不翻轉、記衝突（「年線下不站多」是鐵則）
    conf, conflicts = clamp_confidence("bearish", 0.3, outcomes)
    assert conf == 0.55 and conflicts == []
    conf2, conflicts2 = clamp_confidence("bullish", 0.7, outcomes)
    assert conf2 == 0.7 and len(conflicts2) == 1


def test_ptj_rule_silent_above_200dma():
    ptj = _pack("ptj")
    private, _ = run_skills(ptj.pack.skills, _price_store(120.0, 100.0), key="ptj")
    sage_store = EvidenceStore(ticker="X", market="US")
    sage_store.add_all([*_price_store(120.0, 100.0).items, *private])
    outcomes = {o.rule_id: o for o in evaluate_rules(ptj.pack.hard_rules, rule_values(sage_store))}
    assert not outcomes["below-200dma"].triggered   # +20% 在年線之上


def test_ptj_rule_not_evaluable_when_skill_missing_input():
    # 「rule 依賴 skill 輸出」pattern 的核心 cascade：缺 sma_200 → skill not_evaluable →
    # price_vs_sma_200_pct 不在 values → rule 也 not_evaluable（不靜默失效、不假裝觸發）。
    # 守住 field 名漂移 / skill 改名 / requires 變動時不會 silently 讓鐵則失靈。
    ptj = _pack("ptj")
    store = EvidenceStore(ticker="X", market="US")
    store.add_all([_fund("last_price", 100.0, category="quote")])  # 故意缺 sma_200
    private, not_eval = run_skills(ptj.pack.skills, store, key="ptj")
    assert any("price_vs_sma_200_pct" in n for n in not_eval)   # skill 確實 not_evaluable
    sage_store = EvidenceStore(ticker="X", market="US")
    sage_store.add_all([*store.items, *private])
    outcomes = {o.rule_id: o for o in evaluate_rules(ptj.pack.hard_rules, rule_values(sage_store))}
    assert outcomes["below-200dma"].not_evaluable              # rule 跟著 not_evaluable


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


def _seq_gateway(signals: list[SageSignal]):
    """依序回傳 signals（每次 structured 取下一個），記錄呼叫次數——測空 trace 重試/恢復。"""
    class G:
        def __init__(self):
            self.calls = 0
        async def structured(self, role, *, system, prompt, schema, **kw):
            sig = signals[min(self.calls, len(signals) - 1)]
            self.calls += 1
            return sig.model_copy(deep=True)
    return G()


async def test_empty_sop_trace_retried_then_disclosed(monkeypatch):
    """#41：pack 大師回空 sop_trace → 重試一次仍空 → 揭露於 not_evaluable，不靜默 bypass。"""
    buffett = _pack("buffett")
    monkeypatch.setattr("cyber_sages.agents.council.load_personas", lambda limit=None: [buffett])
    empty = SageSignal(stance="bullish", confidence=0.3, thesis="好生意",
                       what_would_change_my_mind="護城河變窄")  # sop_trace 預設空
    gw = _seq_gateway([empty])
    council = await run_council(_wonderful_us_store(), [], _settings(), gw, n_sages=1)
    [s] = council.signals
    assert gw.calls == 2  # 首次 + 空 trace 重試一次
    assert any("sop-discipline" in n for n in s.not_evaluable)  # 揭露而非靜默通過


async def test_empty_sop_trace_recovered_on_retry_no_disclosure(monkeypatch):
    """#41：首次空、重試補上 sop_trace → 不標記（紀律已恢復）。"""
    buffett = _pack("buffett")
    monkeypatch.setattr("cyber_sages.agents.council.load_personas", lambda limit=None: [buffett])
    empty = SageSignal(stance="bullish", confidence=0.3, thesis="好生意",
                       what_would_change_my_mind="護城河變窄")
    recovered = empty.model_copy(deep=True)
    # 結論不含數字、引用真實 evidence（E001 存在於 wonderful store）→ cite-check 過、不再重試
    recovered.sop_trace = [SopStepResult(step="verdict", conclusion="符合哲學", evidence_ids=["E001"])]
    gw = _seq_gateway([empty, recovered])
    council = await run_council(_wonderful_us_store(), [], _settings(), gw, n_sages=1)
    [s] = council.signals
    assert gw.calls == 2
    assert not any("sop-discipline" in n for n in s.not_evaluable)  # 已恢復，無揭露
    assert s.sop_trace and s.sop_trace[0].conclusion == "符合哲學"


async def test_lazy_sop_step_ids_canonicalized_to_pack(monkeypatch):
    """#45：LLM 偷懶填 ['1'..'6']（步數相符），框架按序覆寫為 sop.yaml canonical id。"""
    buffett = _pack("buffett")
    monkeypatch.setattr("cyber_sages.agents.council.load_personas", lambda limit=None: [buffett])
    canonical = [s.step for s in buffett.pack.sop]  # 6 步
    llm = SageSignal(
        stance="neutral", confidence=0.4, thesis="觀望", what_would_change_my_mind="估值",
        neutral_reason="insufficient_signal",
        # 偷懶的 step id，步數與 sop.yaml 相符；結論不含數字避免觸 cite-check
        sop_trace=[SopStepResult(step=str(i + 1), conclusion=f"第{i + 1}步結論", evidence_ids=[])
                   for i in range(len(canonical))],
    )
    council = await run_council(_wonderful_us_store(), [], _settings(), _gateway(llm), n_sages=1)
    [s] = council.signals
    assert [t.step for t in s.sop_trace] == canonical  # 程式回填，丟棄 '1'..'6'


async def test_mismatched_sop_step_count_left_untouched(monkeypatch):
    """#45：步數不符（不完整 trace）不貿然位移對齊——保留 LLM 原值，交 #41 處理。"""
    buffett = _pack("buffett")
    monkeypatch.setattr("cyber_sages.agents.council.load_personas", lambda limit=None: [buffett])
    llm = SageSignal(
        stance="neutral", confidence=0.4, thesis="觀望", what_would_change_my_mind="估值",
        neutral_reason="insufficient_signal",
        sop_trace=[SopStepResult(step="x", conclusion="只走了一步", evidence_ids=[])],  # 1 ≠ 6
    )
    council = await run_council(_wonderful_us_store(), [], _settings(), _gateway(llm), n_sages=1)
    [s] = council.signals
    assert [t.step for t in s.sop_trace] == ["x"]  # 未覆寫


# ---------- Graham / Lynch packs（Phase 6 遷移：舊單檔 → 完整 Pack）----------


def _graham_bargain_store() -> EvidenceStore:
    """便宜＋盈餘穩定＋財務強健、且市值低於 net-net 清算價——Graham 的夢幻便宜貨。"""
    store = EvidenceStore(ticker="CIGAR", market="US")
    store.add_all([
        _fund("trailing_pe", 11.0, category="quote"),
        _fund("earnings_stability_5y", 0.85),
        _fund("debt_to_equity", 0.3),
        _fund("net_net_value", 1200.0, unit="USD"),
        _fund("market_cap", 1000.0, category="quote", unit="USD"),  # 市值 < 清算價
        _fund("current_assets_annual", 800.0, unit="USD"),
        _fund("current_liabilities_annual", 200.0, unit="USD"),
    ])
    return store


def test_graham_skills_compute_net_net_and_current_ratio():
    private, not_eval = run_skills(_pack("graham").pack.skills, _graham_bargain_store(), key="graham")
    by_id = {e.id: e for e in private}
    assert not_eval == []
    # (1200 − 1000) / 1000 × 100 = 20.0%（市值低於清算價 20%）
    assert by_id["S-graham-net_net_discount_pct"].value == 20.0
    # 800 / 200 = 4.0x
    assert by_id["S-graham-current_ratio"].value == 4.0


def test_graham_rules_fire_on_deep_bargain():
    graham = _pack("graham")
    store = _graham_bargain_store()
    # net_net_discount_pct 是 skill 私有衍生，rules 求值前須先併回 store。
    # 用 items.extend 而非 add_all——保留 S-graham-… id（production 走 EvidenceStore(items=[...])，
    # 而 add_all→add 會把 id 改寫成 E0xx，見 evidence.py:84）。rules 以 field 求值，id 雖不影響觸發，
    # 但保留 id 才不誤導「skill 輸出真是 S-<key>-<name>」（該事實由 test_lynch_skill_computes_peg pin）。
    private, _ = run_skills(graham.pack.skills, store, key="graham")
    store.items.extend(private)
    outcomes = {o.rule_id: o for o in evaluate_rules(graham.pack.hard_rules, rule_values(store))}
    assert outcomes["defensive-bargain"].triggered    # pe 11(>0,<15) 且 stability 0.85>=0.7
    assert outcomes["below-liquidation"].triggered     # net_net_discount 20 > 0
    assert not outcomes["weak-financials"].triggered   # d/e 0.3 < 1.0


def test_graham_weak_financials_caps_even_when_cheap():
    # 紅線：負債過高即使便宜也封頂——cap_confidence 與立場無關。
    # stability 0.5（< 0.7）刻意讓 defensive-bargain 不觸發，單獨驗 cap：cheap-but-unstable
    # 的高槓桿股，Graham 的財務紅線把信心硬收到 0.5（非靠盈餘穩定的 floor 拉抬）。
    graham = _pack("graham")
    store = EvidenceStore(ticker="LEVERED", market="US")
    store.add_all([_fund("trailing_pe", 9.0, category="quote"),
                   _fund("earnings_stability_5y", 0.5), _fund("debt_to_equity", 1.8)])
    outcomes = {o.rule_id: o for o in evaluate_rules(graham.pack.hard_rules, rule_values(store))}
    assert outcomes["weak-financials"].triggered
    assert not outcomes["defensive-bargain"].triggered     # stability 0.5 < 0.7
    # 即使看多、信心 0.9，weak-financials cap 0.5 硬收
    conf, _ = clamp_confidence("bullish", 0.9, list(outcomes.values()))
    assert conf == 0.5


def test_clamp_floor_dominates_cap_when_floor_higher():
    # 現行 clamp_confidence（rules.py:120-124）行為 pin：cap 0.5 與 bullish_floor 0.6 同觸發、
    # stance bullish 時，floor 蓋過 cap → 0.6（cap 與 floor 各自獨立套用，淨值看數值）。
    # 非 Graham pack 引入（首次有 Pack 同時觸發兩者，PR #86 review 觀察）；此測試把現行語意釘住，
    # 日後若重構 cap/floor 優先序會 fail 提醒（見 issue：clamp_confidence cap vs floor 優先序）。
    graham = _pack("graham")
    store = EvidenceStore(ticker="CHEAP_LEVERED", market="US")
    store.add_all([_fund("trailing_pe", 11.0, category="quote"),    # defensive-bargain floor 0.6
                   _fund("earnings_stability_5y", 0.85),
                   _fund("debt_to_equity", 1.8)])                   # weak-financials cap 0.5
    outcomes = evaluate_rules(graham.pack.hard_rules, rule_values(store))
    by_id = {o.rule_id: o for o in outcomes}
    assert by_id["weak-financials"].triggered and by_id["defensive-bargain"].triggered
    conf, _ = clamp_confidence("bullish", 0.9, outcomes)
    assert conf == 0.6   # floor 0.6 蓋過 cap 0.5（現行行為，待 issue 決定是否反轉）


def test_graham_negative_pe_not_treated_as_cheap():
    # trailing_pe < 0（虧損股）不該被 defensive-bargain 誤判成便宜（pe>0 guard）
    graham = _pack("graham")
    store = EvidenceStore(ticker="LOSS", market="US")
    store.add_all([_fund("trailing_pe", -8.0, category="quote"),
                   _fund("earnings_stability_5y", 0.9), _fund("debt_to_equity", 0.3)])
    outcomes = {o.rule_id: o for o in evaluate_rules(graham.pack.hard_rules, rule_values(store))}
    assert not outcomes["defensive-bargain"].triggered


def test_graham_skills_not_evaluable_on_tw_data():
    # 台股的流動資產欄位是 current_assets / current_liabilities（**無** _annual 後綴，
    # 見 data/tw_stocks.py:81-82）、且無 market_cap。Graham 的 skill require *_annual 與 market_cap，
    # 故落到「欄位名後綴錯位 / 缺欄」而降 not_evaluable（誠實，非靜默通過）——這正是台股真實缺的。
    tw = EvidenceStore(ticker="2330", market="TW")
    tw.add_all([_fund("debt_to_equity", 0.2),
                _fund("current_assets", 8.0e11, unit="TWD"),       # 台股口徑：無 _annual
                _fund("current_liabilities", 2.0e11, unit="TWD"),
                _fund("net_net_value", 6.0e11, unit="TWD")])       # net_net_value 有、但缺 market_cap
    private, not_eval = run_skills(_pack("graham").pack.skills, tw, key="graham")
    assert private == []
    # net_net_discount 缺的是 market_cap（net_net_value 在）；current_ratio 缺的是 *_annual（後綴錯位）
    assert any("net_net_discount_pct" in n and "market_cap" in n for n in not_eval)
    assert any("current_ratio" in n and "current_assets_annual" in n for n in not_eval)


def test_lynch_skill_computes_peg():
    store = EvidenceStore(ticker="GARP", market="US")
    store.add_all([_fund("trailing_pe", 20.0, category="quote"),
                   _fund("earnings_growth_est_pct", 25.0, category="estimate")])
    private, not_eval = run_skills(_pack("lynch").pack.skills, store, key="lynch")
    by_id = {e.id: e for e in private}
    assert not_eval == []
    assert by_id["S-lynch-peg_ratio"].value == 0.8       # 20 / 25 = 0.8（PEG<1 便宜）


def test_lynch_garp_rule_fires_when_growth_cheap():
    lynch = _pack("lynch")
    store = EvidenceStore(ticker="GARP", market="US")
    store.add_all([_fund("trailing_pe", 20.0, category="quote"),
                   _fund("earnings_growth_est_pct", 25.0, category="estimate")])
    store.items.extend(run_skills(lynch.pack.skills, store, key="lynch")[0])  # 保留 S- id（同上）
    outcomes = evaluate_rules(lynch.pack.hard_rules, rule_values(store))
    by_id = {o.rule_id: o for o in outcomes}
    assert by_id["garp-bargain"].triggered               # peg 0.8 < 1 且成長 25>0
    # 同向（bullish）floor 0.6 把信心從 0.4 抬升
    conf, conflicts = clamp_confidence("bullish", 0.4, outcomes)
    assert conf == 0.6 and conflicts == []
    # 反向（bearish）不翻轉、記 rule_conflict 揭露
    conf2, conflicts2 = clamp_confidence("bearish", 0.7, outcomes)
    assert conf2 == 0.7 and len(conflicts2) == 1


def test_lynch_negative_growth_not_treated_as_cheap():
    # 衰退股（成長 < 0）得負 PEG，三重 guard（growth>0、peg>0、peg<1）必須擋下假便宜
    lynch = _pack("lynch")
    store = EvidenceStore(ticker="DECLINE", market="US")
    store.add_all([_fund("trailing_pe", 20.0, category="quote"),
                   _fund("earnings_growth_est_pct", -10.0, category="estimate")])
    store.items.extend(run_skills(lynch.pack.skills, store, key="lynch")[0])  # 保留 S- id（同上）
    outcomes = {o.rule_id: o for o in evaluate_rules(lynch.pack.hard_rules, rule_values(store))}
    assert not outcomes["garp-bargain"].triggered


def test_lynch_peg_not_evaluable_on_tw_data():
    # 台股常缺 forward 共識成長與 trailing_pe → peg 降 not_evaluable（誠實，改定性判斷）
    tw = EvidenceStore(ticker="2330", market="TW")
    tw.add_all([_fund("debt_to_equity", 0.2)])
    private, not_eval = run_skills(_pack("lynch").pack.skills, tw, key="lynch")
    assert private == []
    assert any("peg_ratio" in n for n in not_eval)


# ---------- Burry / Damodaran / Druckenmiller packs（Phase 6 遷移，續）----------


def test_burry_fcf_yield_and_balance_sheet_rules():
    # 深度 FCF 價值 + 脆弱資產負債表紅線
    store = EvidenceStore(ticker="DEEP", market="US")
    store.add_all([_fund("free_cash_flow_annual", 1000.0, unit="USD"),
                   _fund("market_cap", 10000.0, category="quote", unit="USD"),  # fcf_yield 10%
                   _fund("interest_coverage", 8.0, unit="x"), _fund("debt_to_equity", 0.5)])
    burry = _pack("burry")
    priv, ne = run_skills(burry.pack.skills, store, key="burry")
    by_id = {e.id: e for e in priv}
    assert ne == [] and by_id["S-burry-fcf_yield"].value == 10.0   # 1000/10000×100
    store.items.extend(priv)
    outcomes = {o.rule_id: o for o in evaluate_rules(burry.pack.hard_rules, rule_values(store))}
    assert outcomes["deep-fcf-value"].triggered          # 10% > 8%
    assert not outcomes["cannot-cover-interest"].triggered  # 8 >= 1
    assert not outcomes["excess-leverage"].triggered     # 0.5 < 2


def test_burry_fragile_balance_sheet_caps_confidence():
    # 付不出利息 → cap 0.4 硬收（即使看多）
    store = EvidenceStore(ticker="ROT", market="US")
    store.add_all([_fund("interest_coverage", 0.5, unit="x"), _fund("debt_to_equity", 3.0)])
    burry = _pack("burry")
    outcomes = evaluate_rules(burry.pack.hard_rules, rule_values(store))
    by_id = {o.rule_id: o for o in outcomes}
    assert by_id["cannot-cover-interest"].triggered and by_id["excess-leverage"].triggered
    conf, _ = clamp_confidence("bullish", 0.9, outcomes)
    assert conf == 0.4   # cannot-cover-interest ceiling 0.4 為兩 cap 中較低


def test_burry_skills_not_evaluable_on_tw():
    tw = EvidenceStore(ticker="2330", market="TW")
    tw.add_all([_fund("debt_to_equity", 0.3)])   # 無 market_cap / free_cash_flow_annual
    priv, ne = run_skills(_pack("burry").pack.skills, tw, key="burry")
    assert priv == [] and any("fcf_yield" in n for n in ne)


def test_burry_tw_rules_cascade_consistently_with_pr_description():
    # 規則級降級：台股僅有 debt_to_equity 時，excess-leverage 仍可評，cannot-cover-interest
    # （缺 interest_coverage）與 deep-fcf-value（缺 skill fcf_yield）降 not_evaluable——
    # 把 PR 描述承諾的三件事釘在測試裡（forge-sage 範本：PR 說的事實要可驗）。
    tw = EvidenceStore(ticker="2330", market="TW")
    tw.add_all([_fund("debt_to_equity", 0.3)])
    outcomes = {o.rule_id: o for o in evaluate_rules(
        _pack("burry").pack.hard_rules, rule_values(tw))}
    assert outcomes["excess-leverage"].not_evaluable is False      # debt_to_equity 在
    assert outcomes["cannot-cover-interest"].not_evaluable is True
    assert outcomes["deep-fcf-value"].not_evaluable is True


def test_damodaran_relative_discount_fires():
    # 現價 P/E 遠低於同業 → relative-discount floor
    store = EvidenceStore(ticker="CHEAP", market="US")
    store.add_all([_fund("trailing_pe", 10.0, category="quote"),
                   _fund("industry_pe_trailing", 20.0, category="reference")])  # -50%
    dam = _pack("damodaran")
    priv, ne = run_skills(dam.pack.skills, store, key="damodaran")
    by_id = {e.id: e for e in priv}
    assert ne == [] and by_id["S-damodaran-pe_vs_industry_pct"].value == -50.0
    store.items.extend(priv)
    outcomes = {o.rule_id: o for o in evaluate_rules(dam.pack.hard_rules, rule_values(store))}
    assert outcomes["relative-discount"].triggered       # -50% < -25%


def test_damodaran_negative_pe_not_treated_as_discount():
    # 虧損股負 P/E → pe_vs_industry 變成大幅「折價」假象，trailing_pe>0 guard 必須擋下
    store = EvidenceStore(ticker="LOSS", market="US")
    store.add_all([_fund("trailing_pe", -15.0, category="quote"),
                   _fund("industry_pe_trailing", 20.0, category="reference")])
    dam = _pack("damodaran")
    store.items.extend(run_skills(dam.pack.skills, store, key="damodaran")[0])
    outcomes = {o.rule_id: o for o in evaluate_rules(dam.pack.hard_rules, rule_values(store))}
    assert not outcomes["relative-discount"].triggered


def test_damodaran_skill_not_evaluable_without_industry_multiple():
    # 台股無產業 multiple（reference US-only）→ pe_vs_industry not_evaluable
    tw = EvidenceStore(ticker="2330", market="TW")
    tw.add_all([_fund("trailing_pe", 18.0, category="quote")])  # 無 industry_pe_trailing
    priv, ne = run_skills(_pack("damodaran").pack.skills, tw, key="damodaran")
    assert priv == [] and any("pe_vs_industry_pct" in n for n in ne)


def test_druckenmiller_trend_floors_both_directions():
    dru = _pack("druckenmiller")
    # 多頭排列 → ride-the-trend，bullish floor 0.55
    up = EvidenceStore(ticker="UP", market="US")
    up.add_all([_fund("sma_20", 110.0, category="history"), _fund("sma_50", 100.0, category="history"),
                _fund("sma_200", 90.0, category="history")])
    up.items.extend(run_skills(dru.pack.skills, up, key="druckenmiller")[0])
    o_up = {o.rule_id: o for o in evaluate_rules(dru.pack.hard_rules, rule_values(up))}
    assert o_up["ride-the-trend"].triggered and not o_up["trend-broken"].triggered
    conf, conflicts = clamp_confidence("bullish", 0.3, list(o_up.values()))
    assert conf == 0.55 and conflicts == []
    # 空頭排列 → trend-broken，bearish floor；若大師看多則記 rule_conflict 不翻轉
    down = EvidenceStore(ticker="DN", market="US")
    down.add_all([_fund("sma_20", 90.0, category="history"), _fund("sma_50", 100.0, category="history"),
                  _fund("sma_200", 110.0, category="history")])
    down.items.extend(run_skills(dru.pack.skills, down, key="druckenmiller")[0])
    o_dn = {o.rule_id: o for o in evaluate_rules(dru.pack.hard_rules, rule_values(down))}
    assert o_dn["trend-broken"].triggered
    _, conflicts2 = clamp_confidence("bullish", 0.6, list(o_dn.values()))
    assert len(conflicts2) == 1   # 規則 bearish、大師 bullish → 衝突揭露、不翻


def test_druckenmiller_trend_evaluable_on_tw():
    # 跨市場：sma_* 兩市場皆由 indicators 算 → 趨勢規則在台股仍可評（非降級）
    tw = EvidenceStore(ticker="2330", market="TW")
    tw.add_all([_fund("sma_20", 1100.0, category="history"), _fund("sma_50", 1000.0, category="history"),
                _fund("sma_200", 900.0, category="history")])
    priv, ne = run_skills(_pack("druckenmiller").pack.skills, tw, key="druckenmiller")
    by_id = {e.id: e for e in priv}   # 用 id 比對（不靠 priv[0] 順序，未來加 skill 也穩）
    assert ne == [] and by_id["S-druckenmiller-trend_alignment_score"].value == 2.0  # 完美多頭排列


# ---------- Taleb（純 SOP）/ Wood（高成長 floor）packs（Phase 6 收官 7/7）----------


def test_taleb_is_pure_sop_no_rules_no_skills():
    # Taleb 純 SOP：哲學使然（反機械化點預測），非資料缺——驗無 rules/skills、SOP 成形
    taleb = _pack("taleb")
    assert taleb.pack.hard_rules == [] and taleb.pack.skills == []
    assert len(taleb.pack.sop) >= 4
    assert taleb.pack.sop[-1].step == "verdict"   # 末步定論


def test_wood_hypergrowth_floor_fires():
    wood = _pack("wood")
    store = EvidenceStore(ticker="DISRUPT", market="US")
    store.add_all([_fund("revenue_growth_est_pct", 40.0, category="estimate")])
    outcomes = evaluate_rules(wood.pack.hard_rules, rule_values(store))
    by_id = {o.rule_id: o for o in outcomes}
    assert by_id["hypergrowth"].triggered                 # 40% > 25%
    # 同向（bullish）floor 0.6 把信心從 0.4 抬升
    conf, conflicts = clamp_confidence("bullish", 0.4, outcomes)
    assert conf == 0.6 and conflicts == []


def test_wood_hypergrowth_silent_on_slow_grower():
    wood = _pack("wood")
    store = EvidenceStore(ticker="SLOW", market="US")
    store.add_all([_fund("revenue_growth_est_pct", 8.0, category="estimate")])
    outcomes = {o.rule_id: o for o in evaluate_rules(wood.pack.hard_rules, rule_values(store))}
    assert not outcomes["hypergrowth"].triggered          # 8% 不 > 25%


def test_wood_hypergrowth_not_evaluable_without_forward_growth():
    # 缺 forward 共識成長（台股常見）→ hypergrowth not_evaluable（誠實，改定性軌跡判斷）
    tw = EvidenceStore(ticker="2330", market="TW")
    tw.add_all([_fund("revenue_annual", 1.0e12, unit="TWD")])  # 無 revenue_growth_est_pct
    outcomes = {o.rule_id: o for o in evaluate_rules(_pack("wood").pack.hard_rules, rule_values(tw))}
    assert outcomes["hypergrowth"].not_evaluable
