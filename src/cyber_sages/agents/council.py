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


# 合議庭通用框架 + 證據摘要——所有 sage 共用，故當作 prompt-cache 的「共享前綴」。
# persona 各異的身分接在其後（見 SAGE_PERSONA）。prompt cache 是前綴比對（順序
# tools→system→messages），要讓 N 位大師共用快取，共享內容必須在前、persona 在後；
# 把各異的 persona 放前面（舊作法）會讓後面的共享段每位都 cache-miss。
SAGE_SHARED_SYSTEM = """\
You are a legendary investor serving on an investment council, judging one stock.
You receive VERIFIED analyst reports and the underlying evidence below (mostly first-hand;
items whose source is tagged "(estimate)" are forward-looking analyst consensus — second-hand).

Rules (apply through whatever investment philosophy you are assigned next):
- Ground your thesis in the provided evidence; reference evidence ids in
  key_evidence_ids. No outside numbers.
- It is perfectly acceptable (and in character) to disagree with the analysts.
- confidence: how strongly your philosophy speaks on THIS stock (0.2 = barely
  in your circle of competence, 0.9 = textbook case for you).
- Write `thesis` and `what_would_change_my_mind` in Traditional Chinese (繁體中文),
  in your distinctive voice; keep tickers/terms in English.

# Stock: {ticker}

# Verified analyst reports
{reports}

# Evidence digest
{digest}"""

# persona 身分——各大師相異，接在共享前綴之後（快取 breakpoint 之外，每位重算）。
SAGE_PERSONA = """\
You are {name}. Judge the stock above strictly through your own philosophy —
do not be a generic analyst.

Your philosophy: {philosophy}
You focus on: {focus}
Your voice: {voice}"""


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
    # 共享前綴：證據摘要 + 分析師報告，所有大師一字不差地共用 → 走 cache_prefix 命中快取。
    # 用 .replace 而非 .format：analyst 報告 / digest 是 LLM 生成 + 數值文本，可能含字面
    # `{` `}`（如「{x}」placeholder 或一般 curly braces），.format 會 raise KeyError/
    # IndexError——與 verify/data_audit.py 的 .replace("{today}") 同一個雷、同一個解法。
    shared_system = (
        SAGE_SHARED_SYSTEM
        .replace("{ticker}", store.ticker)
        .replace("{reports}", _reports_text(reports))
        .replace("{digest}", store.digest())
    )
    # provider 是否支援 prompt cache：決定要不要「先暖一位再 fan-out」。MiniMax 不快取，
    # 全平行即可；Anthropic 會快取，同時併發全部會在快取寫入前每位都 miss（快取在首個
    # 回應開始後才可讀），故先跑一位把共享前綴寫進快取，其餘再平行讀取。
    sage_caches = settings.providers[settings.roles["sage"].provider].has("cache_control")

    async def one(p: Persona) -> SageSignal:
        signal = await gateway.structured(
            "sage",
            system=(SAGE_PERSONA
                    .replace("{name}", p.name).replace("{philosophy}", p.philosophy)
                    .replace("{focus}", p.focus).replace("{voice}", p.voice)),
            prompt="Deliver your signal now.",
            schema=SageSignal,
            cache_prefix=shared_system,
        )
        signal.sage = p.name
        signal.confidence = max(0.0, min(1.0, signal.confidence))
        if on_signal:
            result = on_signal(signal)
            if asyncio.iscoroutine(result):
                await result
        return signal

    # 大師推理是陪審團的本體，不輕易丟棄：gateway 已會在 thinking 截斷 JSON 時自動加大預算
    # 重試讓大師把話講完。這裡是最後防線——仍硬失敗者經 return_exceptions 轉為結果值（記為
    # 缺席、不拖垮全場），唯有過半失敗才視為合議失效而報錯。正常一場 absent 應為空。
    if sage_caches and len(personas) > 1:
        # provider 支援快取時先暖一位（把共享前綴寫進快取）再 fan-out 其餘讀取——快取在首個
        # 回應開始後才可讀，同時併發全部會每位 miss。⚠ 首位若失敗則前綴沒寫入、其餘 N-1 仍
        # cache-miss（不影響正確性，只是該場退回近 N 倍成本）。MiniMax 無快取則照舊全平行。
        first = await asyncio.gather(one(personas[0]), return_exceptions=True)
        rest = await asyncio.gather(*(one(p) for p in personas[1:]), return_exceptions=True)
        results = [*first, *rest]
    else:
        results = await asyncio.gather(*(one(p) for p in personas), return_exceptions=True)
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
