"""Persona Pack — skill 框架（Sage Runtime 三段執行的第 1 段：skill pass）。

大師專屬的「獨特輕量技能」是**確定性計算**——絕不讓 LLM 算數（與整個 Cyber-Sages
反幻覺哲學一致）。每個 skill 宣告它需要哪些 canonical 欄位（`requires`），Runtime
在 LLM 之前對 EvidenceStore 求值，輸出登錄為 **sage-private derived evidence**：
帶公式與輸入 evidence ids、完全可溯源，並可被既有 cite-check 驗證。

設計（Spec E「E1 實作決議」#2）：
- private evidence 走獨立命名空間 `S-<key>-NNN`，不混入共享 `E001…` 序列。
- 缺 `requires` 欄位 → skill 記 `not_evaluable`（不觸發、不假裝算出值），同 rules 處理。
- skill 函式用 `ev["field"]` 取值，accessor 自動記錄碰到的 evidence id，省得手寫 E-id
  （E-id 不穩定、手寫易錯）；`SkillResult.inputs` 留空時由 Runtime 以實際讀取的 id 回填。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from cyber_sages.data.evidence import Evidence, EvidenceStore


@dataclass
class SkillResult:
    """一個 skill 的確定性輸出。inputs 留空則由 Runtime 以 accessor 實際讀到的 id 回填。"""
    value: float | int
    formula: str
    inputs: list[str] = field(default_factory=list)
    unit: str | None = None


@dataclass
class Skill:
    """`@skill(requires=...)` 產出的可呼叫物件。模組層級的 Skill 實例即該 Pack 的技能集。"""
    name: str
    requires: list[str]
    fn: Callable[["EvidenceAccessor"], SkillResult]


def skill(requires: list[str]) -> Callable[[Callable], Skill]:
    """裝飾器：把一個 `def f(ev) -> SkillResult` 註冊成 Skill（含 requires 欄位清單）。"""
    def deco(fn: Callable[["EvidenceAccessor"], SkillResult]) -> Skill:
        return Skill(name=fn.__name__, requires=list(requires), fn=fn)
    return deco


class EvidenceAccessor:
    """傳給 skill 函式的取值器：`ev["net_income_annual"]` 回值並記錄該 evidence 的 id。

    同名欄位若有多筆，取「最後加入」者（最新 quote/history 多半後寫；財報單筆）。
    skill 內只會用 requires 宣告過的欄位——但若取了未宣告或不存在的欄位則 KeyError，
    讓 Pack 作者早點發現拼錯（Runtime 已先以 requires 檢查過缺漏並降級）。
    """
    def __init__(self, store: EvidenceStore):
        self._by_field: dict[str, Evidence] = {}
        for e in store.items:
            if isinstance(e.value, (int, float)):
                self._by_field[e.field] = e  # 後者覆蓋前者 → 取最新
        self.touched_ids: list[str] = []

    def __getitem__(self, field_name: str) -> float:
        ev = self._by_field[field_name]
        if ev.id not in self.touched_ids:
            self.touched_ids.append(ev.id)
        return float(ev.value)

    def __contains__(self, field_name: str) -> bool:
        return field_name in self._by_field


def run_skills(
    skills: list[Skill], store: EvidenceStore, key: str
) -> tuple[list[Evidence], list[str]]:
    """執行一個 Pack 的所有 skills（skill pass）。

    回傳 (private_evidence, not_evaluable)：
    - private_evidence：每個成功 skill 一筆 Evidence，id = `S-<key>-NNN`、category="derived"、
      note 帶公式與輸入 ids，可被 cite-check 當一般 evidence 比對。
    - not_evaluable：缺 requires 欄位（或算出非有限值）而未執行的 skill 名清單。
    """
    private: list[Evidence] = []
    not_evaluable: list[str] = []
    for sk in skills:
        accessor = EvidenceAccessor(store)
        missing = [f for f in sk.requires if f not in accessor]
        if missing:
            not_evaluable.append(f"skill:{sk.name} (缺 {', '.join(missing)})")
            continue
        try:
            result = sk.fn(accessor)
        except (KeyError, ZeroDivisionError, ValueError) as exc:
            not_evaluable.append(f"skill:{sk.name} ({type(exc).__name__})")
            continue
        val = result.value
        if not isinstance(val, (int, float)) or val != val or val in (float("inf"), float("-inf")):
            not_evaluable.append(f"skill:{sk.name} (non-finite)")
            continue
        inputs = result.inputs or accessor.touched_ids
        # id 用 skill 名而非流水號（B4）：流水號會在某 skill not_evaluable 被跳過時產生
        # gap、且隨「哪些 skill 算得出」重排——sop_trace 引用的 id 會跨 run/配置漂移。
        # 以 skill 名為 id 則穩定、自我說明（一個 Pack 內 skill 名天生唯一）。
        ev = Evidence(
            id=f"S-{key}-{sk.name}",
            category="derived",
            field=sk.name,
            value=val,
            unit=result.unit,
            source=f"sage-skill:{key}",
            note=f"{result.formula} [inputs: {', '.join(inputs)}]",
        )
        private.append(ev)
    return private, not_evaluable
