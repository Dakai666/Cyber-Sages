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
from cyber_sages.verify.citation_check import (
    EVIDENCE_ID_RE,
    Claim,
    CitationReport,
    check_claims,
    extract_meaningful_numbers,
)
from cyber_sages.verify.data_audit import AuditReport, build_health_card

# W7：chief brief 主體（thesis / 風險 / 翻盤條件）過 cite-check 的重試次數。
# 決議 5：retry 1 次（驗證錯誤回饋進 prompt，與 analyst 階段同機制）→ 仍失敗則標
# unverified 並在 brief 揭露，不 refuse（degraded-but-disclosed 一貫優於整條報廢）。
_CHIEF_RECHECK_RETRIES = 1

# D-4：風控官給出重大質疑（clamped 調整 ≤ 此閾值）時，chief 自動做 1 輪回應重寫 thesis
# 關鍵段。決議 2：固定 1 輪——簡單可控、成本可預測；動態多輪等 Phase 6 重放工具量化後再評估。
_RISK_REBUTTAL_THRESHOLD = -0.2

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
3. Give exactly three timeframes: short (1-4週), mid (1-6月), long (6月+). They
   may disagree with each other — say so plainly.
4. Synthesize, don't average: if the minority argued better evidence, side with
   it. Be honest about dissent in dissent_summary.
5. conviction reflects evidence strength AND data quality; position_hint must
   translate it into sizing (e.g. 0.3 → 試單1/4倉 or 空手等待).
6. ANCHOR every quantitative claim. In thesis / key_risks / what_would_change_my_mind,
   put an inline evidence id like [E012] right where each number appears — the number is
   verified against the evidence you cite there, NOT the whole store. Cite at the paragraph
   level (each paragraph ≥1 id) and use at least 3 distinct ids across the brief. Narrative
   connective sentences need no id, but any number you cannot anchor is a failure — drop it
   or anchor it. Do not invent ids; cite only ids shown in the evidence sections below.
Never write filler like 僅供參考 / 本報告不構成建議 — write as if real money
moves on this brief. Write all text fields in Traditional Chinese (繁體中文),
tickers/terms in English."""

RISK_SYSTEM = """\
You are the Risk Officer. You did not participate in the analysis; you only
sanity-check the final verdict. Look for: overconfidence vs the actual evidence,
data-quality issues being glossed over, concentration of the thesis on one
fragile assumption — but ALSO for the opposite: a chief that is too timid when the
evidence is actually strong and clean.
Suggest a conviction_adjustment in [-0.3, +0.2]:
- NEGATIVE (down to -0.3) when conviction outruns the evidence or risks are unpriced.
- POSITIVE (up to +0.2 only) when the chief is needlessly conservative AND the evidence
  is strong and data quality is clean — give some conviction back.
Stay default-cautious: your downward capacity (0.3) exceeds your upward (0.2) by design.
Always fill adjustment_reason with a one-sentence justification (繁體中文), whichever way
you move (including 0). Write concerns in Traditional Chinese."""


# action plan 口徑隨 horizon（Spec C v2 P9）：同一支股，當沖與長持的進出場紀律本質不同。
# 命名區隔（#49）：brief 內 short/mid/long 三段已改名 HorizonView.timeframe（prompt 用
# 「three timeframes」），與本 run 的 trading/value 投資框架（run-level Horizon）明確分流。
_HORIZON_PLAN = {
    "value": "# Action-plan 口徑：VALUE（數年）——分批建倉、較寬的停損（容忍多年波動）、"
             "進場錨在估值/長期支撐，invalidation 是長期論點翻盤（護城河受損 / 成長失速），"
             "position_hint 偏長期分批。brief 的 short/mid/long 三段以「持有數年」的角度寫。",
    "trading": "# Action-plan 口徑：TRADING（數天~數週）——明確進場區與緊停損（按近期波動/"
               "結構）、清楚的獲利目標與短線失效訊號（跌破關鍵均線/動能轉弱），invalidation 是"
               "短線結構破壞，position_hint 偏快進快出。brief 的 short/mid/long 三段以「數天~數週」"
               "的角度寫。",
}


async def run_synthesis(
    store: EvidenceStore,
    reports: list[AnalystReport],
    council: CouncilVerdict,
    debate: DebateVerdict | None,
    audit: AuditReport,
    settings: Settings,
    gateway: LLMGateway,
    horizon: str = "value",
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
    analyst_text = "\n".join(f"- {r.analyst}: {r.summary}" for r in reports)

    # D-1：行內引用需要 chief 看得到要引的 id。價格/技術/基本面單列（行動計畫的價位錨點），
    # 其餘類別（news/chips/macro/estimate/profile/reference）一併供出，讓 thesis 任何數字都有
    # 可引的 id——否則改成行內驗證後，引用非價格類數字會被誤判 no_cite。
    other = store.digest(["news", "chips", "macro", "estimate", "profile", "reference"])
    prompt = (
        f"Stock: {store.ticker}\n\n"
        f"# Price & technical anchors (use these for your levels)\n"
        f"{store.digest(['quote', 'history'])}\n\n"
        f"# Fundamental anchors\n{store.digest(['fundamentals'])}\n\n"
        f"# Other evidence (cite these inline in your thesis where relevant)\n"
        f"{other or '（無）'}\n\n"
        f"# Data audit findings\n{audit_text}\n\n"
        f"# Unverified claims (flagged, treat with suspicion)\n"
        f"{chr(10).join(unverified) or 'none'}\n\n"
        f"# Analyst summaries\n{analyst_text}\n\n"
        f"# Council vote\n{council_text}\n\n"
        f"# Sage theses\n"
        + "\n".join(f"- {s.sage} [{s.stance}]: {s.thesis}" for s in council.signals)
        + f"\n\n# Debate verdict\n{debate_text}\n\n"
        + _HORIZON_PLAN[horizon]
        + "\nWrite the decision brief: action plan with anchored levels, three timeframes "
        "(short/mid/long), thesis, risks, invalidation."
    )

    # W7 + D-1 — chief brief 主體過 cite-check：thesis / key_risks / what_would_change_my_mind
    # 內的數字必須能由其行內 [E0xx] 引用的 evidence 推導，否則回饋重寫一次；仍失敗標 unverified。
    verdict, report = await _chief_brief(gateway, prompt, store, settings.citation)

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

    # D-4 — chief↔risk 固定 1 輪迭代：風控官重大質疑（clamped 調整 ≤ -0.2）時，chief 看到質疑
    # 後重寫 thesis 關鍵段正面回應（補強或讓步），不得忽略；重寫版同樣過 cite-check。
    if risk.clamped_adjustment <= _RISK_REBUTTAL_THRESHOLD:
        concerns = "\n".join(f"- {c}" for c in risk.concerns) or "（未列具體條目）"
        rebut_prompt = (
            f"{prompt}\n\n# 風控官對你上一版的重大質疑（信心建議調整 "
            f"{risk.conviction_adjustment:+.2f}）\n{concerns}\n"
            f"理由：{risk.adjustment_reason or '（未說明）'}\n\n"
            "重寫 brief：在 thesis（必要時連同 key_risks）直接回應上述風控質疑——"
            "若質疑成立就讓步並調整論點，若有反證就以 evidence 正面反駁；不得忽略或重述原文。"
        )
        verdict, report = await _chief_brief(gateway, rebut_prompt, store, settings.citation)

    verdict.unverified = [
        # 行內引用檢查，回填該 claim 實際引用的 id，讓讀者看出是哪些錨點對不上。
        UnverifiedClaim(text=c.claim.text, evidence_ids=c.claim.evidence_ids,
                        reason=c.reason, kind=c.kind)
        for c in report.unverified
    ]
    _flag_unanchored_levels(verdict, store)

    adj = risk.clamped_adjustment
    verdict.conviction = round(max(0.0, min(1.0, verdict.conviction + adj)), 2)
    # S7：信心上限由「分維度健康度評分卡」的最壞維度推導，取代舊的全域一律封頂 0.5。
    # 核心維度（price/technical/fundamentals）受損才腰斬 0.5；僅周邊（sentiment/macro）受損
    # 輕罰 0.7——讓 macro 缺不再無謂腰斬一個短線技術裁定。揭露明確指出壞在哪一維度。
    card = build_health_card(audit, store)
    if card.confidence_cap is not None:
        verdict.conviction = round(min(verdict.conviction, card.confidence_cap), 2)
        hurt = [f"{d}（{h.status}）" for d, h in card.dimensions.items()
                if h.status in ("degraded", "fatal")]
        verdict.key_risks.insert(
            0, f"資料品質降級：{'、'.join(hurt)} 受損，信心上限 {card.confidence_cap}")
    return verdict, risk


async def _chief_brief(
    gateway: LLMGateway, prompt: str, store: EvidenceStore, cfg
) -> tuple[FinalVerdict, CitationReport]:
    """產 chief brief 並過 cite-check：失敗回饋重寫一次（_CHIEF_RECHECK_RETRIES）。
    回傳 (verdict, 最終 report)；仍失敗的 claim 由呼叫端標 unverified 揭露（不 refuse）。"""
    verdict = await gateway.structured("chief", system=CHIEF_SYSTEM, prompt=prompt,
                                       schema=FinalVerdict)
    report = check_claims(_chief_claims(verdict), store, cfg)
    for _ in range(_CHIEF_RECHECK_RETRIES):
        if report.all_verified:
            break
        failures = "\n".join(f'- "{c.claim.text}" -> {c.reason}'
                             for c in report.unverified)
        verdict = await gateway.structured(
            "chief", system=CHIEF_SYSTEM,
            prompt=(f"{prompt}\n\n# 你上一版 brief 有數字無法由其行內引用的 evidence 推導\n"
                    f"{failures}\n\n重寫 brief：修正數字、補上正確的行內 [E0xx] 引用，或刪除 "
                    "thesis / key_risks / what_would_change_my_mind 內這些對不上 evidence 的數字。"),
            schema=FinalVerdict,
        )
        report = check_claims(_chief_claims(verdict), store, cfg)
    return verdict, report


def _chief_claims(verdict: FinalVerdict) -> list[Claim]:
    """把 chief brief 主體拆成可驗的 claim：thesis / 翻盤條件 / 每條風險。
    D-1：每條 claim 只引用其文字內行內標註的 [E0xx] id（非全 store）——數字必須能由 chief
    實際引用的那幾筆 evidence 推導，攔截「引對 id 卻寫錯數字」與「寫數字卻不引用」。
    無有意義數字的純敘事段不建 claim（敘事連接句不強制引用，決議 1）。"""
    texts = [verdict.thesis, verdict.what_would_change_my_mind, *verdict.key_risks]
    claims = []
    for t in texts:
        if not (t and t.strip()) or not extract_meaningful_numbers(t):
            continue  # 純質性段：無數字可驗，略過（行內密度由 prompt 引導，非硬閘門）
        ids = sorted(set(EVIDENCE_ID_RE.findall(t)))
        claims.append(Claim(text=t, evidence_ids=ids))
    return claims


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
