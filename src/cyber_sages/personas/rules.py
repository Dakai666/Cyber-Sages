"""Persona Pack — 硬規則 DSL evaluator + confidence clamp（三段執行的第 2、4 段）。

純函式、零 LLM、零 I/O：條件直接對 EvidenceStore 的 canonical 欄位求值，**程式判定**
（Spec E 設計：彈性由自然語言 `exceptions` 在 SOP pass 補回，硬規則保持可驗證）。

DSL 刻意極簡：`{field, op, value}` 三元組 + `all`/`any` 巢狀，欄位名對齊 Spec A canonical。

not_evaluable（Spec E）：規則引用的欄位缺資料時，整條記 not_evaluable（不觸發、不假裝
通過）——這本身就是有意義的訊號（「我平常看的東西這次看不到」），注入 SOP pass 並列在
signal 上。判定採保守解：條件樹內**任一**被引用欄位缺值即 not_evaluable（含 any 分支）。

clamp（Spec E「E1 實作決議」#1，DK 定案）：
- `cap_confidence`（ceiling）：一律程式硬收，與 stance 無關。
- `bullish_floor`/`bearish_floor`：只在 LLM 已**同向**表態時套 confidence floor；反向時
  保留 LLM 的 stance、不翻轉，把衝突記入 rule_conflicts 揭露（LLM 推理為主、程式收口為輔）。
"""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, model_validator

_OPS: dict[str, Callable[[float, float], bool]] = {
    ">": operator.gt, ">=": operator.ge,
    "<": operator.lt, "<=": operator.le,
    "==": operator.eq, "!=": operator.ne,
}

Action = Literal["cap_confidence", "bullish_floor", "bearish_floor"]


class HardRule(BaseModel):
    """rules.yaml 的一條硬規則。`if` 是 Python 關鍵字，故以 alias 對應 yaml 的 `if:`。

    fail-loud（B2，反 silent-failure 一貫哲學）：`action` 是 Literal，typo 直接驗證失敗；
    `cap_confidence` 必須帶 `confidence_ceiling`、directional floor 必須帶 `confidence_floor`，
    否則該規則永遠靜默不作用——載入時即報錯，不讓壞規則溜進 Pack。
    """
    id: str
    if_: dict[str, Any] = Field(alias="if")
    action: Action
    confidence_ceiling: float | None = None
    confidence_floor: float | None = None
    note: str = ""

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _require_matching_bound(self) -> "HardRule":
        if self.action == "cap_confidence" and self.confidence_ceiling is None:
            raise ValueError(f"rule {self.id}: action=cap_confidence 需要 confidence_ceiling")
        if self.action in ("bullish_floor", "bearish_floor") and self.confidence_floor is None:
            raise ValueError(f"rule {self.id}: action={self.action} 需要 confidence_floor")
        return self


@dataclass
class RuleOutcome:
    rule_id: str
    triggered: bool = False
    not_evaluable: bool = False
    action: str = ""
    confidence_ceiling: float | None = None
    confidence_floor: float | None = None
    note: str = ""


def _referenced_fields(cond: dict[str, Any]) -> list[str]:
    """收集條件樹（含 all/any 巢狀）內所有被引用的欄位名。"""
    if "all" in cond or "any" in cond:
        out: list[str] = []
        for child in cond.get("all", []) + cond.get("any", []):
            out.extend(_referenced_fields(child))
        return out
    return [cond["field"]] if "field" in cond else []


def _eval_cond(cond: dict[str, Any], values: dict[str, float]) -> bool:
    """求值（呼叫前已確保所有引用欄位存在）。"""
    if "all" in cond:
        return all(_eval_cond(c, values) for c in cond["all"])
    if "any" in cond:
        return any(_eval_cond(c, values) for c in cond["any"])
    op = _OPS[cond["op"]]
    return op(values[cond["field"]], cond["value"])


def evaluate_rules(rules: list[HardRule], values: dict[str, float]) -> list[RuleOutcome]:
    """rule pass：對每條規則求值，回傳 triggered / not_evaluable 結果清單。"""
    outcomes: list[RuleOutcome] = []
    for r in rules:
        out = RuleOutcome(
            rule_id=r.id, action=r.action,
            confidence_ceiling=r.confidence_ceiling,
            confidence_floor=r.confidence_floor, note=r.note,
        )
        missing = [f for f in _referenced_fields(r.if_) if f not in values]
        if missing:
            out.not_evaluable = True
        else:
            out.triggered = _eval_cond(r.if_, values)
        outcomes.append(out)
    return outcomes


def clamp_confidence(
    stance: str, confidence: float, outcomes: list[RuleOutcome]
) -> tuple[float, list[str]]:
    """clamp（第 4 段）：以觸發的硬規則收口 confidence，回傳 (clamped, rule_conflicts)。

    兩階段（issue #87）：先套所有同向 directional floor，再套所有 cap_confidence——
    cap 是**真正的硬上限**，最後套用、壓過任何同向 floor，淨值與 yaml 規則順序無關
    （語意：cap=「我不碰」的紅線勝過 floor=「我有興趣」的下限）。directional floor
    只在 stance 同向時套下限，反向記衝突；cap 壓過一條同向已觸發 floor 時也記衝突揭露。
    """
    conflicts: list[str] = []

    # 第 1 階段：directional floor（同向抬下限；反向不翻 stance，記衝突）
    applied_floors: list[RuleOutcome] = []
    for o in outcomes:
        if not o.triggered:
            continue
        if o.action == "bullish_floor" and o.confidence_floor is not None:
            if stance == "bullish":
                confidence = max(confidence, o.confidence_floor)
                applied_floors.append(o)
            else:
                conflicts.append(
                    f"{o.rule_id}: 規則傾向 bullish（{o.note or 'floor'}）但大師判 {stance}"
                )
        elif o.action == "bearish_floor" and o.confidence_floor is not None:
            if stance == "bearish":
                confidence = max(confidence, o.confidence_floor)
                applied_floors.append(o)
            else:
                conflicts.append(
                    f"{o.rule_id}: 規則傾向 bearish（{o.note or 'floor'}）但大師判 {stance}"
                )

    # 第 2 階段：cap_confidence 硬收上限（最後套；壓過同向 floor 時揭露衝突）
    for o in outcomes:
        if not o.triggered:
            continue
        if o.action == "cap_confidence" and o.confidence_ceiling is not None:
            for f in applied_floors:
                if o.confidence_ceiling < f.confidence_floor:
                    conflicts.append(
                        f"{o.rule_id}: 紅線上限 {o.confidence_ceiling}"
                        f"（{o.note or 'cap'}）壓過同向 {f.rule_id} floor {f.confidence_floor}"
                    )
            confidence = min(confidence, o.confidence_ceiling)

    return max(0.0, min(1.0, confidence)), conflicts


def rule_values(store) -> dict[str, float]:
    """把 EvidenceStore 攤平成 {canonical_field: value}（含 sage-private derived），供 DSL 求值。

    同名欄位取最後加入者（最新）。只收數值型；字串證據（新聞等）不參與 DSL。
    """
    values: dict[str, float] = {}
    for e in store.items:
        if isinstance(e.value, (int, float)):
            values[e.field] = float(e.value)
    return values
