"""Issue #1 之 Issue 2（brief 列未過驗證清單）與 Issue 3（辯論論點級反駁）。"""

from types import SimpleNamespace

import pytest

from cyber_sages.agents.council import load_personas, run_council
from cyber_sages.agents.debate import (
    _citecheck_debate,
    _losing_side_reps,
    _merge_rebuttals,
    _outlier_theses_text,
    run_debate,
)
from cyber_sages.data.evidence import Evidence, EvidenceStore
from cyber_sages.agents.schemas import (
    AnalystReport,
    CouncilVerdict,
    DebateArgument,
    DebateVerdict,
    OutlierRebuttal,
    RebuttalArgument,
    SageSignal,
    SopStepResult,
    UnverifiedClaim,
)
from cyber_sages.report import render_analysts, render_council, render_debate
from cyber_sages.verify.citation_check import Claim


# ---------- Issue 2：tag 分類 ----------

def test_unverified_tag_maps_from_authoritative_kind():
    # tag 純映射自驗證層給的 kind，不再用 reason 子串猜
    assert UnverifiedClaim(text="x", kind="bad_ref").tag == "BAD_REF"
    assert UnverifiedClaim(text="x", kind="no_cite").tag == "NO_CITE"
    assert UnverifiedClaim(text="x", kind="num_mismatch").tag == "NUM_MISMATCH"


def test_render_analysts_shows_unverified_with_ids_and_tag():
    report = AnalystReport(
        analyst="Valuation Analyst", summary="估值偏高",
        claims=[Claim(text="P/E 30 倍", evidence_ids=["E001"])],
        unverified=[UnverifiedClaim(
            text="按股價 1167 計算本益比 15.7 倍", evidence_ids=["E001", "E023"],
            reason="numbers [1167.0, 15.7] not found in cited evidence")],
    )
    out = render_analysts(SimpleNamespace(ticker="2330", reports=[report]))
    assert "未通過引用驗證" in out
    assert "[NUM_MISMATCH]" in out          # tag 有呈現
    assert "E001 E023" in out               # evidence id 有列出
    assert "not found" in out               # 失敗原因有列出


def test_render_council_surfaces_rule_conflicts():
    # #87：硬規則收口衝突（cap 壓過同向 floor）浮上 details/council.md，不再只藏在 verdict.json。
    sig = SageSignal(
        sage="Benjamin Graham", stance="bullish", confidence=0.5,
        thesis="便宜但財務脆弱", key_evidence_ids=["E001"],
        what_would_change_my_mind="降槓桿",
        rule_conflicts=["weak-financials: 紅線上限 0.5（財務脆弱）壓過同向 defensive-bargain floor 0.6"],
    )
    council = SimpleNamespace(signals=[sig], bullish=1, neutral=0, bearish=0,
                             weighted_score=0.5, consensus="bullish")
    out = render_council(SimpleNamespace(ticker="CHEAP", council=council))
    assert "規則收口衝突" in out
    assert "壓過同向 defensive-bargain floor 0.6" in out


def test_render_council_no_conflict_block_when_clean():
    sig = SageSignal(sage="Buffett", stance="bullish", confidence=0.7, thesis="寬護城河",
                     key_evidence_ids=["E001"], what_would_change_my_mind="護城河變窄")
    council = SimpleNamespace(signals=[sig], bullish=1, neutral=0, bearish=0,
                             weighted_score=0.7, consensus="bullish")
    out = render_council(SimpleNamespace(ticker="MOAT", council=council))
    assert "規則收口衝突" not in out


def test_render_council_surfaces_unexpected_skill_errors_only():
    # #40：skill 的 unexpected 例外（coding bug）浮上報告；一般誠實降級（缺欄位）不顯示（避雜訊）。
    sig = SageSignal(
        sage="Warren Buffett", stance="bullish", confidence=0.4, thesis="t",
        key_evidence_ids=["E001"], what_would_change_my_mind="w",
        not_evaluable=["skill:owner_earnings (缺 market_cap)",
                       "skill:buggy (⚠ unexpected TypeError: 'float' object is not subscriptable)"],
    )
    council = SimpleNamespace(signals=[sig], bullish=1, neutral=0, bearish=0,
                             weighted_score=0.4, consensus="bullish")
    out = render_council(SimpleNamespace(ticker="X", council=council))
    assert "skill 程式錯誤" in out
    assert "buggy" in out and "TypeError" in out
    assert "缺 market_cap" not in out   # 誠實降級不上報告


def test_analyst_is_neutral_data_layer_no_outlook():
    # P8（Spec C v2）：analyst 是數據層、不下方向性立場——schema 無 outlook、render 無 stance 標。
    report = AnalystReport(analyst="Fundamentals Analyst", summary="ROE 35%",
                           claims=[Claim(text="ROE 35%", evidence_ids=["E001"])])
    assert not hasattr(report, "outlook")
    out = render_analysts(SimpleNamespace(ticker="NVDA", reports=[report]))
    assert "方向性判斷見大師合議" in out          # 中性框架語有呈現
    for stance in ("看多", "看空", "中性"):       # analyst 段不再出現 stance 標籤
        assert stance not in out


def test_analyst_system_prompt_states_neutrality_rule():
    # P8 prompt 護欄：未來改 prompt 時不可靜默丟失「analyst 供數據非意見」鐵律。
    from cyber_sages.agents.analysts import ANALYST_SYSTEM
    low = ANALYST_SYSTEM.lower()
    assert "data" in low and "not opinion" in low
    assert "buy/sell" in low or "bullish/bearish" in low


def test_sage_prompt_forbids_restating_neutral_reason():
    # #53-E prompt 護欄：neutral 大師不得在 thesis 複述中性理由（與 neutral_reason enum 冗餘）。
    from cyber_sages.agents.council import SAGE_SHARED_SYSTEM
    assert "neutral_reason` ONLY" in SAGE_SHARED_SYSTEM
    assert "do NOT restate it in `thesis`" in SAGE_SHARED_SYSTEM


# ---------- Issue 3：敗方離群者需論點級反駁 ----------

def _council() -> CouncilVerdict:
    return CouncilVerdict(
        signals=[
            SageSignal(sage="Cathie Wood", stance="bullish", confidence=0.8,
                       thesis="Wright's Law 在 S 曲線起爆合理", what_would_change_my_mind="a"),
            SageSignal(sage="Buffett", stance="bearish", confidence=0.7,
                       thesis="估值透支", what_would_change_my_mind="b"),
        ],
        outliers=["Cathie Wood"],
    )


def test_losing_side_reps_two_sided():
    # P6 雙邊：敗方核心論點皆須反駁，不論敗方是多數或少數
    c = _council()
    assert _losing_side_reps(c, "bear") == ["Cathie Wood"]  # 多方敗 → 多方代表需反駁
    assert _losing_side_reps(c, "bull") == ["Buffett"]      # 空方敗 → 空方代表需反駁（舊版漏）
    assert _losing_side_reps(c, "draw") == []               # 平手不強制


def test_losing_side_reps_minority_winner_defends_losing_majority():
    # 少數派勝出：5B/1S，judge 判 bear（少數）勝 → 落敗的多數 bull 也須被守住（取信心前 3）
    sigs = [SageSignal(sage=f"B{i}", stance="bullish", confidence=0.5 + i * 0.05,
                       thesis="多", what_would_change_my_mind="x") for i in range(5)]
    sigs.append(SageSignal(sage="S1", stance="bearish", confidence=0.9,
                           thesis="空", what_would_change_my_mind="y"))
    c = CouncilVerdict(signals=sigs, consensus="bullish")
    reps = _losing_side_reps(c, "bear")  # bear 勝 → bull 敗
    assert len(reps) == 3                       # cap 3 代表（不要求反駁一整票）
    assert reps == ["B4", "B3", "B2"]           # 信心最高的前三位多方


def test_losing_side_reps_skips_neutral():
    # 辯論軸是 bull vs bear，中性者無軸可歸，任何 winner 都不入敗方代表
    c = CouncilVerdict(
        signals=[SageSignal(sage="Switzerland", stance="neutral", confidence=0.5,
                            thesis="觀望", what_would_change_my_mind="x",
                            neutral_reason="balanced_forces")])
    assert _losing_side_reps(c, "bull") == []
    assert _losing_side_reps(c, "bear") == []


def test_outlier_theses_text_lists_only_known_signals():
    c = _council()
    txt = _outlier_theses_text(c, ["Cathie Wood", "Ghost"])  # Ghost 無 signal → 略過
    assert "Cathie Wood" in txt and "Wright's Law" in txt and "Ghost" not in txt


def test_merge_rebuttals_discloses_remaining_gap():
    A = OutlierRebuttal(sage="A", thesis_point="a", rebuttal="ra")
    B = OutlierRebuttal(sage="B", thesis_point="b", rebuttal="rb")
    # 補打只補了 B，C 仍缺 → unrebutted 必須揭露 C（這正是 review I-1 的洞）
    merged, unrebutted = _merge_rebuttals([A], [B], needed=["A", "B", "C"])
    assert {r.sage for r in merged} == {"A", "B"}
    assert unrebutted == ["C"]


def test_merge_rebuttals_prefers_original_for_same_sage():
    orig = OutlierRebuttal(sage="A", thesis_point="a", rebuttal="原判反駁")
    retry = OutlierRebuttal(sage="A", thesis_point="a", rebuttal="補打反駁")
    merged, unrebutted = _merge_rebuttals([orig], [retry], needed=["A"])
    assert len(merged) == 1 and merged[0].rebuttal == "原判反駁"
    assert unrebutted == []


class _FakeDebateGateway:
    """debater 回固定陳詞；judge 依腳本依序回 verdict，並計次。"""
    def __init__(self, judge_verdicts):
        self._judge = list(judge_verdicts)
        self.judge_calls = 0
        self.debater_calls = 0

    async def structured(self, role, *, system, prompt, schema, **kw):
        if role == "debater":
            self.debater_calls += 1
            if schema.__name__ == "RebuttalArgument":
                return RebuttalArgument(rebuttal="反駁 [E001]")
            return DebateArgument(side="bull", argument="陳詞 [E001]")
        if role == "judge":
            v = self._judge[self.judge_calls]
            self.judge_calls += 1
            return v
        raise AssertionError(f"unexpected role {role}")


def _store_council():
    store = EvidenceStore(ticker="2330", market="TW")
    store.add(Evidence(category="quote", field="latest_close", value=2310.0,
                       unit="TWD", source="x"))
    council = CouncilVerdict(
        signals=[
            SageSignal(sage="Cathie Wood", stance="bullish", confidence=0.8,
                       thesis="Wright's Law", what_would_change_my_mind="a"),
            SageSignal(sage="Buffett", stance="bearish", confidence=0.7,
                       thesis="貴", what_would_change_my_mind="b"),
        ],
        outliers=["Cathie Wood"], consensus="bearish")
    return store, council


def _verdict(winner="bear", rebuttals=()):
    return DebateVerdict(winner=winner, rationale="r", strongest_bull_point="b",
                         strongest_bear_point="s", outlier_rebuttals=list(rebuttals))


async def test_run_debate_no_retry_when_complete():
    store, council = _store_council()
    rb = OutlierRebuttal(sage="Cathie Wood", thesis_point="x", rebuttal="y", evidence_ids=["E001"])
    gw = _FakeDebateGateway([_verdict(rebuttals=[rb])])
    _, _, v = await run_debate(store, [], council, None, gw)
    assert gw.judge_calls == 1            # 首打已完整 → 不補打
    assert v.unrebutted_outliers == []


async def test_run_debate_retry_merges_and_preserves_winner():
    store, council = _store_council()
    rb = OutlierRebuttal(sage="Cathie Wood", thesis_point="x", rebuttal="y", evidence_ids=["E001"])
    # 首打缺反駁且 retry 想改判 bull → 應補上反駁但 winner 維持原判 bear
    gw = _FakeDebateGateway([_verdict(winner="bear"), _verdict(winner="bull", rebuttals=[rb])])
    _, _, v = await run_debate(store, [], council, None, gw)
    assert gw.judge_calls == 2
    assert v.winner == "bear"                              # 補打未改判
    assert {r.sage for r in v.outlier_rebuttals} == {"Cathie Wood"}
    assert v.unrebutted_outliers == []


async def test_run_debate_discloses_when_retry_still_incomplete():
    store, council = _store_council()
    gw = _FakeDebateGateway([_verdict(winner="bear"), _verdict(winner="bear")])  # 兩次都缺
    _, _, v = await run_debate(store, [], council, None, gw)
    assert gw.judge_calls == 2
    assert v.unrebutted_outliers == ["Cathie Wood"]        # fail-loud 揭露


def test_render_debate_shows_outlier_rebuttals():
    verdict = DebateVerdict(
        winner="bear", rationale="空方證據較紮實",
        strongest_bull_point="AI 需求", strongest_bear_point="估值",
        outlier_rebuttals=[OutlierRebuttal(
            sage="Cathie Wood", thesis_point="Wright's Law 成本下降撐估值",
            rebuttal="毛利率已在高位、Wright's Law 的成本曲線未反映先進製程資本支出",
            evidence_ids=["E018", "E047"])],
    )
    result = SimpleNamespace(
        ticker="2330", debate=verdict,
        bull=DebateArgument(side="bull", argument="多方"),
        bear=DebateArgument(side="bear", argument="空方"),
    )
    out = render_debate(result)
    assert "逐點反駁" in out
    assert "Cathie Wood" in out and "Wright's Law" in out
    assert "E018, E047" in out


def test_render_debate_warns_on_unrebutted_outliers():
    verdict = DebateVerdict(winner="bear", rationale="r", strongest_bull_point="b",
                            strongest_bear_point="s", unrebutted_outliers=["Druckenmiller"])
    result = SimpleNamespace(
        ticker="2330", debate=verdict,
        bull=DebateArgument(side="bull", argument="多"),
        bear=DebateArgument(side="bear", argument="空"))
    out = render_debate(result)
    assert "未對" in out and "Druckenmiller" in out  # fail-loud 警告有呈現


def test_render_debate_shows_blind_opening_and_rebuttal():
    # P3：開場（盲打）與反駁（見對手後）分段呈現
    result = SimpleNamespace(
        ticker="2330",
        debate=DebateVerdict(winner="draw", rationale="r", strongest_bull_point="b",
                             strongest_bear_point="s"),
        bull=DebateArgument(side="bull", argument="多方開場", rebuttal="多方反駁空方"),
        bear=DebateArgument(side="bear", argument="空方開場", rebuttal="空方反駁多方"))
    out = render_debate(result)
    assert "開場（盲打）" in out and "多方開場" in out and "空方開場" in out
    assert "多方反駁空方" in out and "空方反駁多方" in out


# ---------- P3 雙盲：run_debate 第一輪互不可見對手論點 ----------


class _BlindCheckGateway:
    """記錄每次 debater prompt，驗第一輪盲打時 prompt 不含對手開場文字。"""
    def __init__(self):
        self.debater_prompts: list[str] = []

    async def structured(self, role, *, system, prompt, schema, **kw):
        if role == "debater":
            self.debater_prompts.append(prompt)
            if schema.__name__ == "RebuttalArgument":   # 第二輪反駁
                return RebuttalArgument(rebuttal="REBUTTAL_TOKEN [E001]")
            side = "bull" if "BULL opening" in prompt else "bear"
            return DebateArgument(side=side, argument=f"{side.upper()}_ARGUMENT_TOKEN [E001]")
        if role == "judge":
            return DebateVerdict(winner="draw", rationale="r", strongest_bull_point="b",
                                 strongest_bear_point="s")
        raise AssertionError(role)


async def test_run_debate_first_round_is_double_blind():
    store, council = _store_council()
    gw = _BlindCheckGateway()
    bull, bear, _ = await run_debate(store, [], council, None, gw)
    # 4 次 debater 呼叫：2 開場（盲打）+ 2 反駁
    assert len(gw.debater_prompts) == 4
    openings = gw.debater_prompts[:2]
    # 第一輪兩個開場 prompt 都不可含對手論點 token（盲打）
    assert all("ARGUMENT_TOKEN" not in p for p in openings)
    # 第二輪反駁 prompt 必含對手開場（見過對手才反駁）
    assert any("ARGUMENT_TOKEN" in p for p in gw.debater_prompts[2:])
    # 兩方都產出了反駁（對稱）
    assert bull.rebuttal and bear.rebuttal


# ---------- #53-D：debate 兩輪文字 cite-check（軟揭露） ----------


def _citecheck_store():
    store = EvidenceStore(ticker="X", market="US")
    store.add(Evidence(category="fundamentals", field="gross_margin_pct",
                       value=35.0, unit="%", source="t"))  # → E001
    return store


def test_debate_citecheck_flags_fabricated_number():
    # 開場引用 E001：35.0% 對得上，但 999.9 對不上 → num_mismatch；反駁無數字 → 過
    store = _citecheck_store()
    cfg = SimpleNamespace(numeric_tolerance_pct=1.0)
    arg = DebateArgument(side="bull",
                         argument="毛利率 35.0% 穩健 [E001]，但每股賺 999.9 元 [E001]",
                         rebuttal="趨勢轉強 [E001]")
    uv = _citecheck_debate(arg, store, cfg)
    assert len(uv) == 1 and uv[0].kind == "num_mismatch"
    assert "開場" in uv[0].text  # 標出是哪一輪


def test_debate_citecheck_passes_grounded_numbers():
    # 數字皆對得上 cited evidence → 無 unverified
    store = _citecheck_store()
    cfg = SimpleNamespace(numeric_tolerance_pct=1.0)
    arg = DebateArgument(side="bear", argument="毛利率 35.0% [E001]", rebuttal="")
    assert _citecheck_debate(arg, store, cfg) == []


def test_debate_citecheck_flags_uncited_number():
    # 有意義數字但整段無 [E0xx] 引用 → no_cite
    store = _citecheck_store()
    cfg = SimpleNamespace(numeric_tolerance_pct=1.0)
    arg = DebateArgument(side="bull", argument="毛利率高達 35.0%（沒給引用）", rebuttal="")
    uv = _citecheck_debate(arg, store, cfg)
    assert len(uv) == 1 and uv[0].kind == "no_cite"


def test_debate_citecheck_ignores_evidence_id_digits():
    # 質性 claim（只有 [E001] 引用、無真實數字）→ 通過；E001 的 "001" 不被當數字驗證
    store = _citecheck_store()
    cfg = SimpleNamespace(numeric_tolerance_pct=1.0)
    arg = DebateArgument(side="bull", argument="動能轉強、趨勢完好 [E001]", rebuttal="")
    assert _citecheck_debate(arg, store, cfg) == []


def test_debate_evid_regex_matches_4plus_digit_ids():
    # #78 review：evidence > 999 時 id 為 E1000+，regex 不可 silent 截成 E100
    from cyber_sages.agents.debate import _EVID_RE
    assert _EVID_RE.findall("見 [E1000] 與 [E007]") == ["E1000", "E007"]


# ---------- D-2：裁定 rationale + 反駁 cite-check（軟揭露） ----------


def test_judge_citecheck_flags_fabricated_rationale_number():
    # rationale 引用 E001（=35.0%）但寫 999.9 → num_mismatch
    from cyber_sages.agents.debate import _citecheck_judge
    store = _citecheck_store()
    cfg = SimpleNamespace(numeric_tolerance_pct=1.0)
    v = DebateVerdict(winner="bear", rationale="毛利率僅 999.9% [E001]，難撐估值",
                      strongest_bull_point="b", strongest_bear_point="s")
    uv = _citecheck_judge(v, store, cfg)
    assert len(uv) == 1 and uv[0].kind == "num_mismatch"
    assert "裁定" in uv[0].text


def test_judge_citecheck_checks_rebuttal_with_struct_and_inline_ids():
    # 反駁取「結構欄 evidence_ids ∪ 行內 [E0xx]」；35.0% 對得上 E001 → 過
    from cyber_sages.agents.debate import _citecheck_judge
    store = _citecheck_store()
    cfg = SimpleNamespace(numeric_tolerance_pct=1.0)
    v = DebateVerdict(
        winner="bull", rationale="多方紮實", strongest_bull_point="b",
        strongest_bear_point="s",
        outlier_rebuttals=[OutlierRebuttal(
            sage="Buffett", thesis_point="估值貴",
            rebuttal="毛利率 35.0% 撐得住", evidence_ids=["E001"])],
    )
    assert _citecheck_judge(v, store, cfg) == []


def test_judge_citecheck_flags_uncited_rebuttal_number():
    # 反駁有數字但結構欄與行內都無 id → no_cite
    from cyber_sages.agents.debate import _citecheck_judge
    store = _citecheck_store()
    cfg = SimpleNamespace(numeric_tolerance_pct=1.0)
    v = DebateVerdict(
        winner="bull", rationale="多方紮實", strongest_bull_point="b",
        strongest_bear_point="s",
        outlier_rebuttals=[OutlierRebuttal(
            sage="Buffett", thesis_point="估值貴", rebuttal="毛利率 35.0% 撐得住")],
    )
    uv = _citecheck_judge(v, store, cfg)
    assert len(uv) == 1 and uv[0].kind == "no_cite" and "Buffett" in uv[0].text


def test_judge_citecheck_qualitative_passes():
    # rationale/反駁皆無有意義數字 → 無 claim → 過
    from cyber_sages.agents.debate import _citecheck_judge
    store = _citecheck_store()
    cfg = SimpleNamespace(numeric_tolerance_pct=1.0)
    v = DebateVerdict(winner="draw", rationale="雙方勢均力敵 [E001]",
                      strongest_bull_point="b", strongest_bear_point="s")
    assert _citecheck_judge(v, store, cfg) == []


async def test_run_debate_backfills_judge_unverified():
    # run_debate 末端把 _citecheck_judge 結果回填到 verdict.unverified（軟揭露）
    store, council = _store_council()  # E001 = latest_close 2310.0
    rb = OutlierRebuttal(sage="Cathie Wood", thesis_point="x", rebuttal="y", evidence_ids=["E001"])
    bad = DebateVerdict(winner="bear", rationale="收盤僅 99.9 元 [E001] 太貴",
                        strongest_bull_point="b", strongest_bear_point="s",
                        outlier_rebuttals=[rb])
    gw = _FakeDebateGateway([bad])
    cfg = SimpleNamespace(numeric_tolerance_pct=1.0)
    _, _, v = await run_debate(store, [], council,
                              SimpleNamespace(citation=cfg), gw)
    assert v.unverified and v.unverified[0].kind == "num_mismatch"


def test_render_debate_shows_judge_unverified():
    from cyber_sages.report import render_debate
    verdict = DebateVerdict(
        winner="bear", rationale="r", strongest_bull_point="b", strongest_bear_point="s",
        unverified=[UnverifiedClaim(text="[裁定] x", kind="num_mismatch",
                                    reason="numbers not found")],
    )
    result = SimpleNamespace(
        ticker="2330", debate=verdict,
        bull=DebateArgument(side="bull", argument="多"),
        bear=DebateArgument(side="bear", argument="空"))
    out = render_debate(result)
    assert "裁定/反駁未過引用驗證" in out and "NUM_MISMATCH" in out


# ---------- P7：neutral 三類獨立訊號 ----------


def _neutral_settings():
    return SimpleNamespace(
        defaults=SimpleNamespace(sages=10),
        roles={"sage": SimpleNamespace(provider="p")},
        providers={"p": SimpleNamespace(has=lambda feat: False)},
        citation=SimpleNamespace(numeric_tolerance_pct=1.0))


def _neutral_gateway(reason):
    class G:
        async def structured(self, role, *, system, prompt, schema, **kw):
            return SageSignal(stance="neutral", confidence=0.4, thesis="觀望",
                              what_would_change_my_mind="x", neutral_reason=reason)
    return G()


async def test_council_neutral_reason_fallback_and_tally(monkeypatch):
    from cyber_sages.agents.council import run_council as rc
    from cyber_sages.personas.pack import Persona
    ps = [Persona(key=f"k{i}", name=f"N{i}", philosophy="p", focus="f", voice="v")
          for i in range(2)]
    monkeypatch.setattr("cyber_sages.agents.council.load_personas", lambda limit=None: ps)
    # LLM 漏填 neutral_reason → 程式回填 insufficient_signal（保守）
    gw_missing = _neutral_gateway(None)
    c = await rc(EvidenceStore(ticker="X", market="US"), [], _neutral_settings(), gw_missing)
    assert c.neutral == 2
    assert c.neutral_by_reason == {"insufficient_signal": 2}
    assert all(s.neutral_reason == "insufficient_signal" for s in c.signals)


async def test_council_neutral_reason_preserved_when_supplied(monkeypatch):
    from cyber_sages.agents.council import run_council as rc
    from cyber_sages.personas.pack import Persona
    ps = [Persona(key="k", name="N", philosophy="p", focus="f", voice="v")]
    monkeypatch.setattr("cyber_sages.agents.council.load_personas", lambda limit=None: ps)
    c = await rc(EvidenceStore(ticker="X", market="US"), [], _neutral_settings(),
                 _neutral_gateway("balanced_forces"), n_sages=1)
    assert c.neutral_by_reason == {"balanced_forces": 1}


# ---------- Council 韌性：單一大師失敗不該拖垮全場 ----------


def _council_settings(caches: bool = False) -> SimpleNamespace:
    # run_council 會查 sage provider 是否支援 cache_control 來決定要不要先暖快取再 fan-out
    return SimpleNamespace(
        defaults=SimpleNamespace(sages=10),
        roles={"sage": SimpleNamespace(provider="p")},
        providers={"p": SimpleNamespace(has=lambda feat: caches)},
    )


def _fake_gateway(fail_names: set[str], order: list[str] | None = None):
    """structured() 對 fail_names 內的 persona 拋錯（模擬 3 次仍截斷），其餘回傳合法訊號。"""
    class G:
        async def structured(self, role, *, system, prompt, schema, **kw):
            # persona 身分在 system（"You are {name}. ..."）；證據摘要在 kw["cache_prefix"]
            name = system.split(".", 1)[0].replace("You are ", "").strip()
            if order is not None:
                order.append(name)
            if name in fail_names:
                raise RuntimeError("Structured output failed after 3 attempts")
            extra = {}
            if schema is SageSignal:
                # pack 大師走完 SOP（非空 trace）→ 不觸發 #41 空 trace 重試；
                # 空結論不產 claim、跳過 cite-check（本測試只驗席位/順序，不驗引用）。
                extra["sop_trace"] = [SopStepResult(step="s", conclusion="", evidence_ids=[])]
            return schema(stance="neutral", confidence=0.5, thesis="t",
                          what_would_change_my_mind="w", **extra)
    return G()


async def test_run_council_drops_failed_sage_records_absent():
    store = EvidenceStore(ticker="0050", market="TW")
    doomed = load_personas(4)[0].name  # 取一個確實被席的大師讓它失敗
    council = await run_council(
        store, [], _council_settings(), _fake_gateway({doomed}), n_sages=4
    )
    assert doomed in council.absent
    assert doomed not in [s.sage for s in council.signals]
    assert len(council.signals) == 3  # 4 席 - 1 缺席


async def test_run_council_raises_when_quorum_lost():
    store = EvidenceStore(ticker="0050", market="TW")
    # 4 席中 3 席失敗 → 未過半 → fail-loud 報錯
    seated = [p.name for p in load_personas(4)]
    with pytest.raises(RuntimeError, match="Council failed"):
        await run_council(
            store, [], _council_settings(), _fake_gateway(set(seated[:3])), n_sages=4
        )


async def test_run_council_warms_cache_with_first_sage_when_provider_caches():
    # provider 支援快取時：先跑首位（最高權重）把共享前綴寫進快取，其餘才平行讀取
    store = EvidenceStore(ticker="0050", market="TW")
    order: list[str] = []
    seated = [p.name for p in load_personas(4)]
    await run_council(
        store, [], _council_settings(caches=True), _fake_gateway(set(), order), n_sages=4
    )
    assert order[0] == seated[0]  # 首位先完成（暖快取），不與其餘同時併發
    assert sorted(order) == sorted(seated)


async def test_run_council_survives_braces_in_reports():
    # I-1 迴歸：analyst 報告含字面 {}（LLM 偶爾輸出 placeholder）時，shared_system 組裝
    # 不可炸——舊 .format() 會 raise KeyError/IndexError，現改 .replace
    from cyber_sages.verify.citation_check import Claim
    store = EvidenceStore(ticker="2330", market="TW")
    report = AnalystReport(
        analyst="估值分析師", summary="模板 {x} 與 {未知欄位} 都不該炸",
        claims=[Claim(text="毛利率 {gross_margin} 約 50%", evidence_ids=["E001"])],
    )
    council = await run_council(
        store, [report], _council_settings(), _fake_gateway(set()), n_sages=2
    )
    assert len(council.signals) == 2  # 沒炸、兩位都出訊號
