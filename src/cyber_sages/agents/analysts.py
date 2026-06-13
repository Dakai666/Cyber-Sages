"""Stage 3 — 分析師團隊。每個 claim 必須引用 evidence id，違者退回重寫。"""

from __future__ import annotations

import asyncio

from cyber_sages.agents.schemas import AnalystReport, UnverifiedClaim
from cyber_sages.config import Settings
from cyber_sages.data.evidence import Category, EvidenceStore
from cyber_sages.llm.gateway import LLMGateway
from cyber_sages.verify.citation_check import check_claims

# (key, 職稱, 關注重點, 可見的 evidence 類別)
ANALYSTS: list[tuple[str, str, str, list[Category]]] = [
    (
        "fundamentals", "Fundamentals Analyst",
        "Profitability, growth, balance sheet strength, cash generation. "
        "Compare annual vs latest quarter to spot inflections. Forward analyst "
        "consensus (estimate category) may contextualise growth — treat it as a "
        "forward estimate, not a first-hand fact.",
        ["fundamentals", "profile", "estimate"],
    ),
    (
        "technical", "Technical Analyst",
        "Give SEPARATE reads for short (1-4週), mid (1-6月), long (6月+) horizons: "
        "trend (price vs SMA20/50/200), momentum (RSI, MACD, returns), volatility, "
        "distance from 52-week range. Identify concrete support/resistance levels "
        "from the SMAs and 52w range. If 籌碼 (chips) evidence is present — 台股三大法人"
        "買賣超 (foreign/trust/dealer net) and 融資融券餘額 (margin/short balances), or US "
        "short interest (short_percent_of_float, short_ratio days-to-cover, MoM change) — "
        "read it as an institutional-flow / positioning / leverage signal alongside the "
        "technicals (US short interest is second-hand FINRA data; TW 借券+融券 is a proxy).",
        ["history", "quote", "chips"],
    ),
    (
        "sentiment", "News & Sentiment Analyst",
        "Short-term market mood: what recent headlines imply for the next weeks; "
        "separate signal from noise; note catalysts with dates and controversies.",
        ["news", "profile", "quote"],
    ),
    (
        "valuation", "Valuation Analyst",
        "Whether the current price is justified: P/E vs growth, market cap vs revenue "
        "and cash flow, implied expectations. First-hand SEC figures take priority. "
        "When computing P/E, anchor on TRAILING-TWELVE-MONTH earnings: use eps_ttm / "
        "revenue_ttm if present (台股 FinMind 財報為單季值，切勿用單季 EPS×4 或單季 EPS "
        "直接除股價，否則本益比會偏離數倍). Forward P/E and PEG may use the estimate "
        "category (forward_eps, target_mean_price, *_growth_est) — label these as "
        "analyst consensus / forward estimates, never as realised first-hand figures.",
        ["fundamentals", "quote", "profile", "estimate"],
    ),
    (
        "macro", "Macro Analyst",
        "The macro regime and what it implies for risk assets and THIS stock's sector: "
        "rate level and direction (fed funds, 2y/10y), the 10y-2y curve (inversion = "
        "recession signal), inflation trend (CPI YoY), and labor market (unemployment, "
        "nonfarm payrolls). Translate the macro backdrop into a tailwind/headwind for "
        "the name — do NOT restate raw numbers without an investment implication.",
        ["macro"],
    ),
]

ANALYST_SYSTEM = """\
You are the {title} on an investment research team analyzing {ticker}.
Focus: {focus}

STRICT EVIDENCE RULES — violations get your report rejected:
1. Use ONLY the evidence provided below. No outside knowledge for any number.
2. Every claim must cite the evidence ids it relies on (e.g. ["E012", "E013"]).
3. Quote numbers EXACTLY as they appear in evidence (you may convert units like
   416161000000 USD -> $416.2B, but the value must match within 1%). When the evidence
   line shows a scale hint (e.g. "≈1.13兆", "≈24.2B"), use THAT scale verbatim — do not
   re-derive 億/兆/B yourself (a 10x slip like writing 兆-scale as 億 fails verification).
   For signed flow values (籌碼 net buy/sell), keep the sign: a negative value is 賣超/
   淨流出 — never cite its positive magnitude.
4. If evidence is missing for something important, say so in your summary instead
   of guessing.

Write `summary` and claim texts in Traditional Chinese (繁體中文), keeping tickers
and technical terms in English."""


async def run_analyst(
    key: str, title: str, focus: str, categories: list[Category],
    store: EvidenceStore, settings: Settings, gateway: LLMGateway,
) -> AnalystReport:
    system = ANALYST_SYSTEM.format(title=title, focus=focus, ticker=store.ticker)
    evidence_text = store.digest(categories)
    prompt = f"Evidence for {store.ticker}:\n{evidence_text}\n\nWrite your analyst report."

    report: AnalystReport | None = None
    for attempt in range(settings.citation.max_rewrite_attempts + 1):
        report = await gateway.structured("analyst", system=system, prompt=prompt,
                                          schema=AnalystReport)
        report.analyst = title
        citation = check_claims(report.claims, store, settings.citation)
        if citation.all_verified:
            report.unverified = []
            return report
        if attempt < settings.citation.max_rewrite_attempts:
            failures = "\n".join(
                f"- \"{c.claim.text}\" -> {c.reason}" for c in citation.unverified
            )
            prompt = (
                f"Evidence for {store.ticker}:\n{evidence_text}\n\n"
                f"Your previous report had claims that FAILED citation verification:\n"
                f"{failures}\n\nRewrite the full report. Fix or drop the failing claims."
            )
    # 重寫額度用完：保留報告但標記未通過驗證的 claim（最終報告強制揭露）
    citation = check_claims(report.claims, store, settings.citation)
    report.unverified = [
        UnverifiedClaim(text=c.claim.text, evidence_ids=c.claim.evidence_ids,
                        reason=c.reason, kind=c.kind)
        for c in citation.unverified
    ]
    return report


async def run_all_analysts(
    store: EvidenceStore, settings: Settings, gateway: LLMGateway,
) -> list[AnalystReport]:
    # 只跑「至少有一類可見證據」的分析師：總經分析師在沒有 macro 證據時自動缺席，
    # 不會憑空產出（反幻覺）。
    active = [
        (key, title, focus, cats) for key, title, focus, cats in ANALYSTS
        if any(store.by_category(c) for c in cats)
    ]
    return list(await asyncio.gather(*[
        run_analyst(key, title, focus, cats, store, settings, gateway)
        for key, title, focus, cats in active
    ]))
