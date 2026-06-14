"""Persona Pack — 格式定義與 loader（Sage Runtime 的入口）。

Pack = 可執行的專家，目錄結構（Spec E）：

    personas/
      buffett-2019/            ← 目錄名 = <key>-<epoch>（時點鎖定）
        persona.yaml           # 身分 + weight_rationale + sources manifest
        rules.yaml             # 硬規則 DSL（hard_rules）+ 自然語言 exceptions
        sop.yaml               # 決策工作流：有序步驟，每步綁 evidence 欄位與 skill
        skills.py              # 選配：該大師專屬的確定性計算
      taleb.yaml               ← 舊單檔向後相容（degraded：無 skills/rules/SOP）

`load_personas` 同時認得目錄與單檔，漸進遷移；degraded persona 走 council 的原單發 prompt。

E1 實作決議 #4：目錄名帶 epoch、`persona.yaml` 有 `epoch` 欄位，但「同 key 取最新 epoch」
的多版本選取邏輯延後 E2——目前每個 key 預期單一 epoch。
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from cyber_sages.personas.rules import HardRule
from cyber_sages.personas.skill import Skill

PERSONA_DIR = Path(__file__).resolve().parent.parent / "personas"


class SopStep(BaseModel):
    """sop.yaml 的一個決策步驟。"""
    step: str
    ask: str
    look_at: list[str] = Field(default_factory=list)
    use_skill: str | None = None
    on_fail: str | None = None


@dataclass
class PersonaPack:
    """目錄格式 persona 的可執行部件（degraded 單檔 persona 此欄為 None）。"""
    hard_rules: list[HardRule] = field(default_factory=list)
    exceptions: list[str] = field(default_factory=list)
    sop: list[SopStep] = field(default_factory=list)
    skills: list[Skill] = field(default_factory=list)


class Persona(BaseModel):
    key: str
    name: str
    weight: float = 1.0
    philosophy: str
    focus: str
    voice: str
    # 適用 horizon（Spec C v2 P9）：未宣告預設兩者皆適用（向後相容——舊 persona 照常全程出席）；
    # 宣告後，run 的 horizon 不在此清單內則該大師 abstain（不出席、不投票，brief 揭露）。
    horizons: list[str] = Field(default_factory=lambda: ["trading", "value"])
    # Pack 專屬（degraded 單檔留預設）
    epoch: str | int | None = None
    weight_rationale: str = ""
    sources: list[str] = Field(default_factory=list)
    pack: PersonaPack | None = None

    model_config = {"arbitrary_types_allowed": True}

    @property
    def is_pack(self) -> bool:
        return self.pack is not None


def _load_skills(skills_path: Path, key: str) -> list[Skill]:
    """import 一個 Pack 的 skills.py，收集模組層級的所有 Skill 實例（@skill 產出）。"""
    spec = importlib.util.spec_from_file_location(f"cyber_sages._pack_skills.{key}", skills_path)
    if spec is None or spec.loader is None:
        return []
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return [v for v in vars(module).values() if isinstance(v, Skill)]


def _load_pack_dir(d: Path) -> Persona:
    """從 <key>-<epoch>/ 目錄載入一個 Pack persona。"""
    with open(d / "persona.yaml", encoding="utf-8") as f:
        identity = yaml.safe_load(f)

    hard_rules: list[HardRule] = []
    exceptions: list[str] = []
    rules_path = d / "rules.yaml"
    if rules_path.exists():
        with open(rules_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        hard_rules = [HardRule.model_validate(r) for r in (raw.get("hard_rules") or [])]
        exceptions = list(raw.get("exceptions") or [])

    sop: list[SopStep] = []
    sop_path = d / "sop.yaml"
    if sop_path.exists():
        with open(sop_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        sop = [SopStep.model_validate(s) for s in (raw.get("sop") or [])]

    skills: list[Skill] = []
    skills_path = d / "skills.py"
    if skills_path.exists():
        skills = _load_skills(skills_path, identity["key"])

    pack = PersonaPack(hard_rules=hard_rules, exceptions=exceptions, sop=sop, skills=skills)
    return Persona(**identity, pack=pack)


def _load_legacy(path: Path) -> Persona:
    with open(path, encoding="utf-8") as f:
        return Persona.model_validate(yaml.safe_load(f))


def load_personas(limit: int | None = None) -> list[Persona]:
    """載入所有 persona（目錄 Pack + 舊單檔），weight 高者優先（--sages N 截斷時）。"""
    personas: list[Persona] = []
    for entry in sorted(PERSONA_DIR.iterdir()):
        if entry.is_dir():
            if (entry / "persona.yaml").exists():  # 非 Pack 目錄（如 __pycache__）略過
                personas.append(_load_pack_dir(entry))
        elif entry.suffix == ".yaml":
            personas.append(_load_legacy(entry))
    personas.sort(key=lambda p: -p.weight)
    return personas[:limit] if limit else personas
