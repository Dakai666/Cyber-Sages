"""Stage 7 — 首席合議 + 風控覆核。"""

from __future__ import annotations

from cyber_sages.agents.schemas import (
    AnalystReport,
    CouncilVerdict,
    DebateVerdict,
    FinalVerdict,
    RiskNote,
)
from cyber_sages.config import Settings
from cyber_sages.data.evidence import EvidenceStore
from cyber_sages.llm.gateway import LLMGateway
from cyber_sages.verify.data_audit import AuditReport

CHIEF_SYSTEM = """\
You are the Chief Sage — the final synthesizer of an investment council.
You weigh: verified analyst reports, the council vote, the debate outcome, and
data-quality caveats. Your duties:
- Synthesize, don't average: if the minority argued better evidence, side with it.
- Be honest about dissent in dissent_summary — never pretend consensus.
- conviction reflects evidence strength AND data quality (degraded data caps it).
- This is research, not financial advice; still, commit to a clear stance.
Write all text fields in Traditional Chinese (繁體中文), tickers/terms in English."""

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
        f"- {r.analyst}: {u}" for r in reports for u in r.unverified_claims
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
        f"# Data audit findings\n{audit_text}\n\n"
        f"# Unverified claims (flagged, treat with suspicion)\n"
        f"{chr(10).join(unverified) or 'none'}\n\n"
        f"# Analyst summaries\n{analyst_text}\n\n"
        f"# Council vote\n{council_text}\n\n"
        f"# Sage theses\n"
        + "\n".join(f"- {s.sage} [{s.stance}]: {s.thesis}" for s in council.signals)
        + f"\n\n# Debate verdict\n{debate_text}\n\nDeliver the final verdict."
    )

    verdict = await gateway.structured("chief", system=CHIEF_SYSTEM, prompt=prompt,
                                       schema=FinalVerdict)

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
