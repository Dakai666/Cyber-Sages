"""Issue #1 之 Issue 2（brief 列未過驗證清單）與 Issue 3（辯論論點級反駁）。"""

from types import SimpleNamespace

from cyber_sages.agents.debate import _outliers_needing_rebuttal
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

def test_unverified_tag_classification():
    assert UnverifiedClaim(text="x", reason="cites nonexistent evidence id E9").tag == "BAD_REF"
    assert UnverifiedClaim(text="x", reason="no evidence cited").tag == "NO_CITE"
    assert UnverifiedClaim(text="x", reason="numbers [1.0] not found").tag == "NUM_MISMATCH"


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
