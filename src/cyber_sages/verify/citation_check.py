"""Stage 4 — 引用驗證閘門。

分析師報告的每個 claim 都帶 evidence_ids；本模組用確定性程式驗證：
claim 文字裡的每個「有意義的數字」必須能在其引用的 evidence 中找到（容差內）。

「有意義的數字」= 帶 $、%、x 倍數、K/M/B/T 量級字尾、或小數點的數字。
裸整數（年份、SMA 視窗天數、排名）天生模糊，不納入驗證。
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from cyber_sages.config import CitationConfig
from cyber_sages.data.evidence import EvidenceStore


class Claim(BaseModel):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)


class ClaimCheck(BaseModel):
    claim: Claim
    verified: bool
    reason: str = ""


class CitationReport(BaseModel):
    checks: list[ClaimCheck] = Field(default_factory=list)

    @property
    def unverified(self) -> list[ClaimCheck]:
        return [c for c in self.checks if not c.verified]

    @property
    def all_verified(self) -> bool:
        return not self.unverified


_SUFFIX = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12, "兆": 1e12, "億": 1e8, "萬": 1e4}

# -$416.2B / 35.3x / -5.2% / 7.46 / 416,161,000,000 / 4.283兆
_NUM_RE = re.compile(
    r"(?P<sign>-)?"
    r"(?P<currency>[$])?"
    r"(?P<num>\d{1,3}(?:,\d{3})+|\d+\.\d+|\d+)"
    r"\s*(?P<suffix>[kKmMbBtT](?![a-zA-Z])|[兆億萬]|%|x(?![a-zA-Z]))?"
)


def extract_meaningful_numbers(text: str) -> list[float]:
    """抓出需要驗證的數字，正規化為絕對值（負號保留）。"""
    out: list[float] = []
    for m in _NUM_RE.finditer(text):
        raw, suffix, currency = m.group("num"), m.group("suffix"), m.group("currency")
        has_decimal = "." in raw
        has_comma = "," in raw
        value = float(raw.replace(",", ""))
        if suffix and suffix.lower() in _SUFFIX:
            value *= _SUFFIX[suffix.lower()]
        if m.group("sign"):
            value = -value
        meaningful = bool(currency or suffix or has_decimal or has_comma)
        if not meaningful:
            continue  # 裸整數不驗證（年份/視窗天數/排名）
        out.append(value)
    return out


def _matches(claim_num: float, evidence_val: float, tol_pct: float) -> bool:
    if evidence_val == 0:
        return abs(claim_num) < 1e-9
    return abs(claim_num - evidence_val) / abs(evidence_val) * 100 <= tol_pct


def _candidate_values(cited) -> list[float]:
    """可供比對的值：引用 evidence 的原值、字串證據（如新聞）內的數字，
    以及任兩原值的簡單衍生（比率、百分比、變動率）——讓「ROE = 淨利/權益」
    這類正確算術不會被誤判。"""
    base: list[float] = []
    for e in cited:
        if isinstance(e.value, (int, float)):
            base.append(float(e.value))
        elif isinstance(e.value, str):
            base.extend(extract_meaningful_numbers(e.value))
    derived: list[float] = []
    for a in base:
        for b in base:
            if b == 0 or a == b:
                continue
            derived.append(a / b)             # 比率（ROE、倍數）
            derived.append(a / b * 100)       # 百分比（利潤率）
            derived.append((a - b) / abs(b) * 100)  # 變動率／距離 %
    return base + derived


def check_claim(claim: Claim, store: EvidenceStore, cfg: CitationConfig) -> ClaimCheck:
    # 引用的 evidence 必須存在
    cited = []
    for eid in claim.evidence_ids:
        ev = store.get(eid)
        if ev is None:
            return ClaimCheck(claim=claim, verified=False,
                              reason=f"cites nonexistent evidence id {eid}")
        cited.append(ev)
    if not cited:
        return ClaimCheck(claim=claim, verified=False, reason="no evidence cited")

    numbers = extract_meaningful_numbers(claim.text)
    if not numbers:
        # 質性 claim：有引用就算通過（語意正確性由辯論階段對抗）
        return ClaimCheck(claim=claim, verified=True, reason="qualitative claim with citation")

    candidates = _candidate_values(cited)
    unmatched = [
        n for n in numbers
        if not any(_matches(n, v, cfg.numeric_tolerance_pct) for v in candidates)
    ]
    if unmatched:
        direct = [float(e.value) for e in cited if isinstance(e.value, (int, float))]
        return ClaimCheck(
            claim=claim, verified=False,
            reason=f"numbers {unmatched} not found in cited evidence "
                   f"(cited values: {direct}, incl. derived ratios)",
        )
    return ClaimCheck(claim=claim, verified=True, reason="all numbers match cited evidence")


def check_claims(claims: list[Claim], store: EvidenceStore, cfg: CitationConfig) -> CitationReport:
    return CitationReport(checks=[check_claim(c, store, cfg) for c in claims])
