"""Stage 6 — 多空辯論（P3 雙盲對稱 + P6 敗方核心論點雙邊反駁）。

P3 雙盲（決議 3）：bull/bear 第一輪「盲打」開場——只見 council 意見與「對手是誰」，互不可見
對方論點；第二輪互餵對手開場各做一輪反駁；裁判看雙方完整兩輪。對稱化消除舊版「bear 看得到
bull、bull 看不到 bear」的先後手不公。

P6 雙邊（C-5）：裁判判敗的一方，其持敗方 stance 的「代表大師」核心論點都須被逐點反駁——
無論敗方是多數或少數（少數派勝出時，落敗的多數派同樣得被守住，不再只反駁『與共識不同調的
離群者』）。敗方人多時取信心最高的 ~3 位代表，避免要求反駁一整票。
"""

from __future__ import annotations

import asyncio

from cyber_sages.agents.schemas import (
    AnalystReport,
    CouncilVerdict,
    DebateArgument,
    DebateVerdict,
    OutlierRebuttal,
    RebuttalArgument,
)
from cyber_sages.config import Settings
from cyber_sages.data.evidence import EvidenceStore
from cyber_sages.llm.gateway import LLMGateway

# 敗方代表上限：敗方是大多數時只反駁信心最高的前幾位核心論點（雙邊對稱、成本可控）。
_MAX_REBUT_REPS = 3

DEBATER_SYSTEM = """\
You are the {side_name} advocate in an investment debate about {ticker}.
Build the strongest honest case for the {side_name} side using ONLY the provided
evidence and council opinions — cite evidence ids inline like [E012].
Attack the other side's weakest assumptions. Do not fabricate numbers.
Write in Traditional Chinese (繁體中文), terms/tickers in English."""

# 第二輪反駁專用 system（review A/B）：與「單欄 RebuttalArgument + 只反駁」的使用者 prompt
# 方向一致——不再要 LLM「建立己方論點」，避免它把輸出塞錯位置。
REBUTTER_SYSTEM = """\
You are the {side_name} advocate in ROUND 2 of an investment debate about {ticker}.
Your ONLY job now is to rebut the opponent's opening — do NOT restate your own case.
Attack their weakest assumptions using ONLY the provided evidence; cite ids like [E012].
Output a single concise rebuttal paragraph in Traditional Chinese; do not fabricate numbers."""

JUDGE_SYSTEM = """\
You are a coolly impartial investment judge. You watched a symmetric bull-vs-bear
debate (each side gave a blind opening, then a rebuttal after seeing the opponent).
Decide which side argued better ON THE EVIDENCE (not which side is more popular),
identify the single strongest point on each side, and list risks neither side
resolved. A draw is allowed. Write text fields in Traditional Chinese.

When you rule a side lost, you MUST NOT defeat its sages only in the aggregate.
The losing side's core dissenting sages are listed below by side. For EACH of them,
add one entry to outlier_rebuttals that names their CORE thesis (thesis_point) and
rebuts THAT specific point with evidence ids — not a generic counter. This holds
whether the losing side was the majority or the minority: a nominal defeat that
leaves a losing-side sage's central argument untouched is unacceptable."""


def _losing_side_reps(council: CouncilVerdict, winner: str) -> list[str]:
    """敗方持敗方 stance 的代表大師（信心最高 ~3 位）——其核心論點需被逐一反駁。

    P6：不再限定「與共識不同調的離群者」，而是敗方那一個方向的所有大師取代表——少數派勝出
    時，落敗的多數派也得被守住。draw 不強制（無敗方）。
    """
    if winner == "draw":
        return []
    losing_stance = "bearish" if winner == "bull" else "bullish"
    # tiebreak 用 sage 名（review C）：confidence 相同時取前 N 才確定性、不隨 signals 原序漂移。
    losers = sorted((s for s in council.signals if s.stance == losing_stance),
                    key=lambda s: (-s.confidence, s.sage))
    return [s.sage for s in losers[:_MAX_REBUT_REPS]]


def _outlier_theses_text(council: CouncilVerdict, names: list[str]) -> str:
    by_name = {s.sage: s for s in council.signals}
    lines = [f"- {n}（{by_name[n].stance}）: {by_name[n].thesis}"
             for n in names if n in by_name]
    return "\n".join(lines)


def _side_theses_text(council: CouncilVerdict, stance: str) -> str:
    """某一方向（bullish/bearish）大師的核心論點清單——給裁判判敗後逐點反駁用。"""
    lines = [f"- {s.sage}（conf {s.confidence:.1f}）: {s.thesis}"
             for s in council.signals if s.stance == stance]
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

    # 第一輪——雙盲盲打：兩方各只見 council + 「對手是誰」，互不可見對方論點，平行出場。
    async def _opening(side: str) -> DebateArgument:
        side_name, opp = ("BULL", "BEAR") if side == "bull" else ("BEAR", "BULL")
        arg = await gateway.structured(
            "debater",
            system=DEBATER_SYSTEM.format(side_name=side_name, ticker=store.ticker),
            prompt=(base_context + _minority_text(council, side)
                    + f"\n\n你的對手是 {opp} 方，但你現在看不到對方的論點（這是盲打第一輪）。"
                    + f"\n\nMake the {side_name} opening case."),
            schema=DebateArgument,
        )
        arg.side = side
        arg.rebuttal = ""
        return arg

    bull, bear = await asyncio.gather(_opening("bull"), _opening("bear"))

    # 第二輪——互餵對手開場，各做一輪反駁，平行（對稱：兩方都在見過對手後才反駁）。
    async def _rebut(mine: DebateArgument, opp: DebateArgument) -> str:
        side_name = "BULL" if mine.side == "bull" else "BEAR"
        out = await gateway.structured(
            "debater",
            system=REBUTTER_SYSTEM.format(side_name=side_name, ticker=store.ticker),
            prompt=(base_context
                    + f"\n\n# 你（{mine.side}）的開場\n{mine.argument}"
                    + f"\n\n# 對手（{opp.side}）的開場\n{opp.argument}"
                    + "\n\n針對對手開場最弱的假設做一輪反駁，引用 evidence id；只出反駁、不重述開場。"),
            schema=RebuttalArgument,
        )
        return out.rebuttal

    bull.rebuttal, bear.rebuttal = await asyncio.gather(
        _rebut(bull, bear), _rebut(bear, bull))

    # 裁判看雙方完整兩輪 + 兩個方向的大師核心論點（判敗後須逐點反駁敗方核心論點）。
    side_theses = (
        f"# Bull-side council sages (core theses)\n"
        f"{_side_theses_text(council, 'bullish') or '（無）'}\n\n"
        f"# Bear-side council sages (core theses)\n"
        f"{_side_theses_text(council, 'bearish') or '（無）'}"
    )
    judge_prompt = (
        f"Stock: {store.ticker}\n\n"
        f"# Bull — opening\n{bull.argument}\n\n# Bull — rebuttal\n{bull.rebuttal}\n\n"
        f"# Bear — opening\n{bear.argument}\n\n# Bear — rebuttal\n{bear.rebuttal}\n\n"
        f"# Evidence digest (for fact-checking both sides)\n{store.digest()}\n\n"
        f"{side_theses}\n\nDeliver your verdict."
    )
    verdict = await gateway.structured("judge", system=JUDGE_SYSTEM,
                                       prompt=judge_prompt, schema=DebateVerdict)

    # 強制論點級反駁：敗方代表若有人未被逐點反駁，補打一次
    needed = _losing_side_reps(council, verdict.winner)
    missing = [n for n in needed if n not in {r.sage for r in verdict.outlier_rebuttals}]
    if missing:
        retry = await gateway.structured(
            "judge", system=JUDGE_SYSTEM,
            prompt=(
                judge_prompt
                + f"\n\nYour verdict ruled '{verdict.winner}' won but did not rebut the "
                  f"core thesis of these losing-side sages: {', '.join(missing)}. "
                  "Keep your winner / rationale / strongest points UNCHANGED; only add one "
                  "concrete, evidence-cited outlier_rebuttals entry per missing sage."
            ),
            schema=DebateVerdict,
        )
        # winner/rationale 等以原判為準（避免補打改判）；只合併 rebuttal、揭露仍缺者
        verdict.outlier_rebuttals, verdict.unrebutted_outliers = _merge_rebuttals(
            verdict.outlier_rebuttals, retry.outlier_rebuttals, needed)
    return bull, bear, verdict
