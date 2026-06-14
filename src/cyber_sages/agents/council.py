"""Stage 5 — Sage Council：大師 persona 平行出訊號 + 加權投票。

統計性抗幻覺：單一 agent 的幻覺/偏誤在足夠多的獨立視角投票下被稀釋；
離群者意見不被丟棄，原文送進辯論階段。

兩條執行路徑（Spec E）：
- **Persona Pack**（目錄格式）：Sage Runtime 三段執行 skill→rule→SOP(LLM)→clamp，
  讓大師「針對性地處理前面階段的資料」——流程本身就是視角。
- **degraded**（舊單檔 yaml）：原本的單發 prompt，向後相容，漸進遷移。

`load_personas` / `Persona` 由 `personas.pack` 提供，這裡 re-export 維持既有 import 路徑。
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel

from cyber_sages.agents.schemas import (
    AnalystReport,
    CouncilVerdict,
    SageSignal,
    SopStepResult,
    Stance,
    UnverifiedClaim,
)
from cyber_sages.config import Settings
from cyber_sages.data.evidence import EvidenceStore
from cyber_sages.llm.gateway import LLMGateway
from cyber_sages.personas.pack import Persona, load_personas
from cyber_sages.personas.rules import clamp_confidence, evaluate_rules, rule_values
from cyber_sages.personas.skill import run_skills
from cyber_sages.verify.citation_check import Claim, check_claims

__all__ = ["Persona", "load_personas", "run_council", "tally"]

# sop_trace 過 cite-check 的重試次數（E1 實作決議 #3：軟揭露，比照 PR #27 chief brief）。
_SOP_RECHECK_RETRIES = 1


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

# Pack 專屬：在身分之後加上 SOP 紀律——逐步作答、每步引用 evidence id 或 skill 輸出。
SAGE_SOP_DISCIPLINE = """\

You follow YOUR OWN decision SOP (below). Work through it step by step. For EACH step,
record a SopStepResult in `sop_trace`: the step id, your conclusion in your voice (繁體中文),
and the evidence ids that anchor it (shared E### ids, or your private S-### skill outputs).
Every number you state in a step must be traceable to a cited evidence id. Your final
stance/confidence/thesis must follow from this trace, through your philosophy."""


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


def _persona_system(p: Persona) -> str:
    base = (SAGE_PERSONA
            .replace("{name}", p.name).replace("{philosophy}", p.philosophy)
            .replace("{focus}", p.focus).replace("{voice}", p.voice))
    return base + SAGE_SOP_DISCIPLINE if p.is_pack else base


def _sop_prompt(
    p: Persona, private_digest: str, triggered: list[str], not_evaluable: list[str]
) -> str:
    """Pack SOP pass 的 user prompt：skill 輸出 + 觸發規則 + 看不到的東西 + SOP 步驟 + exceptions。"""
    steps = "\n".join(
        f"{i + 1}. [{s.step}] {s.ask}"
        + (f" — 用 skill `{s.use_skill}`" if s.use_skill else "")
        + (f"（看 {', '.join(s.look_at)}）" if s.look_at else "")
        + (f"\n   on_fail: {s.on_fail}" if s.on_fail else "")
        for i, s in enumerate(p.pack.sop)
    )
    exceptions = "\n".join(f"- {e}" for e in p.pack.exceptions)
    return (
        "# 你的 skill 算出的 private evidence（確定性、可引用）\n"
        f"{private_digest or '（無——本次缺輸入欄位，見下方 not_evaluable）'}\n\n"
        "# 你的硬規則觸發結果（已由程式判定）\n"
        f"{chr(10).join(triggered) or '（無規則觸發）'}\n\n"
        "# 本次無法評估的規則/技能（你平常會看、這次看不到的東西——納入判斷並誠實揭露）\n"
        f"{chr(10).join('- ' + n for n in not_evaluable) or '（無）'}\n\n"
        "# 你的 exceptions（自然語言裁量，動用時務必引用 evidence 說明）\n"
        f"{exceptions or '（無）'}\n\n"
        "# 你的決策 SOP（逐步走，每步寫進 sop_trace）\n"
        f"{steps}\n\n"
        "依你的 SOP 逐步作答，最後按你的哲學下結論。"
    )


def _sop_claims(trace: list[SopStepResult]) -> list[Claim]:
    return [
        Claim(text=s.conclusion, evidence_ids=s.evidence_ids)
        for s in trace if s.conclusion.strip()
    ]


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

    async def _degraded(p: Persona) -> SageSignal:
        """舊單檔 persona：原本的單發 prompt（無 skill/rule/SOP）。"""
        signal = await gateway.structured(
            "sage", system=_persona_system(p), prompt="Deliver your signal now.",
            schema=SageSignal, cache_prefix=shared_system,
        )
        # degraded 無 clamp 階段，LLM 的 confidence 在此收進 [0,1]（pack 路徑已由
        # clamp_confidence 收口，故不在 one() 重複夾一次——B7）。
        signal.confidence = max(0.0, min(1.0, signal.confidence))
        return signal

    async def _pack(p: Persona) -> SageSignal:
        """Persona Pack：Sage Runtime 三段執行 skill→rule→SOP(LLM)→clamp。"""
        # 1. skill pass（程式）→ sage-private derived evidence
        private, skill_ne = run_skills(p.pack.skills, store, p.key)
        sage_store = EvidenceStore(
            ticker=store.ticker, market=store.market, instrument=store.instrument,
            items=[*store.items, *private],
        )
        private_digest = "\n".join(e.digest_line() for e in private)
        # 2. rule pass（程式）→ triggered / not_evaluable（規則可引用 private derived 欄位）
        outcomes = evaluate_rules(p.pack.hard_rules, rule_values(sage_store))
        triggered = [
            f"- [{o.rule_id}] {o.action}"
            + (f" ceiling={o.confidence_ceiling}" if o.confidence_ceiling is not None else "")
            + (f" floor={o.confidence_floor}" if o.confidence_floor is not None else "")
            + (f"（{o.note}）" if o.note else "")
            for o in outcomes if o.triggered
        ]
        not_evaluable = skill_ne + [
            f"rule:{o.rule_id}" + (f"（{o.note}）" if o.note else "")
            for o in outcomes if o.not_evaluable
        ]
        # 3. SOP pass（LLM）
        system = _persona_system(p)
        prompt = _sop_prompt(p, private_digest, triggered, not_evaluable)
        signal = await gateway.structured(
            "sage", system=system, prompt=prompt, schema=SageSignal, cache_prefix=shared_system,
        )
        # sop_trace 過 cite-check（軟揭露：retry 後仍對不上 → 標 unverified，不 refuse）
        for attempt in range(_SOP_RECHECK_RETRIES + 1):
            report = check_claims(_sop_claims(signal.sop_trace), sage_store, settings.citation)
            if report.all_verified:
                break
            if attempt < _SOP_RECHECK_RETRIES:
                failures = "\n".join(f'- "{c.claim.text}" -> {c.reason}'
                                     for c in report.unverified)
                signal = await gateway.structured(
                    "sage", system=system,
                    prompt=(f"{prompt}\n\n# 你上一版 sop_trace 有數字無法由所引 evidence 推導\n"
                            f"{failures}\n\n重走 SOP：修正或刪除這些對不上 evidence 的數字，"
                            "或補上正確的 evidence id。"),
                    schema=SageSignal, cache_prefix=shared_system,
                )
        signal.unverified = [
            UnverifiedClaim(text=c.claim.text, evidence_ids=c.claim.evidence_ids,
                            reason="sop_trace 數字無法由所引 evidence 推導", kind=c.kind)
            for c in report.unverified
        ]
        # 4. clamp（程式）：confidence 受觸發規則的 floor/ceiling 約束；directional 衝突揭露
        signal.confidence, signal.rule_conflicts = clamp_confidence(
            signal.stance, signal.confidence, outcomes
        )
        signal.not_evaluable = not_evaluable
        return signal

    async def one(p: Persona) -> SageSignal:
        signal = await (_pack(p) if p.is_pack else _degraded(p))
        signal.sage = p.name
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
