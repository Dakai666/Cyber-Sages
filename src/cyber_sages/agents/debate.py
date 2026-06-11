"""Stage 6 — 多空辯論。投票結果當輸入；離群少數派的意見強制餵給弱勢方。"""

from __future__ import annotations

from cyber_sages.agents.schemas import (
    AnalystReport,
    CouncilVerdict,
    DebateArgument,
    DebateVerdict,
)
from cyber_sages.config import Settings
from cyber_sages.data.evidence import EvidenceStore
from cyber_sages.llm.gateway import LLMGateway

DEBATER_SYSTEM = """\
You are the {side_name} advocate in an investment debate about {ticker}.
Build the strongest honest case for the {side_name} side using ONLY the provided
evidence and council opinions — cite evidence ids inline like [E012].
Attack the other side's weakest assumptions. Do not fabricate numbers.
Write in Traditional Chinese (繁體中文), terms/tickers in English."""

JUDGE_SYSTEM = """\
You are a coolly impartial investment judge. You watched a bull-vs-bear debate.
Decide which side argued better ON THE EVIDENCE (not which side is more popular),
identify the single strongest point on each side, and list risks neither side
resolved. A draw is allowed. Write text fields in Traditional Chinese."""


def _council_text(council: CouncilVerdict) -> str:
    lines = [
        f"Vote: {council.bullish} bullish / {council.neutral} neutral / "
        f"{council.bearish} bearish, weighted score {council.weighted_score:+.2f}, "
        f"consensus {council.consensus}",
    ]
    for s in council.signals:
        lines.append(f"- {s.sage} [{s.stance}, conf {s.confidence:.1f}]: {s.thesis}")
    return "\n".join(lines)


def _minority_text(council: CouncilVerdict, side: str) -> str:
    """把與多數不同調的 sage 原文塞給弱勢方，避免多數壓掉正確少數。"""
    wanted = "bullish" if side == "bull" else "bearish"
    if council.consensus == wanted:
        return ""
    minority = [s for s in council.signals if s.stance == wanted]
    if not minority:
        return ""
    quotes = "\n".join(f"- {s.sage}: {s.thesis}" for s in minority)
    return (
        "\n\nYou are the MINORITY side. These council members agreed with you — "
        f"build on their strongest points:\n{quotes}"
    )


async def run_debate(
    store: EvidenceStore,
    reports: list[AnalystReport],
    council: CouncilVerdict,
    settings: Settings,
    gateway: LLMGateway,
) -> tuple[DebateArgument, DebateArgument, DebateVerdict]:
    base_context = (
        f"# Council opinions\n{_council_text(council)}\n\n"
        f"# Evidence digest\n{store.digest()}"
    )

    bull = await gateway.structured(
        "debater",
        system=DEBATER_SYSTEM.format(side_name="BULL", ticker=store.ticker),
        prompt=base_context + _minority_text(council, "bull") + "\n\nMake the bull case.",
        schema=DebateArgument,
    )
    bull.side = "bull"

    bear = await gateway.structured(
        "debater",
        system=DEBATER_SYSTEM.format(side_name="BEAR", ticker=store.ticker),
        prompt=(
            base_context + _minority_text(council, "bear")
            + f"\n\n# The bull just argued:\n{bull.argument}\n\n"
              "Make the bear case and rebut the bull's weakest points."
        ),
        schema=DebateArgument,
    )
    bear.side = "bear"

    verdict = await gateway.structured(
        "judge",
        system=JUDGE_SYSTEM,
        prompt=(
            f"Stock: {store.ticker}\n\n# Bull argument\n{bull.argument}\n\n"
            f"# Bear argument\n{bear.argument}\n\n"
            f"# Evidence digest (for fact-checking both sides)\n{store.digest()}\n\n"
            "Deliver your verdict."
        ),
        schema=DebateVerdict,
    )
    return bull, bear, verdict
