"""Evidence — 可溯源資料的最小單位。

資料層不回傳裸數字，回傳 Evidence；下游所有 claim 都引用 evidence id。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

Category = Literal[
    "quote", "fundamentals", "history", "news", "profile", "chips", "macro"
]
# chips = 台股籌碼面（三大法人買賣超 / 融資融券）
# macro = 總經（利率/通膨/殖利率曲線/就業），全市場共用、非個股


class Evidence(BaseModel):
    id: str = ""  # 由 EvidenceStore 編號（E001…）
    category: Category
    field: str                      # 例如 "close_price", "revenue_fy"
    value: float | int | str
    unit: str | None = None         # "USD", "%", "shares"…
    source: str                     # "yfinance", "SEC EDGAR", "finnhub"
    url: str | None = None
    as_of: date | None = None       # 資料本身的日期（收盤日、財報期末日）
    retrieved_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    note: str | None = None

    def digest_line(self) -> str:
        as_of = f" as_of={self.as_of}" if self.as_of else ""
        unit = f" {self.unit}" if self.unit else ""
        return f"[{self.id}] {self.field} = {self.value}{unit} (src: {self.source}{as_of})"


class EvidenceStore(BaseModel):
    ticker: str
    market: str = "US"  # "US" | "TW" …，審核與下游可據此調整口徑
    items: list[Evidence] = Field(default_factory=list)

    def add(self, ev: Evidence) -> Evidence:
        ev.id = f"E{len(self.items) + 1:03d}"
        self.items.append(ev)
        return ev

    def add_all(self, evs: list[Evidence]) -> list[Evidence]:
        return [self.add(e) for e in evs]

    def get(self, evidence_id: str) -> Evidence | None:
        return next((e for e in self.items if e.id == evidence_id), None)

    def by_category(self, category: Category) -> list[Evidence]:
        return [e for e in self.items if e.category == category]

    def digest(self, categories: list[Category] | None = None) -> str:
        """給 LLM prompt 用的緊湊文字版（含 id，方便引用）。"""
        items = self.items if categories is None else [
            e for e in self.items if e.category in categories
        ]
        return "\n".join(e.digest_line() for e in items)

    def to_json_file(self, path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.model_dump(mode="json"), f, ensure_ascii=False, indent=2)
