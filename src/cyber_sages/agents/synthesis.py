"""Stage 7 — 首席合議 + 風控覆核。"""

from __future__ import annotations

from cyber_sages.agents.schemas import (
    AnalystReport,
    CouncilVerdict,
    DebateVerdict,
    FinalVerdict,
    RiskNote,
    UnverifiedClaim,
)
from cyber_sages.config import Settings
from cyber_sages.data.evidence import EvidenceStore
from cyber_sages.llm.gateway import LLMGateway
from cyber_sages.verify.citation_check import Claim, check_claims
from cyber_sages.verify.data_audit import AuditReport

# W7：chief brief 主體（thesis / 風險 / 翻盤條件）過 cite-check 的重試次數。
# 決議 5：retry 1 次（驗證錯誤回饋進 prompt，與 analyst 階段同機制）→ 仍失敗則標
# unverified 並在 brief 揭露，不 refuse（degraded-but-disclosed 一貫優於整條報廢）。
_CHIEF_RECHECK_RETRIES = 1

CHIEF_SYSTEM = """\
You are the Chief of Staff of an investment council, writing the decision brief
for the final decision-maker (a human or their AI agent). They will act on real
questions like: 「今天可以買嗎？多少可以買？多少該賣？」 Your brief must answer
those questions directly — vague neutrality is a failure mode.

Duties:
1. COMMIT to an action_plan with concrete price levels. Every level must be
   anchored to provided evidence (SMA / 52-week range / cross levels / valuation
   anchors like implied P/E at a given price) — state the anchor in `basis` and
   cite its evidence ids. You may place a level a stated offset from an anchor
   (e.g. "SMA200 下方 3% 留緩衝"), but never invent an unanchored number.
2. Express uncertainty through STRUCTURE, not hedging: entry zones, stop loss,
   invalidation conditions, and position_hint sized to conviction. "avoid" and
   "hold" are valid actions but still need the levels that would change them
   (e.g. avoid now, buy_dip zone at X–Y).
3. Give exactly three horizons: short (1-4週), mid (1-6月), long (6月+). They
   may disagree with each other — say so plainly.
4. Synthesize, don't average: if the minority argued better evidence, side with
   it. Be honest about dissent in dissent_summary.
5. conviction reflects evidence strength AND data quality; position_hint must
   translate it into sizing (e.g. 0.3 → 試單1/4倉 or 空手等待).
Never write filler like 僅供參考 / 本報告不構成建議 — write as if real money
moves on this brief. Write all text fields in Traditional Chinese (繁體中文),
tickers/terms in English."""

RISK_SYSTEM = """\
You are the Risk Officer. You did not participate in the analysis; you only
sanity-check the final verdict for: overconfidence vs the actual evidence,
data-quality issues being glossed over, concentration of the thesis on one
fragile assumption. Suggest a conviction_adjustment (-0.3..0) if warranted.
Write concerns in Traditional Chinese."""


async def run_synthesis(
    store: EvidenceStore,
    reports: list[AnalystReport],
    council: CouncilVerdict,
    debate: DebateVerdict | None,
    audit: AuditReport,
    settings: Settings,
    gateway: LLMGateway,
) -> tuple[FinalVerdict, RiskNote]:
    audit_text = (
        "\n".join(f"- [{f.severity}] {f.check}: {f.message}" for f in audit.findings)
        or "clean"
    )
    unverified = [
        f"- {r.analyst}: {u.as_line()}" for r in reports for u in r.unverified
    ]
    debate_text = (
        f"winner: {debate.winner}\nrationale: {debate.rationale}\n"
        f"strongest bull: {debate.strongest_bull_point}\n"
        f"strongest bear: {debate.strongest_bear_point}\n"
        f"unresolved: {'; '.join(debate.unresolved_risks)}"
        if debate else "(debate skipped)"
    )
    council_text = (
        f"{council.bullish}B/{council.neutral}N/{council.bearish}S, "
        f"weighted {council.weighted_score:+.2f}, consensus {council.consensus}, "
        f"outliers: {', '.join(council.outliers) or 'none'}"
    )
    analyst_text = "\n".join(f"- {r.analyst} ({r.outlook}): {r.summary}" for r in reports)

    prompt = (
        f"Stock: {store.ticker}\n\n"
        f"# Price & technical anchors (use these for your levels)\n"
        f"{store.digest(['quote', 'history'])}\n\n"
        f"# Fundamental anchors\n{store.digest(['fundamentals'])}\n\n"
        f"# Data audit findings\n{audit_text}\n\n"
        f"# Unverified claims (flagged, treat with suspicion)\n"
        f"{chr(10).join(unverified) or 'none'}\n\n"
        f"# Analyst summaries\n{analyst_text}\n\n"
        f"# Council vote\n{council_text}\n\n"
        f"# Sage theses\n"
        + "\n".join(f"- {s.sage} [{s.stance}]: {s.thesis}" for s in council.signals)
        + f"\n\n# Debate verdict\n{debate_text}\n\n"
        "Write the decision brief: action plan with anchored levels, three horizons, "
        "thesis, risks, invalidation."
    )

    # W7 — chief brief 主體過 cite-check：thesis / key_risks / what_would_change_my_mind
    # 內的數字必須能由 evidence 推導，否則回饋重寫一次；仍失敗則標 unverified 揭露。
    all_ids = [e.id for e in store.items]
    verdict = await gateway.structured("chief", system=CHIEF_SYSTEM, prompt=prompt,
                                       schema=FinalVerdict)
    for attempt in range(_CHIEF_RECHECK_RETRIES + 1):
        report = check_claims(_chief_claims(verdict, all_ids), store, settings.citation)
        if report.all_verified:
            break
        if attempt < _CHIEF_RECHECK_RETRIES:
            failures = "\n".join(f'- "{c.claim.text}" -> {c.reason}'
                                 for c in report.unverified)
            verdict = await gateway.structured(
                "chief", system=CHIEF_SYSTEM,
                prompt=(f"{prompt}\n\n# 你上一版 brief 有數字無法由 evidence 推導\n"
                        f"{failures}\n\n重寫 brief：修正或刪除 thesis / key_risks / "
                        "what_would_change_my_mind 內這些對不上 evidence 的數字。"),
                schema=FinalVerdict,
            )
    verdict.unverified = [
        # 引用全 store 檢查，故不回填冗長的 id 清單；要旨是「此數字無 evidence 可溯」。
        UnverifiedClaim(text=c.claim.text, evidence_ids=[],
                        reason="brief 內數字無法由 evidence 推導", kind=c.kind)
        for c in report.unverified
    ]
    _flag_unanchored_levels(verdict, store)

    risk = await gateway.structured(
        "risk",
        system=RISK_SYSTEM,
        prompt=(
            f"Stock: {store.ticker}\n\n# Data audit\n{audit_text}\n\n"
            f"# Final verdict under review\nstance: {verdict.stance}, "
            f"conviction: {verdict.conviction}\nthesis: {verdict.thesis}\n"
            f"risks listed: {'; '.join(verdict.key_risks)}\n\n"
            f"# Council vote\n{council_text}\n\nReview it."
        ),
        schema=RiskNote,
    )

    adj = max(-0.3, min(0.0, risk.conviction_adjustment))
    verdict.conviction = round(max(0.0, min(1.0, verdict.conviction + adj)), 2)
    if audit.degraded:
        verdict.conviction = round(min(verdict.conviction, 0.5), 2)  # 髒資料封頂
        verdict.key_risks.insert(0, "資料品質降級（audit gate 有 error），信心上限 0.5")
    return verdict, risk


def _chief_claims(verdict: FinalVerdict, all_ids: list[str]) -> list[Claim]:
    """把 chief brief 主體拆成可驗的 claim：thesis / 翻盤條件 / 每條風險。
    chief 不逐句標 evidence id（行內引用紀律屬 Spec D / Phase 5），故引用全 store——
    本階段只驗「數字可由某筆 evidence 推導」，攔截幻覺數字。"""
    texts = [verdict.thesis, verdict.what_would_change_my_mind, *verdict.key_risks]
    return [Claim(text=t, evidence_ids=all_ids) for t in texts if t and t.strip()]


def _flag_unanchored_levels(verdict: FinalVerdict, store: EvidenceStore) -> None:
    """價位錨點稽核：引用不存在 evidence id 或完全沒引用的價位，basis 加註警示。
    （價位本身是判斷值，允許偏離錨點；但錨點必須真實存在。）"""
    plan = verdict.action_plan
    levels = [*plan.entry_zone, *plan.targets]
    if plan.stop_loss:
        levels.append(plan.stop_loss)
    for level in levels:
        valid = [eid for eid in level.evidence_ids if store.get(eid)]
        if not valid:
            level.basis += "（⚠ 無有效 evidence 錨點）"
        level.evidence_ids = valid
