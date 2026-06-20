"""台灣專屬總經源：中央銀行（利率）＋ 主計總處（CPI）。FRED 只穩定提供 TWD/USD FX；
台灣國內政策利率/通膨不在 FRED 標準系列（IMF/OECD 的台灣序列稀疏、季度或已停更），
故另接本土第一手源。兩個 provider 皆 TW 專屬，僅在 TW 市場併入（見 make_macro_provider）。

`TWMacroProvider`（央行重貼現率）——央行統計 API 以「項目代號」回 JSON、無需金鑰：
  GET https://cpx.cbc.gov.tw/API/DataAPI/Get?FileName=<項目代號>
回應含 `meta`（標題/單位/last_updated）與 `data`（`dataSets` 逐列 [期別, 欄1, 欄2…]
+ `structure.Table1` 欄位名）。EG2AM01＝中央銀行利率（月，期底值），首欄即**重貼現率**
——台灣貨幣政策基準、台股最受關注的利率槓桿（補上 FRED 只有美國 fed funds 的缺口）。

`TWCpiProvider`（主計總處 CPI 年增率）——data.gov.tw dataset 6019「消費者物價基本分類
指數」的官方 XML（無需金鑰、**每月**原地更新；issue #68 原誤判為「年度 XML」，實為月頻、
含官方公布之 `年增率(%)`）。`總指數` 區塊位於檔首，故 streaming 早停只下載 ~190KB（全檔
含數百子分類達 ~15MB）。直接採主計總處公布的官方年增率（對得上新聞標題，無方法學漂移）。
"""

from __future__ import annotations

import calendar
import logging
import re
import ssl
from datetime import date
from functools import lru_cache
from pathlib import Path

import certifi
import httpx

from cyber_sages.data.evidence import Evidence
from cyber_sages.data.retry import with_retry

logger = logging.getLogger(__name__)

CBC_API = "https://cpx.cbc.gov.tw/API/DataAPI/Get"
# 項目代號 → (欄位名（structure.Table1 內）, evidence field, note)。
# 列表化以便日後擴充（如加金融業隔夜拆款利率）；目前聚焦政策利率。
_DISCOUNT_RATE_FILE = "EG2AM01"
_DISCOUNT_RATE_COLUMN = "重貼現率"

# 主計總處 CPI（data.gov.tw dataset 6019 的官方 XML，catalog 登記之 canonical 連結，月更）。
DGBAS_CPI_URL = "https://ws.dgbas.gov.tw/001/Upload/461/relfile/11525/230555/pr0101a1m.xml"
# DGBAS 伺服器漏送中介憑證（見 certs/twca_secure_ssl_ca.pem 內說明）；併入 certifi 信任庫
# 補全鏈、維持完整 TLS 驗證（不關 verify）。lru_cache：context 建一次重用。
_DGBAS_INTERMEDIATE = Path(__file__).parent / "certs" / "twca_secure_ssl_ca.pem"


@lru_cache(maxsize=1)
def _dgbas_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.load_verify_locations(cafile=str(_DGBAS_INTERMEDIATE))
    return ctx
_CPI_TOTAL_PREFIX = "總指數"   # 表頭「總指數(指數基期：民國110年=100)」，位於檔首，子分類在其後
_CPI_YOY_TYPE = "年增率(%)"    # 每月每項皆有「原始值」「年增率(%)」兩列；通膨取後者官方公布值
# 每筆觀測：<Obs><Item>..</Item><TIME_PERIOD>..</TIME_PERIOD><FREQ>..</FREQ><TYPE>..</TYPE>
# <Item_VALUE>..</Item_VALUE></Obs>。Item_VALUE 可能為空（如最早年份無年增率）。
_OBS_RE = re.compile(
    r"<Item>(?P<item>[^<]*)</Item>.*?"
    r"<TIME_PERIOD>(?P<period>[^<]*)</TIME_PERIOD>.*?"
    r"<TYPE>(?P<type>[^<]*)</TYPE>.*?"
    r"<Item_VALUE>(?P<value>[^<]*)</Item_VALUE>",
    re.DOTALL,
)


def _extract_obs(text: str) -> list[dict]:
    """從 XML 文字片段抽出所有完整 `<Obs>` 為 dict（純函式、便於離線測試與 streaming 早停）。"""
    return [
        {"item": m["item"].strip(), "period": m["period"].strip(),
         "type": m["type"].strip(), "value": m["value"].strip()}
        for m in _OBS_RE.finditer(text)
    ]


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


class TWCpiProvider:
    """主計總處 CPI 年增率的 `MacroProvider`（見 data/base.py）。台灣專屬，僅 TW 市場併入。
    無需金鑰故 available 恆真——抓取失敗在 get_macro 內優雅降級為 []（與央行/FRED 同語意）。"""

    market = "MACRO"

    @staticmethod
    def available() -> bool:
        return True  # data.gov.tw 開放資料、無需金鑰

    async def get_macro(self) -> list[Evidence]:
        try:
            obs = await with_retry(self._fetch_total_index_obs, what="DGBAS PR0101A1M")
            ev = self._parse_cpi_yoy(obs)
            return [ev] if ev else []
        except Exception as e:  # best-effort：DGBAS 掛掉不該炸整條 collect
            logger.warning("TW macro (DGBAS CPI) fetch failed: %s", e)
            return []

    @staticmethod
    async def _fetch_total_index_obs() -> list[dict]:
        """Streaming 抓 PR0101A1M，只收 `總指數` 區塊就早停——總指數在檔首，省下其後數百
        子分類（~15MB）的傳輸。逐塊累積、以最後一個完整 `</Obs>` 切齊，避免跨 chunk 截斷；
        一旦在收過總指數後遇到不同 Item 即收工。位置容錯：若總指數非首塊，會讀到它為止才早停。"""
        collected: list[dict] = []
        seen_total = False
        pending = ""
        async with httpx.AsyncClient(timeout=60, verify=_dgbas_ssl_context()) as client:
            async with client.stream("GET", DGBAS_CPI_URL) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_text():
                    pending += chunk
                    cut = pending.rfind("</Obs>")
                    if cut == -1:
                        continue
                    complete, pending = pending[: cut + 6], pending[cut + 6:]
                    for rec in _extract_obs(complete):
                        if rec["item"].startswith(_CPI_TOTAL_PREFIX):
                            seen_total = True
                            collected.append(rec)
                        elif seen_total:
                            return collected  # 總指數區塊結束，早停（離開 stream→關連線）
        # EOF 前未遇到他塊（如總指數恰為最後一塊）：補收殘餘 pending 內的總指數列
        collected.extend(
            rec for rec in _extract_obs(pending)
            if rec["item"].startswith(_CPI_TOTAL_PREFIX)
        )
        return collected

    @staticmethod
    def _parse_cpi_yoy(obs: list[dict]) -> Evidence | None:
        """總指數 obs → 最新一筆有效「年增率(%)」evidence（純函式，便於不打網路測試）。
        以 as_of 最大者為最新（容忍非嚴格時間序），跳過空值/壞期別/非數字。"""
        latest: tuple[date, float] | None = None
        for rec in obs:
            if rec["type"] != _CPI_YOY_TYPE or not rec["value"]:
                continue
            as_of = _period_to_date(rec["period"])
            if as_of is None:
                continue
            try:
                value = round(float(rec["value"]), 2)
            except (TypeError, ValueError):
                continue
            if latest is None or as_of > latest[0]:
                latest = (as_of, value)
        if latest is None:
            return None
        as_of, value = latest
        return Evidence(
            category="macro", field="tw_cpi_yoy_pct", value=value, unit="%",
            source="DGBAS PR0101A1M（主計總處消費者物價總指數）",
            url=DGBAS_CPI_URL, as_of=as_of,
            note="消費者物價指數年增率（台灣通膨；主計總處官方公布值，升=通膨壓力、"
                 "牽動央行利率與台股估值）",
        )
