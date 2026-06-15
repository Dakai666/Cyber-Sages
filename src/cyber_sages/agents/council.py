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
    ScoutSignal,
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

# P2 兩階段取樣（C-3）：deep 階段只深入「共識代表 + 離群代表」各上限數位（決議：各 ~3）。
# 房間 ≤ deep 預算時跳過 scout、全員深入（小房間兩階段反而多花一輪 scout 成本）。
_CONSENSUS_REPS = 3
_OUTLIER_REPS = 3
_DEEP_BUDGET = _CONSENSUS_REPS + _OUTLIER_REPS  # 預設；可由 settings.defaults.deep_budget 覆寫（review E）

# review D：scout 信心是 LLM 直出、未經 deep 的 rule clamp 校準。對 scout-only 票套不確定性
# 折扣，避免「未校準的 scout 0.9」在加權投票中壓過「被規則 clamp 到 0.5 的 deep 代表」。
_SCOUT_CONFIDENCE_DISCOUNT = 0.85


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
- If your stance is `neutral`, you MUST set `neutral_reason`. Decide in THIS order:
  1. if the evidence is missing key fields you'd need to judge (thin/incomplete)
     → `insufficient_signal`;
  2. else if the evidence is there but this stock sits outside your competence or
     your method doesn't apply → `out_of_circle`;
  3. else (evidence is there, in your circle, but genuine bull and bear forces
     cancel out) → `balanced_forces`.
  Pick the first that matches — do not overclaim a standoff when you simply lack data.
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


# P2 scout（第一輪）共享前綴——刻意精簡（只給 evidence digest、不含分析師報告、不含 SOP
# 紀律），走 sage_scout 角色（可指向便宜模型）。目的：用低成本對全員取得粗 stance + 信心，
# 再只對代表深入，避免 N 位同 model 大師全程昂貴推理（P2：同 model 統計稀釋不成立）。
SCOUT_SHARED_SYSTEM = """\
You are a legendary investor giving a QUICK triage read on one stock for an
investment council. Deliver a rough stance + confidence in ONE pass — no deep
workup, no step-by-step reasoning. Ground it in the evidence; do not fabricate
numbers. If your stance is neutral, set neutral_reason honestly.

# Stock: {ticker}

# Evidence digest
{digest}"""


# 注入 shared system 末端，讓被席大師按本次 horizon 校準（trading 看數天~數週、value 看數年）。
_HORIZON_NOTE = {
    "value": "\n\n# Horizon: VALUE（數年，3~10y）——以多年基本面、護城河、估值、owner "
             "earnings 為主軸判斷；短線價格雜訊不是重點。",
    "trading": "\n\n# Horizon: TRADING（數天~數週）——以價格趨勢、動能、籌碼/流向、波動為"
               "主軸判斷；長期內在價值不是本次重點，重在近期不對稱與進出場時機。",
}


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
        parts.append(f"## {r.analyst}\n{r.summary}\n{claims}{flags}")
    return "\n\n".join(parts)


def _persona_identity(p: Persona) -> str:
    """大師身分段（philosophy/focus/voice）——deep 與 scout 共用；scout 不加 SOP 紀律。"""
    return (SAGE_PERSONA
            .replace("{name}", p.name).replace("{philosophy}", p.philosophy)
            .replace("{focus}", p.focus).replace("{voice}", p.voice))


def _persona_system(p: Persona) -> str:
    base = _persona_identity(p)
    return base + SAGE_SOP_DISCIPLINE if p.is_pack else base


def _consensus_stance(counts: dict[str, int]) -> Stance:
    """多數方向（與 tally 同規則）：directional 多於對方且不少於 neutral 才成立，否則 neutral。"""
    if counts["bullish"] > counts["bearish"] and counts["bullish"] >= counts["neutral"]:
        return "bullish"
    if counts["bearish"] > counts["bullish"] and counts["bearish"] >= counts["neutral"]:
        return "bearish"
    return "neutral"


def _select_reps(scouted: list[tuple[Persona, SageSignal]]) -> list[Persona]:
    """P2：依 scout 結果挑深入代表——共識代表 + 離群代表各取 weight×confidence 最高 ~3 位。

    保留反單一視角：離群者（與 scout 共識不同調）獨立配額，確保異議獲得深度推理，不被多數淹沒。
    回傳的代表順序：共識代表在前、離群代表在後（皆按分數降序）。
    """
    counts = {"bullish": 0, "bearish": 0, "neutral": 0}
    for _, s in scouted:
        counts[s.stance] += 1
    consensus = _consensus_stance(counts)

    def score(item: tuple[Persona, SageSignal]) -> float:
        p, s = item
        return p.weight * s.confidence

    agree = sorted((it for it in scouted if it[1].stance == consensus), key=score, reverse=True)
    dissent = sorted((it for it in scouted if it[1].stance != consensus), key=score, reverse=True)
    reps = agree[:_CONSENSUS_REPS] + dissent[:_OUTLIER_REPS]
    return [p for p, _ in reps]


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
    horizon: str = "value",
) -> CouncilVerdict:
    # P9：按 horizon 分席——只座位適用本 horizon 的大師；越圍者 abstain（不出席、不投票，
    # brief 揭露），不浪費 LLM 呼叫去問 Buffett 當沖。abstain ≠ absent（後者是硬失敗）。
    everyone = load_personas()  # 全員、已按 weight 降序
    applicable = [p for p in everyone if horizon in p.horizons]
    abstained = [p.name for p in everyone if horizon not in p.horizons]
    personas = applicable[: (n_sages or settings.defaults.sages)]
    # fail-loud（review C）：該 horizon 無任何適用大師時不可靜默產出「0 席、中性共識」的詭異
    # brief——quorum 條件 0<0 為 False 不會擋。PR3 改 trading roster 時這是高風險路徑。
    if not personas:
        raise RuntimeError(
            f"Council failed: horizon={horizon} 沒有任何適用大師——檢查 persona horizons 標記"
        )
    # 共享前綴：證據摘要 + 分析師報告，所有大師一字不差地共用 → 走 cache_prefix 命中快取。
    # 用 .replace 而非 .format：analyst 報告 / digest 是 LLM 生成 + 數值文本，可能含字面
    # `{` `}`（如「{x}」placeholder 或一般 curly braces），.format 會 raise KeyError/
    # IndexError——與 verify/data_audit.py 的 .replace("{today}") 同一個雷、同一個解法。
    shared_system = (
        SAGE_SHARED_SYSTEM
        .replace("{ticker}", store.ticker)
        .replace("{reports}", _reports_text(reports))
        .replace("{digest}", store.digest())
        + _HORIZON_NOTE[horizon]
    )
    # provider 是否支援 prompt cache：決定要不要「先暖一位再 fan-out」。MiniMax 不快取，
    # 全平行即可；Anthropic 會快取，同時併發全部會在快取寫入前每位都 miss（快取在首個
    # 回應開始後才可讀），故先跑一位把共享前綴寫進快取，其餘再平行讀取。
    sage_caches = settings.providers[settings.roles["sage"].provider].has("cache_control")
    # P2 scout 角色：config.yaml 有 sage_scout 就用（可指便宜模型），否則回退 sage（向後相容）。
    scout_role = "sage_scout" if "sage_scout" in settings.roles else "sage"
    scout_shared = (
        SCOUT_SHARED_SYSTEM.replace("{ticker}", store.ticker).replace("{digest}", store.digest())
        + _HORIZON_NOTE[horizon]
    )

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
        # sop_trace 過 cite-check（軟揭露：retry 後仍對不上 → 標 unverified，不 refuse）。
        # 空 trace（LLM 未產出步驟）→ 無數字可驗，跳過、不觸 settings.citation。
        report = None
        for attempt in range(_SOP_RECHECK_RETRIES + 1):
            claims = _sop_claims(signal.sop_trace)
            if not claims:
                break
            report = check_claims(claims, sage_store, settings.citation)
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
        signal.unverified = [] if report is None else [
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

    async def _finalize(signal: SageSignal, p: Persona, notify: bool) -> SageSignal:
        signal.sage = p.name
        # P7：neutral 卻漏填 neutral_reason → 程式回填 insufficient_signal（保守：不假裝有方向，
        # 也不擅自說「能力圈外」或「勢均力敵」）。非 neutral 的殘留 reason 清掉，避免誤導 tally。
        if signal.stance == "neutral":
            signal.neutral_reason = signal.neutral_reason or "insufficient_signal"
        else:
            signal.neutral_reason = None
        if notify and on_signal:
            result = on_signal(signal)
            if asyncio.iscoroutine(result):
                await result
        return signal

    async def deep_one(p: Persona) -> SageSignal:
        signal = await (_pack(p) if p.is_pack else _degraded(p))
        return await _finalize(signal, p, notify=True)

    async def scout_one(p: Persona) -> SageSignal:
        """第一輪 scout：便宜模型粗判，轉成輕量 SageSignal（無 sop_trace）。不發 on_signal——
        代表稍後會以深入訊號覆蓋、避免重複；scout-only 者於組裝後統一補發。"""
        sc = await gateway.structured(
            scout_role, system=_persona_identity(p),
            prompt="Give your quick scout read now: stance, confidence (0-1), one-line rationale.",
            schema=ScoutSignal, cache_prefix=scout_shared,
        )
        # review D：scout 未經 deep 的 rule clamp → 套不確定性折扣，scout-only 票才不致過重。
        conf = max(0.0, min(1.0, sc.confidence)) * _SCOUT_CONFIDENCE_DISCOUNT
        sig = SageSignal(stance=sc.stance, confidence=conf,
                         thesis=sc.one_liner, what_would_change_my_mind="",
                         neutral_reason=sc.neutral_reason)
        return await _finalize(sig, p, notify=False)

    async def _run_deep(plist: list[Persona]) -> tuple[list[SageSignal], list[str]]:
        """深入一組大師（Sage Runtime）。硬失敗者經 return_exceptions 轉缺席、不拖垮全場。
        provider 支援快取時先暖一位（共享前綴寫入快取）再 fan-out，否則全平行（決議 4）。"""
        if not plist:
            return [], []
        if sage_caches and len(plist) > 1:
            first = await asyncio.gather(deep_one(plist[0]), return_exceptions=True)
            rest = await asyncio.gather(*(deep_one(p) for p in plist[1:]), return_exceptions=True)
            results = [*first, *rest]
        else:
            results = await asyncio.gather(*(deep_one(p) for p in plist), return_exceptions=True)
        sigs, absent = [], []
        for p, r in zip(plist, results):
            absent.append(p.name) if isinstance(r, BaseException) else sigs.append(r)
        return sigs, absent

    # 單階段 vs 兩階段：房間 ≤ deep 預算則全員深入（scout 反而多花成本）；否則 scout 全員 →
    # 選代表 → 只深入代表，其餘留 scout 粗判（仍計票、不丟票）。預算可由 settings 覆寫（review E）。
    deep_budget = getattr(settings.defaults, "deep_budget", _DEEP_BUDGET)
    scouted_only: list[str] = []
    if len(personas) <= deep_budget:
        signals, absent = await _run_deep(personas)
    else:
        scout_caches = settings.providers[settings.roles[scout_role].provider].has("cache_control")
        if scout_caches and len(personas) > 1:
            # review H：scout 也「先暖一位再 fan-out」——cache_control 啟用時避免其餘同時 miss。
            first = await asyncio.gather(scout_one(personas[0]), return_exceptions=True)
            rest = await asyncio.gather(
                *(scout_one(p) for p in personas[1:]), return_exceptions=True)
            scout_results = [*first, *rest]
        else:
            scout_results = await asyncio.gather(
                *(scout_one(p) for p in personas), return_exceptions=True)
        scouted: list[tuple[Persona, SageSignal]] = []
        absent = []
        for p, r in zip(personas, scout_results):
            absent.append(p.name) if isinstance(r, BaseException) else scouted.append((p, r))
        if not scouted:
            raise RuntimeError("Council failed: scout 階段全員失敗（無人可選為代表）")
        reps = _select_reps(scouted)
        deep_sigs, _deep_absent = await _run_deep(reps)
        deep_by_name = {s.sage: s for s in deep_sigs}
        # 組裝最終訊號：代表用深入訊號；其餘（含深入失敗的代表）退回 scout 粗判，不丟票。
        signals = []
        for p, sc in scouted:
            if p.name in deep_by_name:
                signals.append(deep_by_name[p.name])
            else:
                signals.append(sc)
                scouted_only.append(p.name)
                if on_signal:  # scout-only 統一在此補發（deep 已於 deep_one 內發過）
                    res = on_signal(sc)
                    if asyncio.iscoroutine(res):
                        await res

    if len(signals) * 2 < len(personas):
        raise RuntimeError(
            f"Council failed: only {len(signals)}/{len(personas)} sages returned a "
            f"valid signal (absent: {', '.join(absent)})"
        )
    return tally(signals, personas, absent=absent, abstained=abstained,
                 scouted_only=scouted_only)


def tally(
    signals: list[SageSignal],
    personas: list[Persona],
    absent: list[str] | None = None,
    abstained: list[str] | None = None,
    scouted_only: list[str] | None = None,
) -> CouncilVerdict:
    absent = absent or []
    abstained = abstained or []
    scouted_only = scouted_only or []
    weights = {p.name: p.weight for p in personas}
    counts = {"bullish": 0, "bearish": 0, "neutral": 0}
    neutral_by_reason: dict[str, int] = {}
    score_num = score_den = 0.0
    for s in signals:
        counts[s.stance] += 1
        if s.stance == "neutral":
            # P7：neutral 細分計數（reason 必非 None——one() 已回填；degraded 路徑亦經 one()）
            reason = s.neutral_reason or "insufficient_signal"
            neutral_by_reason[reason] = neutral_by_reason.get(reason, 0) + 1
        w = weights.get(s.sage, 1.0) * s.confidence
        direction = {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0}[s.stance]
        score_num += w * direction
        score_den += w
    weighted = score_num / score_den if score_den else 0.0

    consensus = _consensus_stance(counts)  # review F：與 _select_reps 共用同一份共識規則（DRY）
    outliers = [s.sage for s in signals if s.stance != consensus and s.stance != "neutral"]
    return CouncilVerdict(
        signals=signals, bullish=counts["bullish"], bearish=counts["bearish"],
        neutral=counts["neutral"], weighted_score=round(weighted, 3),
        consensus=consensus, outliers=outliers, absent=absent, abstained=abstained,
        neutral_by_reason=neutral_by_reason, scouted_only=scouted_only,
    )
