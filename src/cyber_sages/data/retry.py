"""統一的重試 / 退避——所有外部資料源的網路呼叫共用（W6）。

高流量時段（盤中、財報季）單次 transient 失敗就讓整個 run 掛掉並不合理。這裡提供
一個極簡的 async 重試包裝：只對「暫時性」錯誤退避重試，永久性錯誤（4xx 除 429、
邏輯錯誤）立即拋出，不浪費配額硬打。

刻意不分層 budget（見 Spec A 決議 6）：FinMind 配額壓力靠縮窗解決，這裡統一 3 次
指數退避 + full jitter 即可。
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")

# 只重試暫時性錯誤：連線 / 逾時類，加上 HTTP 429（限流）與 5xx（伺服器端）。
# 其餘 4xx 多為永久性（找不到、參數錯），重試只是浪費配額。
# 註：TimeoutException 本就是 TransportError 子類，isinstance 上冗餘；明列是當文件用，
# 讓讀者一眼看到「逾時也算 transient」。
_RETRYABLE_TRANSPORT = (httpx.TransportError, httpx.TimeoutException)


def is_retryable(exc: Exception) -> bool:
    if isinstance(exc, _RETRYABLE_TRANSPORT):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    return False


def get_retry_after(exc: Exception) -> float | None:
    """從 HTTP 回應的 `Retry-After` header 解析 server 要求的等待秒數（#17，禮貌用戶）。

    支援兩種格式：純秒數（`Retry-After: 2`）與 HTTP-date（`Retry-After: Wed, 21 Oct ...`）。
    EDGAR 對 429、FRED 偶發 503 會回此 header；尊重它可避免過早重試又踩限流。
    無 header / 非 HTTPStatusError / 解析失敗則回 None（退回預設指數退避）。
    """
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    raw = exc.response.headers.get("Retry-After")
    if not raw:
        return None
    raw = raw.strip()
    if raw.isdigit():
        return float(raw)
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    what: str,
    max_attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    retry_after_cap: float = 60.0,
) -> T:
    """呼叫 async `fn`；暫時性失敗以指數退避 + full jitter 重試。

    `max_attempts` 次（含首發）都失敗才把最後一次的例外往外拋；非暫時性例外立即拋出。
    若 server 回 `Retry-After`（#17），改用其指定秒數（夾在 `retry_after_cap` 內防惡意大值）。
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001 — 由 is_retryable 把關該不該吞
            if attempt >= max_attempts or not is_retryable(exc):
                raise
            retry_after = get_retry_after(exc)
            if retry_after is not None:
                # server 明示等待：尊重它（睡得比它短只會再撞 429），但夾上限防惡意/誤填大值
                delay = min(retry_after, retry_after_cap)
            else:
                # full jitter：退避上限隨次數翻倍，實際睡眠在 [0, cap] 間隨機，削尖重試峰值
                cap = min(max_delay, base_delay * (2 ** (attempt - 1)))
                delay = random.uniform(0, cap)
            logger.warning(
                "%s failed (attempt %d/%d): %s; retrying in %.2fs",
                what, attempt, max_attempts, exc, delay,
            )
            await asyncio.sleep(delay)


async def to_thread_with_timeout(
    fn: Callable[[], T], *, what: str, timeout: float = 20.0, default: T | None = None,
) -> T | None:
    """在執行緒跑同步阻塞函式（如 yfinance），逾時則放棄等待並 graceful 降級（#22）。

    yfinance 是同步 requests 函式庫、無 timeout，被限流或網路不穩時會卡死對應的
    `get_*`，拖垮整個 pipeline。此包裝給它一個逾時上限，逾時回 `default`（呼叫端多為
    `[]`），缺值由 audit completeness 閘門接手降級——寧可少一塊資料，不要整條卡住。

    注意：底層執行緒無法被取消，逾時後它仍會在背景跑完再被丟棄，但 pipeline 不再等它。
    """
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn), timeout)
    except asyncio.TimeoutError:
        logger.warning("%s timed out after %.0fs; degrading to default", what, timeout)
        return default
