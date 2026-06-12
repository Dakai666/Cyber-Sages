"""Stage 5 — Sage Council：大師 persona 平行出訊號 + 加權投票。

統計性抗幻覺：單一 agent 的幻覺/偏誤在足夠多的獨立視角投票下被稀釋；
離群者意見不被丟棄，原文送進辯論階段。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import yaml
from pydantic import BaseModel

from cyber_sages.agents.schemas import AnalystReport, CouncilVerdict, SageSignal, Stance
from cyber_sages.config import Settings
from cyber_sages.data.evidence import EvidenceStore
from cyber_sages.llm.gateway import LLMGateway

PERSONA_DIR = Path(__file__).resolve().parent.parent / "personas"


class Persona(BaseModel):
    key: str
    name: str
    weight: float = 1.0
    philosophy: str
    focus: str
    voice: str


def load_personas(limit: int | None = None) -> list[Persona]:
    personas = []
    for path in sorted(PERSONA_DIR.glob("*.yaml")):
        with open(path, encoding="utf-8") as f:
            personas.append(Persona.model_validate(yaml.safe_load(f)))
    # weight 高者優先出席（--sages N 截斷時）
    personas.sort(key=lambda p: -p.weight)
    return personas[:limit] if limit else personas


SAGE_SYSTEM = """\
You are {name}, the legendary investor, serving on an investment council.

Your philosophy: {philosophy}
You focus on: {focus}
Your voice: {voice}

You receive VERIFIED analyst reports and the underlying evidence for one stock.
Judge it strictly through your own philosophy — do not be a generic analyst.
Rules:
- Ground your thesis in the provided evidence; reference evidence ids in
  key_evidence_ids. No outside numbers.
- It is perfectly acceptable (and in character) to disagree with the analysts.
- confidence: how strongly your philosophy speaks on THIS stock (0.2 = barely
  in your circle of competence, 0.9 = textbook case for you).
- Write `thesis` and `what_would_change_my_mind` in Traditional Chinese (繁體中文),
  in your distinctive voice; keep tickers/terms in English."""


def _reports_text(reports: list[AnalystReport]) -> str:
    parts = []
    for r in reports:
        claims = "\n".join(
            f"  - {c.text} [{', '.join(c.evidence_ids)}]" for c in r.claims
        )
        flags = (
            "\n  ⚠ UNVERIFIED claims (excluded from verification): "
            + "; ".join(u.as_line() for u in r.unverified)
            if r.unverified else ""
        )
        parts.append(f"## {r.analyst} (outlook: {r.outlook})\n{r.summary}\n{claims}{flags}")
    return "\n\n".join(parts)


async def run_council(
    store: EvidenceStore,
    reports: list[AnalystReport],
    settings: Settings,
    gateway: LLMGateway,
    n_sages: int | None = None,
    on_signal=None,  # callback(SageSignal) — CLI 即時更新投票表
) -> CouncilVerdict:
    personas = load_personas(n_sages or settings.defaults.sages)
    shared_prompt = (
        f"Stock: {store.ticker}\n\n# Verified analyst reports\n{_reports_text(reports)}\n\n"
        f"# Evidence digest\n{store.digest()}\n\nDeliver your signal."
    )

    async def one(p: Persona) -> SageSignal:
        signal = await gateway.structured(
            "sage",
            system=SAGE_SYSTEM.format(name=p.name, philosophy=p.philosophy,
                                      focus=p.focus, voice=p.voice),
            prompt=shared_prompt,
            schema=SageSignal,
            cache_system=False,  # persona 各異；共享前綴在 prompt 端（anthropic 自動前綴比對）
        )
        signal.sage = p.name
        signal.confidence = max(0.0, min(1.0, signal.confidence))
        if on_signal:
            result = on_signal(signal)
            if asyncio.iscoroutine(result):
                await result
        return signal

    # 大師推理是陪審團的本體，不輕易丟棄：gateway 已會在 thinking 截斷 JSON 時自動加大
    # 預算重試，讓大師把話講完。這裡只是最後防線——加大重試後仍硬失敗者才記為缺席、不拖垮
    # 全場；且唯有過半失敗才視為合議失效而報錯。正常一場 absent 應為空。
    results = await asyncio.gather(*[one(p) for p in personas], return_exceptions=True)
    signals: list[SageSignal] = []
    absent: list[str] = []
    for p, r in zip(personas, results):
        if isinstance(r, BaseException):
            absent.append(p.name)
        else:
            signals.append(r)
    if len(signals) * 2 < len(personas):
        raise RuntimeError(
            f"Council failed: only {len(signals)}/{len(personas)} sages returned a "
            f"valid signal (absent: {', '.join(absent)})"
        )
    return tally(signals, personas, absent=absent)


def tally(
    signals: list[SageSignal],
    personas: list[Persona],
    absent: list[str] | None = None,
) -> CouncilVerdict:
    absent = absent or []
    weights = {p.name: p.weight for p in personas}
    counts = {"bullish": 0, "bearish": 0, "neutral": 0}
    score_num = score_den = 0.0
    for s in signals:
        counts[s.stance] += 1
        w = weights.get(s.sage, 1.0) * s.confidence
        direction = {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0}[s.stance]
        score_num += w * direction
        score_den += w
    weighted = score_num / score_den if score_den else 0.0

    consensus: Stance = "neutral"
    if counts["bullish"] > counts["bearish"] and counts["bullish"] >= counts["neutral"]:
        consensus = "bullish"
    elif counts["bearish"] > counts["bullish"] and counts["bearish"] >= counts["neutral"]:
        consensus = "bearish"

    outliers = [s.sage for s in signals if s.stance != consensus and s.stance != "neutral"]
    return CouncilVerdict(
        signals=signals, bullish=counts["bullish"], bearish=counts["bearish"],
        neutral=counts["neutral"], weighted_score=round(weighted, 3),
        consensus=consensus, outliers=outliers, absent=absent,
    )
