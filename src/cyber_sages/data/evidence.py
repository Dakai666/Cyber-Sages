"""Evidence — 可溯源資料的最小單位。

資料層不回傳裸數字，回傳 Evidence；下游所有 claim 都引用 evidence id。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

Category = Literal[
    "quote", "fundamentals", "history", "news", "profile", "chips", "macro",
    "estimate", "reference", "derived"
]
# chips = 台股籌碼面（三大法人買賣超 / 融資融券）+ 美股 short interest
# macro = 總經（利率/通膨/殖利率曲線/就業），全市場共用、非個股
# estimate = 分析師前瞻共識（forward EPS / 目標價 / 成長率），二手聚合值，
#            標 (estimate) 來源、不做 freshness error、brief 與事實欄位視覺區隔
# reference = 外部產業/市場 benchmark（Damodaran 產業 multiples），二手年度聚合，
#             供相對估值比較；同 estimate 不做 freshness error、非必要類別
# derived = Sage Runtime 的 sage-private skill 輸出（owner_earnings 等），id 為 S-<key>-###、
#           帶公式與輸入 ids；只在該大師的 SOP pass + cite-check 視圖內可見，不入共享 digest


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

    def _scaled_hint(self) -> str:
        """大數值附上可讀量級，讓 LLM 直接引用正確刻度，不必心算 億/兆/B 而出現 10x 口誤。"""
        v = self.value
        if not isinstance(v, (int, float)) or abs(v) < 1e6:
            return ""
        a = abs(v)
        if self.unit and "TWD" in self.unit:        # 台股口徑用 億/兆
            if a >= 1e12:
                return f" ≈{v / 1e12:.4g}兆"
            if a >= 1e8:
                return f" ≈{v / 1e8:.4g}億"
            return ""
        if a >= 1e12:                                # 其餘用 M/B/T
            return f" ≈{v / 1e12:.4g}T"
        if a >= 1e9:
            return f" ≈{v / 1e9:.4g}B"
        return f" ≈{v / 1e6:.4g}M"

    def digest_line(self) -> str:
        as_of = f" as_of={self.as_of}" if self.as_of else ""
        unit = f" {self.unit}" if self.unit else ""
        return (f"[{self.id}] {self.field} = {self.value}{unit}{self._scaled_hint()} "
                f"(src: {self.source}{as_of})")


class EvidenceStore(BaseModel):
    ticker: str
    market: str = "US"         # "US" | "TW" …，審核與下游可據此調整口徑
    instrument: str = "stock"  # "stock" | "etf"，ETF 無個股財報，審核不要求基本面
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
