"""FinMind REST 客戶端 + 共用 retry/backoff 的純單元測試（不打 API）。

用 httpx.MockTransport 假裝 FinMind 端點，驗證：解析、邏輯錯誤拋出、暫時性網路
失敗會退避重試後成功、永久性錯誤立即放棄。退避用的 jitter 被 patch 成 0，測試不真睡。
"""

from datetime import date, timedelta

import httpx
import pytest

from cyber_sages.data import retry as retry_mod
from cyber_sages.data.finmind import days_ago, finmind_get
from cyber_sages.data.retry import is_retryable, with_retry


@pytest.fixture(autouse=True)
def _no_jitter_sleep(monkeypatch):
    # full jitter 取 0 → asyncio.sleep(0)，測試零等待，邏輯不變
    monkeypatch.setattr(retry_mod.random, "uniform", lambda a, b: 0.0)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------- days_ago ----------

def test_days_ago():
    assert days_ago(0) == date.today().isoformat()
    assert days_ago(10) == (date.today() - timedelta(days=10)).isoformat()


# ---------- finmind_get 解析 / 錯誤 ----------

async def test_finmind_get_parses_data():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["dataset"] == "TaiwanStockPrice"
        assert request.url.params["data_id"] == "2330"
        return httpx.Response(200, json={"status": 200, "data": [{"close": 1075.0}]})

    async with _client(handler) as client:
        rows = await finmind_get(client, "TaiwanStockPrice", "2330", start_date="2024-01-01")
    assert rows == [{"close": 1075.0}]


async def test_finmind_get_logical_error_raises():
    # HTTP 200 但 body status != 200（如查無資料 / 配額）→ 邏輯錯誤，不重試直接拋。
    # 斷言 call count==1 鎖住「body 檢查在 with_retry 邊界之外」這個結構不變量：
    # 若未來有人把 body 檢查搬進 _fetch，這裡會抓到 silent retry（4xx 路徑的測試覆蓋不到）。
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"status": 402, "msg": "quota exceeded"})

    async with _client(handler) as client:
        with pytest.raises(RuntimeError, match="quota exceeded"):
            await finmind_get(client, "TaiwanStockPrice", "2330")
    assert calls["n"] == 1  # 單次呼叫即失敗，不重試


async def test_finmind_get_retries_transient_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("transient", request=request)
        return httpx.Response(200, json={"status": 200, "data": [{"ok": 1}]})

    async with _client(handler) as client:
        rows = await finmind_get(client, "TaiwanStockPrice", "2330")
    assert rows == [{"ok": 1}]
    assert calls["n"] == 3  # 失敗兩次、第三次成功


async def test_finmind_get_gives_up_after_max_attempts():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503)  # 持續 5xx

    async with _client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await finmind_get(client, "TaiwanStockPrice", "2330")
    assert calls["n"] == 3  # 預設 max_attempts=3


# ---------- with_retry 行為 ----------

def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.com")
    return httpx.HTTPStatusError("e", request=request,
                                 response=httpx.Response(status, request=request))


def test_is_retryable_classification():
    assert is_retryable(httpx.ConnectError("x"))
    assert is_retryable(httpx.ReadTimeout("x"))
    assert is_retryable(_http_error(503))
    assert is_retryable(_http_error(429))
    assert not is_retryable(_http_error(404))
    assert not is_retryable(ValueError("not network"))


async def test_with_retry_returns_on_first_success():
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        return "ok"

    assert await with_retry(fn, what="t") == "ok"
    assert calls["n"] == 1


async def test_with_retry_does_not_retry_permanent_error():
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        raise _http_error(404)

    with pytest.raises(httpx.HTTPStatusError):
        await with_retry(fn, what="t")
    assert calls["n"] == 1  # 4xx 永久性，不重試


async def test_with_retry_recovers_after_transient():
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("transient")
        return 42

    assert await with_retry(fn, what="t") == 42
    assert calls["n"] == 2
