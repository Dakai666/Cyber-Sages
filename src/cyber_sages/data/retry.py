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


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    what: str,
    max_attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
) -> T:
    """呼叫 async `fn`；暫時性失敗以指數退避 + full jitter 重試。

    `max_attempts` 次（含首發）都失敗才把最後一次的例外往外拋；非暫時性例外立即拋出。
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001 — 由 is_retryable 把關該不該吞
            if attempt >= max_attempts or not is_retryable(exc):
                raise
            # full jitter：退避上限隨次數翻倍，實際睡眠在 [0, cap] 間隨機，削尖同時重試的峰值
            cap = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay = random.uniform(0, cap)
            logger.warning(
                "%s failed (attempt %d/%d): %s; retrying in %.2fs",
                what, attempt, max_attempts, exc, delay,
            )
            await asyncio.sleep(delay)
