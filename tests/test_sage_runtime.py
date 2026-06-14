"""Spec E1 — Sage Runtime 框架測試：DSL evaluator / clamp / skill / Pack loader / 三段執行。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cyber_sages.agents.council import load_personas, run_council
from cyber_sages.agents.schemas import SageSignal, SopStepResult
from cyber_sages.data.evidence import Evidence, EvidenceStore
from cyber_sages.personas.pack import (
    Persona,
    PersonaPack,
    SopStep,
    _load_pack_dir,
    load_personas as pack_load_personas,
)
from cyber_sages.personas import pack as pack_module
from cyber_sages.personas.rules import (
    HardRule,
    clamp_confidence,
    evaluate_rules,
    rule_values,
)
from cyber_sages.personas.skill import SkillResult, run_skills, skill


# ---------- rules DSL evaluator ----------


def _rule(d: dict) -> HardRule:
    return HardRule.model_validate(d)


def test_dsl_simple_triple_triggers():
    r = _rule({"id": "lev", "if": {"field": "debt_to_equity", "op": ">", "value": 1.0},
               "action": "cap_confidence", "confidence_ceiling": 0.5})
    [out] = evaluate_rules([r], {"debt_to_equity": 1.5})
    assert out.triggered and not out.not_evaluable
    [out] = evaluate_rules([r], {"debt_to_equity": 0.3})
    assert not out.triggered and not out.not_evaluable


def test_dsl_all_any_nesting():
    r = _rule({"id": "moat", "action": "bullish_floor", "confidence_floor": 0.6,
               "if": {"all": [{"field": "roe_5y_avg", "op": ">", "value": 0.20},
                              {"any": [{"field": "gross_margin_trend_5y", "op": ">=", "value": 0},
                                       {"field": "roe_pct", "op": ">", "value": 30}]}]}})
    [out] = evaluate_rules([r], {"roe_5y_avg": 0.25, "gross_margin_trend_5y": 0.1, "roe_pct": 10})
    assert out.triggered
    # all 的第二支 any 兩條都不成立 → 整體 false
    [out] = evaluate_rules([r], {"roe_5y_avg": 0.25, "gross_margin_trend_5y": -0.1, "roe_pct": 10})
    assert not out.triggered


def test_dsl_missing_field_is_not_evaluable():
    r = _rule({"id": "moat", "action": "bullish_floor", "confidence_floor": 0.6,
               "if": {"all": [{"field": "roe_5y_avg", "op": ">", "value": 0.20},
                              {"field": "gross_margin_trend_5y", "op": ">=", "value": 0}]}})
    # any 巢狀中任一引用欄位缺值 → 整條 not_evaluable（不觸發、不假裝通過）
    [out] = evaluate_rules([r], {"roe_5y_avg": 0.25})  # 缺 gross_margin_trend_5y
    assert out.not_evaluable and not out.triggered


# ---------- clamp（E1 實作決議 #1：floor 只夾 confidence、不翻 stance） ----------


def test_clamp_ceiling_is_hard_regardless_of_stance():
    out = evaluate_rules([_rule({"id": "lev", "if": {"field": "debt_to_equity", "op": ">",
                          "value": 1.0}, "action": "cap_confidence", "confidence_ceiling": 0.5})],
                         {"debt_to_equity": 2.0})
    conf, conflicts = clamp_confidence("bullish", 0.9, out)
    assert conf == 0.5 and conflicts == []


def test_clamp_floor_applies_only_when_stance_aligned():
    out = evaluate_rules([_rule({"id": "moat", "if": {"field": "roe_5y_avg", "op": ">",
                          "value": 0.2}, "action": "bullish_floor", "confidence_floor": 0.6,
                          "note": "寬護城河"})], {"roe_5y_avg": 0.3})
    # 同向：floor 生效
    conf, conflicts = clamp_confidence("bullish", 0.4, out)
    assert conf == 0.6 and conflicts == []
    # 反向：不翻 stance、不套 floor，記衝突揭露
    conf, conflicts = clamp_confidence("bearish", 0.4, out)
    assert conf == 0.4 and len(conflicts) == 1 and "moat" in conflicts[0]


# ---------- skill 框架 ----------


@skill(requires=["net_income_annual", "depreciation_amortization", "capex"])
def owner_earnings(ev) -> SkillResult:
    return SkillResult(value=ev["net_income_annual"] + ev["depreciation_amortization"]
                       - ev["capex"], formula="NI + D&A - CapEx")


@skill(requires=["net_income_annual", "shares"])
def needs_missing(ev) -> SkillResult:
    return SkillResult(value=ev["net_income_annual"] / ev["shares"], formula="NI / shares")


def _fund(field: str, value: float) -> Evidence:
    return Evidence(category="fundamentals", field=field, value=value, source="test")


def test_skill_pass_registers_traceable_private_evidence():
    store = EvidenceStore(ticker="X")
    store.add_all([_fund("net_income_annual", 1000.0),
                   _fund("depreciation_amortization", 600.0),
                   _fund("capex", 100.0)])
    private, not_eval = run_skills([owner_earnings], store, key="buffett")
    assert not_eval == []
    [ev] = private
    assert ev.id == "S-buffett-001" and ev.value == 1500.0 and ev.category == "derived"
    assert "NI + D&A - CapEx" in ev.note
    # inputs 由 accessor 自動回填為實際讀到的 evidence ids（不需手寫 E-id）
    assert all(eid in ev.note for eid in ("E001", "E002", "E003"))


def test_skill_missing_requires_is_not_evaluable():
    store = EvidenceStore(ticker="X")
    store.add(_fund("net_income_annual", 1000.0))  # 缺 shares
    private, not_eval = run_skills([needs_missing], store, key="lynch")
    assert private == [] and len(not_eval) == 1 and "shares" in not_eval[0]


# ---------- Pack loader（目錄 + 舊單檔共存） ----------


_SKILLS_PY = '''
from cyber_sages.personas.skill import skill, SkillResult

@skill(requires=["net_income_annual"])
def trivial(ev) -> SkillResult:
    return SkillResult(value=ev["net_income_annual"], formula="NI")

NOT_A_SKILL = 42
'''


def _write_pack(d, key="buffett", epoch=2019, weight=1.2):
    d.mkdir(parents=True, exist_ok=True)
    (d / "persona.yaml").write_text(
        f"key: {key}\nname: Warren Buffett\nweight: {weight}\nepoch: {epoch}\n"
        "philosophy: p\nfocus: f\nvoice: v\nweight_rationale: 試點\n"
        "sources: [股東信]\n", encoding="utf-8")
    (d / "rules.yaml").write_text(
        "hard_rules:\n  - id: no-leverage\n    if: {field: debt_to_equity, op: '>', value: 1.0}\n"
        "    action: cap_confidence\n    confidence_ceiling: 0.5\n"
        "exceptions:\n  - 壟斷市占可推翻 P/E 規則\n", encoding="utf-8")
    (d / "sop.yaml").write_text(
        "sop:\n  - step: moat\n    ask: 護城河在哪?\n    look_at: [fundamentals.roe_pct]\n"
        "  - step: verdict\n    ask: 下結論\n    use_skill: trivial\n", encoding="utf-8")
    (d / "skills.py").write_text(_SKILLS_PY, encoding="utf-8")


def test_load_pack_dir_parses_all_parts(tmp_path):
    d = tmp_path / "buffett-2019"
    _write_pack(d)
    p = _load_pack_dir(d)
    assert p.is_pack and p.epoch == 2019 and p.sources == ["股東信"]
    assert len(p.pack.hard_rules) == 1 and p.pack.hard_rules[0].id == "no-leverage"
    assert [s.step for s in p.pack.sop] == ["moat", "verdict"]
    assert p.pack.exceptions == ["壟斷市占可推翻 P/E 規則"]
    assert len(p.pack.skills) == 1 and p.pack.skills[0].name == "trivial"  # NOT_A_SKILL 不算


def test_load_personas_coexists_dir_and_legacy(tmp_path, monkeypatch):
    _write_pack(tmp_path / "buffett-2019", key="buffett", weight=1.2)
    (tmp_path / "taleb.yaml").write_text(
        "key: taleb\nname: Nassim Taleb\nweight: 0.8\nphilosophy: p\nfocus: f\nvoice: v\n",
        encoding="utf-8")
    monkeypatch.setattr(pack_module, "PERSONA_DIR", tmp_path)
    personas = pack_load_personas()
    assert [p.name for p in personas] == ["Warren Buffett", "Nassim Taleb"]  # weight 降序
    assert personas[0].is_pack and not personas[1].is_pack
    # --sages N 截斷邏輯不變（weight 高者優先）
    assert [p.name for p in pack_load_personas(1)] == ["Warren Buffett"]


# ---------- 三段執行（run_council 的 Pack 路徑） ----------


def _runtime_settings() -> SimpleNamespace:
    return SimpleNamespace(
        defaults=SimpleNamespace(sages=10),
        roles={"sage": SimpleNamespace(provider="p")},
        providers={"p": SimpleNamespace(has=lambda feat: False)},
        citation=SimpleNamespace(numeric_tolerance_pct=1.0),
    )


def _pack_persona() -> Persona:
    pack = PersonaPack(
        hard_rules=[HardRule.model_validate(
            {"id": "no-leverage", "if": {"field": "debt_to_equity", "op": ">", "value": 1.0},
             "action": "cap_confidence", "confidence_ceiling": 0.5, "note": "槓桿過高"})],
        sop=[SopStep(step="verdict", ask="下結論", use_skill="owner_earnings")],
        skills=[owner_earnings],
    )
    return Persona(key="buffett", name="Warren Buffett", weight=1.2, epoch=2019,
                   philosophy="p", focus="f", voice="v", pack=pack)


def _levered_store() -> EvidenceStore:
    store = EvidenceStore(ticker="LEV", market="US")
    store.add_all([_fund("net_income_annual", 1000.0),
                   _fund("depreciation_amortization", 600.0),
                   _fund("capex", 100.0),
                   _fund("debt_to_equity", 2.0)])  # 觸發 ceiling
    return store


def _gateway_returning(signal: SageSignal):
    class G:
        async def structured(self, role, *, system, prompt, schema, **kw):
            return signal.model_copy(deep=True)
    return G()


async def test_runtime_clamps_confidence_to_ceiling(monkeypatch):
    # 驗收條件：構造觸發 ceiling 的 evidence，LLM 給 0.9 也被壓到 0.5
    persona = _pack_persona()
    monkeypatch.setattr("cyber_sages.agents.council.load_personas", lambda limit=None: [persona])
    # owner_earnings = 1000 + 600 - 100 = 1500 → sop_trace 引用 private S-buffett-001
    llm = SageSignal(stance="bullish", confidence=0.9, thesis="t", what_would_change_my_mind="w",
                     sop_trace=[SopStepResult(step="verdict", conclusion="owner earnings 約 1,500",
                                              evidence_ids=["S-buffett-001"])])
    council = await run_council(_levered_store(), [], _runtime_settings(),
                                _gateway_returning(llm), n_sages=1)
    [s] = council.signals
    assert s.confidence == 0.5  # 0.9 被 ceiling 壓到 0.5
    assert s.unverified == []   # 1,500 對得上 private evidence → 通過
    assert s.not_evaluable == []  # skill 與 rule 都評得出來


async def test_runtime_soft_flags_unverifiable_sop_trace(monkeypatch):
    persona = _pack_persona()
    monkeypatch.setattr("cyber_sages.agents.council.load_personas", lambda limit=None: [persona])
    # sop_trace 宣稱 owner earnings 是 9,999（對不上 private 的 1,500）→ retry 後仍標 unverified
    llm = SageSignal(stance="bullish", confidence=0.3, thesis="t", what_would_change_my_mind="w",
                     sop_trace=[SopStepResult(step="verdict", conclusion="owner earnings 約 9,999",
                                              evidence_ids=["S-buffett-001"])])
    council = await run_council(_levered_store(), [], _runtime_settings(),
                                _gateway_returning(llm), n_sages=1)
    [s] = council.signals
    assert len(s.unverified) == 1 and s.unverified[0].kind == "num_mismatch"
    assert s.stance == "bullish"  # 不 refuse，仍出訊號


async def test_runtime_records_rule_conflict_when_floor_opposes_stance(monkeypatch):
    # bullish_floor 觸發但 LLM 判 bearish → 不翻 stance、不套 floor、記 conflict
    pack = PersonaPack(
        hard_rules=[HardRule.model_validate(
            {"id": "moat-premium", "if": {"field": "roe_pct", "op": ">", "value": 20},
             "action": "bullish_floor", "confidence_floor": 0.7, "note": "寬護城河"})],
        sop=[SopStep(step="verdict", ask="下結論")],
        skills=[],
    )
    persona = Persona(key="buffett", name="Warren Buffett", weight=1.0, epoch=2019,
                      philosophy="p", focus="f", voice="v", pack=pack)
    monkeypatch.setattr("cyber_sages.agents.council.load_personas", lambda limit=None: [persona])
    store = EvidenceStore(ticker="Q", market="US")
    store.add(_fund("roe_pct", 35.0))
    llm = SageSignal(stance="bearish", confidence=0.4, thesis="t", what_would_change_my_mind="w")
    council = await run_council(store, [], _runtime_settings(), _gateway_returning(llm), n_sages=1)
    [s] = council.signals
    assert s.stance == "bearish" and s.confidence == 0.4  # floor 沒套
    assert len(s.rule_conflicts) == 1 and "moat-premium" in s.rule_conflicts[0]


async def test_runtime_lists_not_evaluable_when_skill_inputs_missing(monkeypatch):
    pack = PersonaPack(
        hard_rules=[HardRule.model_validate(
            {"id": "needs-data", "if": {"field": "owner_earnings_yield", "op": ">", "value": 0.05},
             "action": "bullish_floor", "confidence_floor": 0.6})],
        sop=[SopStep(step="verdict", ask="下結論", use_skill="owner_earnings")],
        skills=[owner_earnings],
    )
    persona = Persona(key="buffett", name="Warren Buffett", weight=1.0, epoch=2019,
                      philosophy="p", focus="f", voice="v", pack=pack)
    monkeypatch.setattr("cyber_sages.agents.council.load_personas", lambda limit=None: [persona])
    store = EvidenceStore(ticker="Z", market="US")
    store.add(_fund("net_income_annual", 1000.0))  # 缺 D&A / capex / owner_earnings_yield
    llm = SageSignal(stance="neutral", confidence=0.3, thesis="t", what_would_change_my_mind="w")
    council = await run_council(store, [], _runtime_settings(), _gateway_returning(llm), n_sages=1)
    [s] = council.signals
    joined = " ".join(s.not_evaluable)
    assert "skill:owner_earnings" in joined and "rule:needs-data" in joined
