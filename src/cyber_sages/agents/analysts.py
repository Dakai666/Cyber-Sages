"""Stage 3 — 分析師團隊。每個 claim 必須引用 evidence id，違者退回重寫。"""

from __future__ import annotations

import asyncio

from cyber_sages.agents.schemas import AnalystReport
from cyber_sages.config import Settings
from cyber_sages.data.evidence import Category, EvidenceStore
from cyber_sages.llm.gateway import LLMGateway
from cyber_sages.verify.citation_check import check_claims

# (key, 職稱, 關注重點, 可見的 evidence 類別)
ANALYSTS: list[tuple[str, str, str, list[Category]]] = [
    (
        "fundamentals", "Fundamentals Analyst",
        "Profitability, growth, balance sheet strength, cash generation. "
        "Compare annual vs latest quarter to spot inflections.",
        ["fundamentals", "profile"],
    ),
    (
        "technical", "Technical Analyst",
        "Give SEPARATE reads for short (1-4週), mid (1-6月), long (6月+) horizons: "
        "trend (price vs SMA20/50/200), momentum (RSI, MACD, returns), volatility, "
        "distance from 52-week range. Identify concrete support/resistance levels "
        "from the SMAs and 52w range.",
        ["history", "quote"],
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
        "and cash flow, implied expectations. First-hand SEC figures take priority.",
        ["fundamentals", "quote", "profile"],
    ),
]

ANALYST_SYSTEM = """\
You are the {title} on an investment research team analyzing {ticker}.
Focus: {focus}

STRICT EVIDENCE RULES — violations get your report rejected:
1. Use ONLY the evidence provided below. No outside knowledge for any number.
2. Every claim must cite the evidence ids it relies on (e.g. ["E012", "E013"]).
3. Quote numbers EXACTLY as they appear in evidence (you may convert units like
   416161000000 USD -> $416.2B, but the value must match within 1%).
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
            report.unverified_claims = []
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
    report.unverified_claims = [
        f"{c.claim.text} ({c.reason})" for c in citation.unverified
    ]
    return report


async def run_all_analysts(
    store: EvidenceStore, settings: Settings, gateway: LLMGateway,
) -> list[AnalystReport]:
    return list(await asyncio.gather(*[
        run_analyst(key, title, focus, cats, store, settings, gateway)
        for key, title, focus, cats in ANALYSTS
    ]))
