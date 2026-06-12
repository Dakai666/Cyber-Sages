"""Stage 6 — 多空辯論。投票結果當輸入；離群少數派的意見強制餵給弱勢方。"""

from __future__ import annotations

from cyber_sages.agents.schemas import (
    AnalystReport,
    CouncilVerdict,
    DebateArgument,
    DebateVerdict,
    OutlierRebuttal,
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
resolved. A draw is allowed. Write text fields in Traditional Chinese.

When you rule a side lost, you MUST NOT defeat its dissenting sages only in the
aggregate. For EACH listed outlier sage on the losing side, add one entry to
outlier_rebuttals that names their CORE thesis (thesis_point) and rebuts THAT
specific point with evidence ids — not a generic counter. A nominal defeat that
leaves the outlier's central argument untouched is unacceptable."""


def _outliers_needing_rebuttal(council: CouncilVerdict, winner: str) -> list[str]:
    """敗方的離群大師——其核心論點需被逐一反駁。draw 時不強制。"""
    if winner == "draw":
        return []
    losing_stance = "bearish" if winner == "bull" else "bullish"
    stance_by_sage = {s.sage: s.stance for s in council.signals}
    return [name for name in council.outliers
            if stance_by_sage.get(name) == losing_stance]


def _outlier_theses_text(council: CouncilVerdict, names: list[str]) -> str:
    by_name = {s.sage: s for s in council.signals}
    lines = [f"- {n}（{by_name[n].stance}）: {by_name[n].thesis}"
             for n in names if n in by_name]
    return "\n".join(lines)


def _merge_rebuttals(
    original: list[OutlierRebuttal], retry: list[OutlierRebuttal], needed: list[str]
) -> tuple[list[OutlierRebuttal], list[str]]:
    """合併首打與補打的反駁（依 sage 去重，首打優先），回傳 (合併結果, 仍未反駁名單)。

    對 needed 集合判定缺漏（而非對原 covered 集合），避免補打只補了一部分卻被當成完成——
    仍缺者塞進 unrebutted 讓 brief 顯式警告（fail-loud > 假裝做了）。
    """
    by_sage = {r.sage: r for r in original}
    for r in retry:
        by_sage.setdefault(r.sage, r)
    merged = list(by_sage.values())
    unrebutted = sorted(set(needed) - set(by_sage))
    return merged, unrebutted


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

    outliers_text = (
        f"\n\n# Outlier sages (dissented from consensus) — their core theses:\n"
        f"{_outlier_theses_text(council, council.outliers)}"
        if council.outliers else ""
    )
    judge_prompt = (
        f"Stock: {store.ticker}\n\n# Bull argument\n{bull.argument}\n\n"
        f"# Bear argument\n{bear.argument}\n\n"
        f"# Evidence digest (for fact-checking both sides)\n{store.digest()}"
        f"{outliers_text}\n\nDeliver your verdict."
    )
    verdict = await gateway.structured("judge", system=JUDGE_SYSTEM,
                                       prompt=judge_prompt, schema=DebateVerdict)

    # 強制論點級反駁：敗方的離群者若有人未被逐點反駁，補打一次
    needed = _outliers_needing_rebuttal(council, verdict.winner)
    missing = [n for n in needed if n not in {r.sage for r in verdict.outlier_rebuttals}]
    if missing:
        retry = await gateway.structured(
            "judge", system=JUDGE_SYSTEM,
            prompt=(
                judge_prompt
                + f"\n\nYour verdict ruled '{verdict.winner}' won but did not rebut the "
                  f"core thesis of these losing-side outlier sages: {', '.join(missing)}. "
                  "Keep your winner / rationale / strongest points UNCHANGED; only add one "
                  "concrete, evidence-cited outlier_rebuttals entry per missing sage."
            ),
            schema=DebateVerdict,
        )
        # winner/rationale 等以原判為準（避免補打改判）；只合併 rebuttal、揭露仍缺者
        verdict.outlier_rebuttals, verdict.unrebutted_outliers = _merge_rebuttals(
            verdict.outlier_rebuttals, retry.outlier_rebuttals, needed)
    return bull, bear, verdict
