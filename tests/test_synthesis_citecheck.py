"""W7 — chief brief 主體（thesis / 風險 / 翻盤條件）過 cite-check。"""

from cyber_sages.agents.schemas import (
    ActionPlan,
    CouncilVerdict,
    FinalVerdict,
    RiskNote,
)
from types import SimpleNamespace

from cyber_sages.agents.synthesis import run_synthesis
from cyber_sages.config import CitationConfig
from cyber_sages.data.evidence import Evidence, EvidenceStore
from cyber_sages.verify.data_audit import AuditReport


def _store() -> EvidenceStore:
    store = EvidenceStore(ticker="TEST")
    store.add(Evidence(category="quote", field="trailing_pe", value=35.3,
                       source="yfinance"))
    store.add(Evidence(category="quote", field="last_price", value=250.0,
                       unit="USD", source="yfinance"))
    return store


def _verdict(thesis: str) -> FinalVerdict:
    return FinalVerdict(
        stance="neutral", conviction=0.5,
        action_plan=ActionPlan(action="hold", directive="觀望"),
        thesis=thesis,
    )


class _ChiefGateway:
    """chief 依腳本依序回 verdict 並計次；risk 回中性無調整。"""
    def __init__(self, theses):
        self._theses = list(theses)
        self.chief_calls = 0

    async def structured(self, role, *, system, prompt, schema, **kw):
        if role == "chief":
            t = self._theses[min(self.chief_calls, len(self._theses) - 1)]
            self.chief_calls += 1
            return _verdict(t)
        if role == "risk":
            return RiskNote(concerns=[], data_quality_acceptable=True,
                            conviction_adjustment=0.0)
        raise AssertionError(f"unexpected role {role}")


def _settings():
    # run_synthesis 只取用 settings.citation
    return SimpleNamespace(citation=CitationConfig())


async def test_chief_recheck_recovers_on_retry():
    # D-1：首版數字無行內引用 → no_cite → 重寫後改用可溯的 35.3x [E001]（=trailing_pe）→ 乾淨
    gw = _ChiefGateway(["預估本益比達 99.9x，明顯偏貴", "本益比 35.3x [E001]，估值偏高"])
    verdict, _ = await run_synthesis(
        _store(), [], CouncilVerdict(signals=[]), None,
        AuditReport(), _settings(), gw,  # type: ignore[arg-type]
    )
    assert gw.chief_calls == 2          # retry 觸發了一次
    assert verdict.unverified == []     # 修正後乾淨


async def test_chief_recheck_discloses_when_persistent():
    # D-1：兩版都引 [E001] 但數字 99.9x 對不上其值 35.3 → num_mismatch（不 refuse、不報廢）
    gw = _ChiefGateway(["本益比 99.9x [E001]，貴", "本益比 99.9x [E001]，仍貴"])
    verdict, _ = await run_synthesis(
        _store(), [], CouncilVerdict(signals=[]), None,
        AuditReport(), _settings(), gw,  # type: ignore[arg-type]
    )
    assert gw.chief_calls == 2          # 用完 1 次 retry
    assert verdict.unverified            # fail-loud 揭露
    assert verdict.unverified[0].kind == "num_mismatch"
    assert verdict.unverified[0].evidence_ids == ["E001"]  # 回填實際引用的 id


async def test_chief_uncited_number_flagged_no_cite():
    # D-1：有意義數字但整段無 [E0xx] 行內引用 → no_cite（量化宣稱一律要錨點）
    gw = _ChiefGateway(["本益比高達 35.3 倍（沒給引用）"] * 2)
    verdict, _ = await run_synthesis(
        _store(), [], CouncilVerdict(signals=[]), None,
        AuditReport(), _settings(), gw,  # type: ignore[arg-type]
    )
    assert verdict.unverified and verdict.unverified[0].kind == "no_cite"


class _RiskGateway:
    """chief 回固定乾淨 thesis（計次）；risk 回可設定的 RiskNote。"""
    def __init__(self, risk_adj, risk_reason="風控理由",
                 thesis="本益比 35.3x [E001]，估值合理"):
        self.risk_adj = risk_adj
        self.risk_reason = risk_reason
        self.thesis = thesis
        self.chief_calls = 0

    async def structured(self, role, *, system, prompt, schema, **kw):
        if role == "chief":
            self.chief_calls += 1
            return _verdict(self.thesis)
        if role == "risk":
            return RiskNote(concerns=["論點集中於單一假設"], data_quality_acceptable=True,
                            conviction_adjustment=self.risk_adj,
                            adjustment_reason=self.risk_reason)
        raise AssertionError(f"unexpected role {role}")


async def _run(gw):
    return await run_synthesis(
        _store(), [], CouncilVerdict(signals=[]), None,
        AuditReport(), _settings(), gw,  # type: ignore[arg-type]
    )


async def test_risk_positive_adjustment_clamped_and_applied():
    # D-3：風控官上調 +0.5 → clamp 到 +0.2 → conviction 0.5 + 0.2 = 0.7
    gw = _RiskGateway(risk_adj=0.5)
    verdict, risk = await _run(gw)
    assert risk.clamped_adjustment == 0.2
    assert verdict.conviction == 0.7
    assert gw.chief_calls == 1            # 非重大質疑 → 不觸發 D-4 迭代


async def test_risk_negative_adjustment_clamped():
    # D-3：下調 -0.9 → clamp 到 -0.3 → conviction 0.5 - 0.3 = 0.2
    gw = _RiskGateway(risk_adj=-0.9)
    verdict, risk = await _run(gw)
    assert risk.clamped_adjustment == -0.3
    assert verdict.conviction == 0.2


async def test_chief_risk_iteration_triggers_on_strong_concern():
    # D-4：clamped 調整 ≤ -0.2（重大質疑）→ chief 自動做 1 輪回應 → chief 被呼叫兩次
    gw = _RiskGateway(risk_adj=-0.25)
    _, _ = await _run(gw)
    assert gw.chief_calls == 2            # 初稿 1 + D-4 回應 1


async def test_chief_risk_no_iteration_on_mild_concern():
    # D-4：小幅下調（> -0.2）不觸發迭代——固定 1 輪只在重大質疑時花這筆成本
    gw = _RiskGateway(risk_adj=-0.1)
    _, _ = await _run(gw)
    assert gw.chief_calls == 1


async def test_chief_qualitative_thesis_needs_no_retry():
    # 純質性論述（無有意義數字）→ 一次過、無 retry、無 unverified
    gw = _ChiefGateway(["基本面穩健、護城河深厚，但缺乏短線催化"])
    verdict, _ = await run_synthesis(
        _store(), [], CouncilVerdict(signals=[]), None,
        AuditReport(), _settings(), gw,  # type: ignore[arg-type]
    )
    assert gw.chief_calls == 1
    assert verdict.unverified == []
