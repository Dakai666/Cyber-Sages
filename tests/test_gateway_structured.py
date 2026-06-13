"""gateway.structured 的截斷重試：thinking 模型吃光預算被腰斬時，加大 max_tokens 重試
而非把大師的推理當成「講錯」丟棄。"""

from types import SimpleNamespace

import pytest

from cyber_sages.agents.schemas import SageSignal
from cyber_sages.llm.gateway import LLMGateway, LLMResult

_VALID = (
    '{"stance":"neutral","confidence":0.5,"thesis":"t",'
    '"what_would_change_my_mind":"w"}'
)


def _gateway(role_max=16384, default_max=8192, rewrites=2):
    settings = SimpleNamespace(
        roles={"sage": SimpleNamespace(max_tokens=role_max)},
        defaults=SimpleNamespace(max_tokens=default_max),
        citation=SimpleNamespace(max_rewrite_attempts=rewrites),
    )
    return LLMGateway(settings)  # type: ignore[arg-type]


async def test_truncation_escalates_budget_and_keeps_prompt():
    g = _gateway(role_max=16384)
    seen: list[tuple[int, str]] = []

    async def fake_complete(role, *, system, prompt, schema, max_tokens, cache_system,
                            cache_prefix=None):
        seen.append((max_tokens, prompt))
        # 前兩次被截斷（thinking 吃光預算），第三次講完
        text = _VALID if len(seen) == 3 else '{"thesis":"…(截斷'
        stop = None if len(seen) == 3 else "max_tokens"
        return LLMResult(text=text, model="m", input_tokens=0, output_tokens=0,
                         cache_read_tokens=0, stop_reason=stop)

    g.complete = fake_complete  # type: ignore[method-assign]
    out = await g.structured("sage", system="s", prompt="P", schema=SageSignal)

    assert isinstance(out, SageSignal)
    # 預算逐次加倍：16384 → 32768 → 65536
    assert [mt for mt, _ in seen] == [16384, 32768, 65536]
    # 截斷重試不該把錯誤回饋灌進 prompt——大師沒講錯，只是沒講完
    assert all(p == "P" for _, p in seen)


async def test_validation_error_feeds_error_back_not_escalate():
    g = _gateway(role_max=16384)
    seen: list[tuple[int, str]] = []

    async def fake_complete(role, *, system, prompt, schema, max_tokens, cache_system,
                            cache_prefix=None):
        seen.append((max_tokens, prompt))
        text = _VALID if len(seen) == 2 else '{"stance":"bananas"}'  # 合法 JSON 但 schema 不符
        return LLMResult(text=text, model="m", input_tokens=0, output_tokens=0,
                         cache_read_tokens=0, stop_reason="end_turn")

    g.complete = fake_complete  # type: ignore[method-assign]
    out = await g.structured("sage", system="s", prompt="P", schema=SageSignal)

    assert isinstance(out, SageSignal)
    assert [mt for mt, _ in seen] == [16384, 16384]   # 非截斷：預算不變
    assert seen[1][1] != "P"                          # 錯誤被回饋進 prompt 要求重答


async def test_budget_caps_at_ceiling():
    # 角色預算已接近上限，加倍會被夾在 65536
    g = _gateway(role_max=40000)
    seen: list[int] = []

    async def fake_complete(role, *, system, prompt, schema, max_tokens, cache_system,
                            cache_prefix=None):
        seen.append(max_tokens)
        return LLMResult(text="not json", model="m", input_tokens=0, output_tokens=0,
                         cache_read_tokens=0, stop_reason="max_tokens")

    g.complete = fake_complete  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="failed after"):
        await g.structured("sage", system="s", prompt="P", schema=SageSignal)
    assert seen == [40000, 65536, 65536]  # 夾頂後不再成長


async def test_truncation_at_ceiling_keeps_original_prompt():
    # 角色預算已超過 ceiling（=65536），預算不再成長；且模型每次都該收到原 prompt——
    # 它沒講錯，餵「your previous reply failed validation」只會誤導它以為自己講錯了。
    g = _gateway(role_max=70000)
    seen: list[tuple[int, str]] = []

    async def fake_complete(role, *, system, prompt, schema, max_tokens, cache_system,
                            cache_prefix=None):
        seen.append((max_tokens, prompt))
        return LLMResult(text="not json", model="m", input_tokens=0, output_tokens=0,
                         cache_read_tokens=0, stop_reason="max_tokens")

    g.complete = fake_complete  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="failed after"):
        await g.structured("sage", system="s", prompt="P", schema=SageSignal)
    # 預算已高於 ceiling，不再被加倍；prompt 從頭到尾都是原樣（沒被灌「你講錯了」訊息）
    assert [mt for mt, _ in seen] == [70000, 70000, 70000]
    assert all(p == "P" for _, p in seen)
