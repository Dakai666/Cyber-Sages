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
    # 首版幻覺數字 99.9x → 重寫後改用可溯的 35.3x（=trailing_pe）→ 不留 unverified
    gw = _ChiefGateway(["預估本益比達 99.9x，明顯偏貴", "本益比 35.3x，估值偏高"])
    verdict, _ = await run_synthesis(
        _store(), [], CouncilVerdict(signals=[]), None,
        AuditReport(), _settings(), gw,  # type: ignore[arg-type]
    )
    assert gw.chief_calls == 2          # retry 觸發了一次
    assert verdict.unverified == []     # 修正後乾淨


async def test_chief_recheck_discloses_when_persistent():
    # 兩版都掛同一個無法溯源的數字 → 標 unverified（不 refuse、不報廢）
    gw = _ChiefGateway(["本益比 99.9x，貴", "本益比 99.9x，仍貴"])
    verdict, _ = await run_synthesis(
        _store(), [], CouncilVerdict(signals=[]), None,
        AuditReport(), _settings(), gw,  # type: ignore[arg-type]
    )
    assert gw.chief_calls == 2          # 用完 1 次 retry
    assert verdict.unverified            # fail-loud 揭露
    assert verdict.unverified[0].kind == "num_mismatch"


async def test_chief_qualitative_thesis_needs_no_retry():
    # 純質性論述（無有意義數字）→ 一次過、無 retry、無 unverified
    gw = _ChiefGateway(["基本面穩健、護城河深厚，但缺乏短線催化"])
    verdict, _ = await run_synthesis(
        _store(), [], CouncilVerdict(signals=[]), None,
        AuditReport(), _settings(), gw,  # type: ignore[arg-type]
    )
    assert gw.chief_calls == 1
    assert verdict.unverified == []
