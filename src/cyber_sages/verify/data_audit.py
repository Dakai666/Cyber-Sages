"""Stage 2 — 資料審核閘門。

兩層：
1. 確定性檢查（程式）：必要資料齊全、新鮮度、跨來源一致性、基本 sanity。
2. LLM 稽核員：找程式抓不到的語意異常（如 EPS 與 net_income/shares 對不上）。

error → 管線降級（degraded mode，最終報告強制揭露）；warning → 通過但標記。
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from cyber_sages.config import AuditConfig, Settings
from cyber_sages.data.evidence import EvidenceStore
from cyber_sages.llm.gateway import LLMGateway

Severity = Literal["error", "warning"]


class AuditFinding(BaseModel):
    severity: Severity
    check: str
    message: str
    evidence_ids: list[str] = Field(default_factory=list)


class AuditReport(BaseModel):
    findings: list[AuditFinding] = Field(default_factory=list)
    auditor_summary: str = ""

    @property
    def errors(self) -> list[AuditFinding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def degraded(self) -> bool:
        return len(self.errors) > 0


def deterministic_checks(store: EvidenceStore, cfg: AuditConfig) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    today = date.today()

    def field_evs(category: str, field: str):
        return [e for e in store.items if e.category == category and e.field == field]

    # 1. 必要資料齊全
    required = [
        ("quote", ["last_price", "latest_close"], "error", "no usable price"),
        ("fundamentals", ["revenue_annual", "net_income_annual"], "error", "no first-hand financials"),
        ("history", ["sma_20"], "warning", "no price history / indicators"),
        ("news", None, "warning", "no recent news"),
    ]
    for category, fields, severity, msg in required:
        evs = store.by_category(category)  # type: ignore[arg-type]
        if fields is not None:
            evs = [e for e in evs if e.field in fields]
        if not evs:
            findings.append(AuditFinding(
                severity=severity, check="completeness",
                message=f"Missing {category} data: {msg}",
            ))

    # 2. 新鮮度
    for ev in field_evs("quote", "latest_close"):
        if ev.as_of and (today - ev.as_of).days > cfg.max_quote_age_days:
            findings.append(AuditFinding(
                severity="error", check="freshness",
                message=f"Quote is {(today - ev.as_of).days} days old (max {cfg.max_quote_age_days})",
                evidence_ids=[ev.id],
            ))
    fund = store.by_category("fundamentals")
    if fund:
        newest = max((e.as_of for e in fund if e.as_of), default=None)
        if newest and (today - newest).days > cfg.max_fundamentals_age_days:
            findings.append(AuditFinding(
                severity="warning", check="freshness",
                message=f"Newest financial statement is {(today - newest).days} days old",
            ))

    # 3. 跨來源價格一致性
    live = field_evs("quote", "last_price")
    close = field_evs("quote", "latest_close")
    if live and close:
        a, b = float(live[0].value), float(close[0].value)
        if b > 0:
            div = abs(a - b) / b * 100
            if div > cfg.max_price_divergence_pct:
                findings.append(AuditFinding(
                    severity="error", check="cross_source",
                    message=f"Price divergence {div:.1f}% between sources "
                            f"({a} vs {b}, max {cfg.max_price_divergence_pct}%)",
                    evidence_ids=[live[0].id, close[0].id],
                ))

    # 4. sanity：價格類數值必須為正
    for ev in store.by_category("quote"):
        if ev.field in ("last_price", "latest_close") and float(ev.value) <= 0:
            findings.append(AuditFinding(
                severity="error", check="sanity",
                message=f"Non-positive price in {ev.id}: {ev.value}",
                evidence_ids=[ev.id],
            ))

    # 5. 內部一致性：EPS ≈ net_income / shares（寬容差，股數會變動）
    eps = field_evs("fundamentals", "eps_diluted_annual")
    ni = field_evs("fundamentals", "net_income_annual")
    sh = field_evs("fundamentals", "shares_outstanding")
    if eps and ni and sh and float(sh[0].value) > 0:
        implied = float(ni[0].value) / float(sh[0].value)
        reported = float(eps[0].value)
        if reported > 0 and abs(implied - reported) / reported > 0.25:
            findings.append(AuditFinding(
                severity="warning", check="internal_consistency",
                message=f"EPS {reported} vs implied net_income/shares {implied:.2f} "
                        "diverge >25% (share count drift or data issue)",
                evidence_ids=[eps[0].id, ni[0].id, sh[0].id],
            ))
    return findings


class _AuditorOutput(BaseModel):
    findings: list[AuditFinding] = Field(default_factory=list)
    summary: str


AUDITOR_SYSTEM = """\
You are a meticulous financial data auditor. You receive a raw evidence dump for one stock.
Your ONLY job is to find data quality problems BEFORE analysts use this data:
- values that contradict each other across sources
- magnitudes that look implausible for this company
- stale or suspiciously missing data
- unit confusion (e.g. a value that looks like millions recorded as raw dollars)

You do NOT give investment opinions. Only report genuine data problems; an empty findings
list is the correct answer for clean data. severity "error" = analysts must not trust this
field; "warning" = usable with caution. Reference evidence ids."""


async def run_audit(
    store: EvidenceStore, settings: Settings, gateway: LLMGateway
) -> AuditReport:
    findings = deterministic_checks(store, settings.audit)
    summary = ""
    try:
        out = await gateway.structured(
            "data_auditor",
            system=AUDITOR_SYSTEM,
            prompt=f"Ticker: {store.ticker}\n\nEvidence dump:\n{store.digest()}",
            schema=_AuditorOutput,
        )
        findings.extend(out.findings)
        summary = out.summary
    except Exception as e:
        findings.append(AuditFinding(
            severity="warning", check="auditor_agent",
            message=f"LLM auditor unavailable ({e}); only deterministic checks ran",
        ))
    return AuditReport(findings=findings, auditor_summary=summary)
