"""台灣專屬總經源：中央銀行統計資料庫 API（cpx.cbc.gov.tw）。

FRED 只穩定提供 TWD/USD FX；台灣國內政策利率不在 FRED 標準系列（IMF/OECD 的台灣序列
稀疏、季度或已停更）。央行統計 API 以「項目代號」回 JSON、無需金鑰：
  GET https://cpx.cbc.gov.tw/API/DataAPI/Get?FileName=<項目代號>
回應含 `meta`（標題/單位/last_updated）與 `data`（`dataSets` 逐列 [期別, 欄1, 欄2…]
+ `structure.Table1` 欄位名）。EG2AM01＝中央銀行利率（月，期底值），首欄即**重貼現率**
——台灣貨幣政策基準、台股最受關注的利率槓桿（補上 FRED 只有美國 fed funds 的缺口）。

CPI 年增率刻意未納：DGBAS 無乾淨即時 JSON 源（政府開放資料為年度 XML、物價統計資料庫
為 PXWeb POST 查詢、FRED 台灣 CPI 季度且已停更），硬接任一者都違反「資料正確」鐵律，
留待 follow-up 以專屬 PXWeb client 處理（見 issue #68）。
"""

from __future__ import annotations

import calendar
import logging
from datetime import date

import httpx

from cyber_sages.data.evidence import Evidence
from cyber_sages.data.retry import with_retry

logger = logging.getLogger(__name__)

CBC_API = "https://cpx.cbc.gov.tw/API/DataAPI/Get"
# 項目代號 → (欄位名（structure.Table1 內）, evidence field, note)。
# 列表化以便日後擴充（如加金融業隔夜拆款利率）；目前聚焦政策利率。
_DISCOUNT_RATE_FILE = "EG2AM01"
_DISCOUNT_RATE_COLUMN = "重貼現率"


def _period_to_date(period: str) -> date | None:
    """央行月期別 "YYYYMmm"（如 "2026M04"）→ 期底日（月底，因利率為期底值）。"""
    if len(period) != 7 or period[4] != "M":
        return None
    try:
        year, month = int(period[:4]), int(period[5:7])
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, last_day)
    except ValueError:
        return None


class TWMacroProvider:
    """中央銀行統計資料庫實作的 `MacroProvider`（見 data/base.py）。台灣專屬，僅在 TW
    市場併入（見 make_macro_provider）。無需金鑰，故 available 恆真——抓取失敗在
    get_macro 內優雅降級為 []（與 FRED 缺 key 回 [] 同語意）。"""

    market = "MACRO"

    @staticmethod
    def available() -> bool:
        return True  # 央行 API 公開、無需金鑰

    async def get_macro(self) -> list[Evidence]:
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                async def _fetch() -> httpx.Response:
                    resp = await client.get(CBC_API, params={"FileName": _DISCOUNT_RATE_FILE})
                    resp.raise_for_status()
                    return resp

                resp = await with_retry(_fetch, what=f"CBC {_DISCOUNT_RATE_FILE}")
                ev = self._parse_discount_rate(resp.json())
                return [ev] if ev else []
            except Exception as e:  # best-effort：央行 API 掛掉不該炸整條 collect
                logger.warning("TW macro (CBC %s) fetch failed: %s", _DISCOUNT_RATE_FILE, e)
                return []

    @staticmethod
    def _parse_discount_rate(payload: dict) -> Evidence | None:
        """央行 EG2AM01 JSON → 重貼現率 evidence（取最新一筆非空）。抽純函式以便不打網路
        測試（同 us_stocks `_facts_to_evidence` 範式）。結構：
          data.dataSets = [[期別, 重貼現率, 擔保放款融通利率, 短期融通], …]（時間升冪）
          data.structure.Table1 = [{"data":"重貼現率"}, …]  ← 欄位名，定位欄序不靠硬編。"""
        data = payload.get("data") or {}
        rows = data.get("dataSets") or []
        cols = [c.get("data") for c in (data.get("structure") or {}).get("Table1", [])]
        if not rows or _DISCOUNT_RATE_COLUMN not in cols:
            return None
        # row[0]=期別，故資料欄在 row 中的位置 = 欄序 + 1。
        value_pos = cols.index(_DISCOUNT_RATE_COLUMN) + 1
        for row in reversed(rows):  # 由新到舊，取第一筆有效值
            if len(row) <= value_pos:
                continue
            raw = row[value_pos]
            if raw in (None, "", "-", "...", "."):
                continue
            as_of = _period_to_date(row[0])
            try:
                value = round(float(raw), 3)
            except (TypeError, ValueError):
                continue
            return Evidence(
                category="macro", field="tw_discount_rate", value=value, unit="%",
                source=f"CBC {_DISCOUNT_RATE_FILE}（央行重貼現率）",
                url=f"{CBC_API}?FileName={_DISCOUNT_RATE_FILE}",
                as_of=as_of,
                note="中央銀行重貼現率（台灣貨幣政策基準利率，期底值；升=緊縮、抑台股估值）",
            )
        return None
