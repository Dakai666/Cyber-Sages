"""總經資料源：FRED（聯準會聖路易分行）。

總經是全市場共用的背景，不綁個股 → 獨立的 macro evidence 類別。
總經分析師據此歸隊；同一份證據也餵給大師們做跨域視角（利率/通膨/殖利率曲線/就業
如何牽動風險性資產）。

需要環境變數 FRED_API_KEY；缺 key 時 provider 回傳空 list（管線優雅降級）。
"""

from __future__ import annotations

import os
from datetime import date

import httpx

from cyber_sages.data.evidence import Evidence

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"

# series_id → (欄位名, 單位, 說明)
LEVEL_SERIES: list[tuple[str, str, str, str]] = [
    ("FEDFUNDS", "fed_funds_rate", "%", "聯邦資金利率（月）"),
    ("DGS10", "treasury_10y", "%", "10年期公債殖利率"),
    ("DGS2", "treasury_2y", "%", "2年期公債殖利率"),
    ("T10Y2Y", "yield_curve_10y_2y", "%", "10年減2年利差，負值=殖利率倒掛（衰退訊號）"),
    ("UNRATE", "unemployment_rate", "%", "失業率"),
]


class MacroProvider:
    market = "MACRO"

    @staticmethod
    def available() -> bool:
        return bool(os.environ.get("FRED_API_KEY"))

    async def get_macro(self) -> list[Evidence]:
        key = os.environ.get("FRED_API_KEY")
        if not key:
            return []
        async with httpx.AsyncClient(timeout=30) as client:
            evs: list[Evidence] = []
            for sid, field, unit, note in LEVEL_SERIES:
                obs = await self._latest_obs(client, sid, key, limit=2)
                if obs:
                    d, v = obs[0]
                    evs.append(self._ev(field, v, unit, sid, d, note))
            # CPI 年增率：需 13 個月觀測
            cpi = await self._latest_obs(client, "CPIAUCSL", key, limit=13)
            if len(cpi) >= 13 and cpi[12][1] > 0:
                yoy = (cpi[0][1] / cpi[12][1] - 1) * 100
                evs.append(self._ev("cpi_yoy_pct", round(yoy, 2), "%",
                                    "CPIAUCSL", cpi[0][0], "CPI 年增率（通膨）"))
            # 非農就業月增。FRED PAYEMS 以「千人」計，這裡換算成實際人數輸出，
            # 讓分析師直接引用（避免「172K vs 172」這種單位刻度落差被誤判）。
            pay = await self._latest_obs(client, "PAYEMS", key, limit=2)
            if len(pay) >= 2:
                evs.append(self._ev("nonfarm_payrolls_mom_change",
                                    round((pay[0][1] - pay[1][1]) * 1000), "persons",
                                    "PAYEMS", pay[0][0], "非農就業人數月增"))
            return evs

    @staticmethod
    async def _latest_obs(
        client: httpx.AsyncClient, series_id: str, key: str, *, limit: int
    ) -> list[tuple[date, float]]:
        """回傳最新在前的 (date, value)，自動跳過缺值（FRED 的 "."）。"""
        resp = await client.get(FRED_URL, params={
            "series_id": series_id, "api_key": key, "file_type": "json",
            "sort_order": "desc", "limit": limit + 5,
        })
        resp.raise_for_status()
        out: list[tuple[date, float]] = []
        for o in resp.json().get("observations", []):
            if o["value"] in (".", "", None):
                continue
            out.append((date.fromisoformat(o["date"]), float(o["value"])))
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def _ev(field: str, value: float, unit: str, series_id: str,
            as_of: date, note: str) -> Evidence:
        return Evidence(
            category="macro", field=field, value=value, unit=unit,
            source=f"FRED {series_id}",
            url=f"https://fred.stlouisfed.org/series/{series_id}",
            as_of=as_of, note=note,
        )
