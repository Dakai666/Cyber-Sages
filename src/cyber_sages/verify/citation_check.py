"""Stage 4 — 引用驗證閘門。

分析師報告的每個 claim 都帶 evidence_ids；本模組用確定性程式驗證：
claim 文字裡的每個「有意義的數字」必須能在其引用的 evidence 中找到（容差內）。

「有意義的數字」= 帶 $、%、x 倍數、K/M/B/T 量級字尾、或小數點的數字。
裸整數（年份、SMA 視窗天數、排名）天生模糊，不納入驗證。
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from cyber_sages.config import CitationConfig
from cyber_sages.data.evidence import EvidenceStore


class Claim(BaseModel):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)


# 失敗種類由驗證層權威認定（不在 presentation 端用 reason 子串猜）
FailureKind = Literal["ok", "bad_ref", "no_cite", "num_mismatch"]


class ClaimCheck(BaseModel):
    claim: Claim
    verified: bool
    reason: str = ""
    kind: FailureKind = "ok"


class CitationReport(BaseModel):
    checks: list[ClaimCheck] = Field(default_factory=list)

    @property
    def unverified(self) -> list[ClaimCheck]:
        return [c for c in self.checks if not c.verified]

    @property
    def all_verified(self) -> bool:
        return not self.unverified


# 行內引用 [E012] 的 id 抽取——chief / judge / debater 文字共用同一真相來源。
# `\d{3,}`（非 {3}）：evidence 數 > 999 時 id 為 E1000+，{3} 會 silent 截成 E100 引到錯 id。
EVIDENCE_ID_RE = re.compile(r"E\d{3,}")


_SUFFIX = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12, "兆": 1e12, "億": 1e8, "萬": 1e4}

# -$416.2B / 35.3x / -5.2% / 7.46 / 416,161,000,000 / 4.283兆 / 1,134.1B
# 千分位群組後允許小數（1,134.1），否則「1,134.1B」會被拆成 1,134 與 1B 兩段錯誤值。
_NUM_RE = re.compile(
    r"(?P<sign>-)?"
    r"(?P<currency>[$])?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d+)"
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


# W4 — 衍生白名單（Spec B 決議 2，手列）。
#
# 舊版對所有引用值做全笛卡兒積（任兩數都衍生差值/比率/百分比/變動率），證據一多
# 候選集爆量，1% 容差下偽造數字極易誤中某個無意義的跨欄位組合。改為先把每個 evidence
# 依「欄位語意」粗分 kind，只有白名單內的 (kind, kind) 配對才衍生——且每種配對只開
# 語意上說得通的運算（價格×價格→差值/變動率；金額×金額→比率/百分比；價格×每股→倍數）。
#
# 刻意不配的鏈式組合（如 P/E × shares × EPS 回推股價、price × shares 算市值）：源頭
# 該把市值/比率等以自身 evidence 呈現，分析師直接引用，而非靠驗證層拼湊放行。
_PRICE_FIELD_HINTS = ("price", "close", "open", "high", "low", "sma", "ema",
                      "vwap", "band", "52w")
# 基本面裡本身就是比率/百分比的欄位不算「金額」（不可再被當分子分母衍生新比率）
_RATIO_FIELD_HINTS = ("margin", "ratio", "roe", "coverage", "yield", "_pct", "_to_")


def _kind(ev) -> str:
    """把 evidence 粗分為參與衍生的語意類別；不可衍生者回 'other'（僅供原值直配）。"""
    field = (ev.field or "").lower()
    if "eps" in field:
        return "per_share"
    if ev.category in ("quote", "history") and any(h in field for h in _PRICE_FIELD_HINTS):
        return "price"
    if ev.category == "fundamentals" and not any(h in field for h in _RATIO_FIELD_HINTS):
        return "magnitude"
    return "other"


def _derive(a: float, b: float, ops: set[str]) -> list[float]:
    out: list[float] = []
    if "diff" in ops:
        out += [a - b, b - a]                       # 差值（乖離/價差），方向不拘
    if "ratio" in ops and a != 0 and b != 0:
        out += [a / b, b / a]                        # 倍數（ROE、P/E）
    if "ratio_pct" in ops and a != 0 and b != 0:
        out += [a / b * 100, b / a * 100]            # 百分比（利潤率）
    if "pct" in ops and a != 0 and b != 0:
        # 變動率／距離 %：中文「上漲/下跌 X%」措辭天生帶號（X 永遠寫成正數），
        # 故此一種——且僅此一種——同時放行兩個方向與其絕對值，讓「下跌 5%」對得上
        # 真實的 -5% 變動。比率/利潤率不放寬（其符號本身有意義）。
        for c in ((a - b) / b * 100, (b - a) / a * 100):
            out += [c, -c]
    return out


# (kind, kind)（無序）→ 允許的衍生運算
_PAIR_OPS: dict[frozenset[str], set[str]] = {
    frozenset({"price"}): {"diff", "pct"},              # 價差 + 乖離/距高低點 %
    frozenset({"magnitude"}): {"ratio", "ratio_pct"},   # ROE/負債比 + 利潤率
    frozenset({"price", "per_share"}): {"ratio"},       # P/E
    frozenset({"magnitude", "per_share"}): {"ratio"},   # shares ≈ net_income/eps
}


def _candidate_values(cited) -> list[float]:
    """可供比對的值：引用 evidence 的原值、字串證據（如新聞）內的數字，以及白名單內
    配對的正確算術衍生（見上方 _PAIR_OPS）。

    刻意只納入「明確正確且語意成立」的衍生，不做量級容忍、不做跨類亂配——那會把源頭
    的單位/方向錯誤、或無關欄位的巧合組合一起放行。源頭該做的是把數字以可讀量級呈現
    （見 Evidence.digest_line），讓分析師直接引用正確值，而非靠驗證層事後猜測。"""
    base: list[float] = []                       # 所有可直接比對的原值（含 news 字串內數字）
    typed: list[tuple[float, str]] = []          # 參與衍生的 (值, kind)
    for e in cited:
        if isinstance(e.value, (int, float)):
            v = float(e.value)
            base.append(v)
            k = _kind(e)
            if k != "other":
                typed.append((v, k))
        elif isinstance(e.value, str):
            base.extend(extract_meaningful_numbers(e.value))
    derived: list[float] = []
    for i, (a, ka) in enumerate(typed):
        for b, kb in typed[i + 1:]:
            ops = _PAIR_OPS.get(frozenset({ka, kb}))
            if ops:
                derived.extend(_derive(a, b, ops))
    return base + derived


def check_claim(claim: Claim, store: EvidenceStore, cfg: CitationConfig) -> ClaimCheck:
    # 引用的 evidence 必須存在
    cited = []
    for eid in claim.evidence_ids:
        ev = store.get(eid)
        if ev is None:
            return ClaimCheck(claim=claim, verified=False, kind="bad_ref",
                              reason=f"cites nonexistent evidence id {eid}")
        cited.append(ev)
    if not cited:
        return ClaimCheck(claim=claim, verified=False, kind="no_cite",
                          reason="no evidence cited")

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
            claim=claim, verified=False, kind="num_mismatch",
            reason=f"numbers {unmatched} not found in cited evidence "
                   f"(cited values: {direct}, incl. derived ratios)",
        )
    return ClaimCheck(claim=claim, verified=True, reason="all numbers match cited evidence")


def check_claims(claims: list[Claim], store: EvidenceStore, cfg: CitationConfig) -> CitationReport:
    return CitationReport(checks=[check_claim(c, store, cfg) for c in claims])
