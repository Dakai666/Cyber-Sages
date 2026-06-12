"""Issue #1 之 Issue 2（brief 列未過驗證清單）與 Issue 3（辯論論點級反駁）。"""

from types import SimpleNamespace

from cyber_sages.agents.debate import (
    _merge_rebuttals,
    _outlier_theses_text,
    _outliers_needing_rebuttal,
    run_debate,
)
from cyber_sages.data.evidence import Evidence, EvidenceStore
from cyber_sages.agents.schemas import (
    AnalystReport,
    CouncilVerdict,
    DebateArgument,
    DebateVerdict,
    OutlierRebuttal,
    SageSignal,
    UnverifiedClaim,
)
from cyber_sages.report import render_analysts, render_debate
from cyber_sages.verify.citation_check import Claim


# ---------- Issue 2：tag 分類 ----------

def test_unverified_tag_maps_from_authoritative_kind():
    # tag 純映射自驗證層給的 kind，不再用 reason 子串猜
    assert UnverifiedClaim(text="x", kind="bad_ref").tag == "BAD_REF"
    assert UnverifiedClaim(text="x", kind="no_cite").tag == "NO_CITE"
    assert UnverifiedClaim(text="x", kind="num_mismatch").tag == "NUM_MISMATCH"


def test_render_analysts_shows_unverified_with_ids_and_tag():
    report = AnalystReport(
        analyst="Valuation Analyst", summary="估值偏高", outlook="bearish",
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


def test_outliers_needing_rebuttal_only_losing_side():
    c = _council()
    assert _outliers_needing_rebuttal(c, "bear") == ["Cathie Wood"]  # 多頭離群者敗 → 需反駁
    assert _outliers_needing_rebuttal(c, "bull") == []               # 離群者在勝方 → 不需
    assert _outliers_needing_rebuttal(c, "draw") == []               # 平手不強制


def test_neutral_outlier_never_needs_rebuttal():
    # 辯論軸是 bull vs bear，中性離群者無軸可歸，任何 winner 都不需反駁
    c = CouncilVerdict(
        signals=[SageSignal(sage="Switzerland", stance="neutral", confidence=0.5,
                            thesis="觀望", what_would_change_my_mind="x")],
        outliers=["Switzerland"])
    assert _outliers_needing_rebuttal(c, "bull") == []
    assert _outliers_needing_rebuttal(c, "bear") == []


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
    assert "論點級反駁" in out
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


# ---------- Council 韌性：單一大師失敗不該拖垮全場 ----------

import pytest

from cyber_sages.agents.council import run_council
from cyber_sages.data.evidence import EvidenceStore


def _fake_gateway(fail_names: set[str]):
    """structured() 對 fail_names 內的 persona 拋錯（模擬 3 次仍截斷），其餘回傳合法訊號。"""
    class G:
        async def structured(self, role, *, system, prompt, schema, **kw):
            name = system.split(",", 1)[0].replace("You are ", "").strip()
            if name in fail_names:
                raise RuntimeError("Structured output failed after 3 attempts")
            return schema(stance="neutral", confidence=0.5, thesis="t",
                          what_would_change_my_mind="w")
    return G()


async def test_run_council_drops_failed_sage_records_absent():
    from cyber_sages.agents.council import load_personas
    store = EvidenceStore(ticker="0050", market="TW")
    settings = SimpleNamespace(defaults=SimpleNamespace(sages=10))
    doomed = load_personas(4)[0].name  # 取一個確實被席的大師讓它失敗
    council = await run_council(
        store, [], settings, _fake_gateway({doomed}), n_sages=4
    )
    assert doomed in council.absent
    assert doomed not in [s.sage for s in council.signals]
    assert len(council.signals) == 3  # 4 席 - 1 缺席


async def test_run_council_raises_when_quorum_lost():
    store = EvidenceStore(ticker="0050", market="TW")
    settings = SimpleNamespace(defaults=SimpleNamespace(sages=10))
    # 4 席中 3 席失敗 → 未過半 → fail-loud 報錯
    from cyber_sages.agents.council import load_personas
    seated = [p.name for p in load_personas(4)]
    with pytest.raises(RuntimeError, match="Council failed"):
        await run_council(
            store, [], settings, _fake_gateway(set(seated[:3])), n_sages=4
        )
