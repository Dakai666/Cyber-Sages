"""FinMind REST 客戶端 — 台股第一手資料源。

不另外裝 SDK，直接打 v4 REST（貼合本專案既有的 httpx 風格）。
token 由環境變數 FINMIND_TOKEN 提供；無 token 也能打但限流很緊。
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import httpx

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"


def _token() -> str | None:
    return os.environ.get("FINMIND_TOKEN")


async def finmind_get(
    client: httpx.AsyncClient,
    dataset: str,
    data_id: str | None = None,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict]:
    """打單一 dataset，回傳 data list（升冪）。非 200 或 FinMind 回報錯誤即 raise。"""
    params: dict[str, str] = {"dataset": dataset}
    if data_id is not None:
        params["data_id"] = data_id
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    tok = _token()
    if tok:
        params["token"] = tok

    resp = await client.get(FINMIND_URL, params=params)
    resp.raise_for_status()
    body = resp.json()
    if body.get("status") != 200:
        raise RuntimeError(f"FinMind {dataset} error: {body.get('msg')}")
    return body.get("data", [])


def days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()
