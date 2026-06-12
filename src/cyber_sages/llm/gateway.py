"""LLMGateway — 單一 anthropic SDK 介面，依角色路由到不同 provider/model。

Feature gating：cache_control / adaptive thinking / json_schema output
只在 provider 宣告支援時送出，相容端點（MiniMax 等）拿到的是乾淨的標準請求。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from anthropic import AsyncAnthropic
from pydantic import BaseModel, ValidationError

from cyber_sages.config import ProviderConfig, Settings
from cyber_sages.llm.structured import api_json_schema, parse_into, schema_prompt

T = TypeVar("T", bound=BaseModel)

# CLI 串流 callback：(role, text_delta)
TextCallback = Callable[[str, str], None] | Callable[[str, str], Awaitable[None]]


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    stop_reason: str | None = None  # "max_tokens" = 輸出被截斷（thinking 吃光預算）


# 輸出被截斷時逐次加大的 max_tokens 上限——避免無限制吃 rate limit / 成本。
_MAX_TOKENS_CEILING = 65536


class LLMGateway:
    def __init__(self, settings: Settings, on_text: TextCallback | None = None):
        self.settings = settings
        self.on_text = on_text
        self._clients: dict[str, AsyncAnthropic] = {}
        # 全域併發上限，避免平行 sage 打爆 rate limit
        self._sem = asyncio.Semaphore(8)

    def _client(self, provider_name: str, provider: ProviderConfig) -> AsyncAnthropic:
        if provider_name not in self._clients:
            if not provider.api_key:
                raise RuntimeError(
                    f"Missing API key: set {provider.api_key_env} in .env "
                    f"(provider '{provider_name}')"
                )
            self._clients[provider_name] = AsyncAnthropic(
                api_key=provider.api_key,
                base_url=provider.base_url,
                max_retries=3,
            )
        return self._clients[provider_name]

    async def _emit(self, role: str, delta: str) -> None:
        if self.on_text is None:
            return
        result = self.on_text(role, delta)
        if asyncio.iscoroutine(result):
            await result

    async def complete(
        self,
        role: str,
        *,
        system: str,
        prompt: str,
        schema: type[BaseModel] | None = None,
        max_tokens: int | None = None,
        cache_system: bool = False,
    ) -> LLMResult:
        """跑一次 completion；schema 給定時由呼叫端再用 parse() 取得物件。"""
        role_cfg = self.settings.roles[role]
        provider = self.settings.providers[role_cfg.provider]
        client = self._client(role_cfg.provider, provider)
        # 解析優先序：呼叫端明指 > 角色設定 > 全域預設
        max_tokens = max_tokens or role_cfg.max_tokens or self.settings.defaults.max_tokens

        kwargs: dict = {
            "model": role_cfg.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }

        use_native_schema = schema is not None and provider.has("json_schema_output")
        if schema is not None and not use_native_schema:
            system = system + schema_prompt(schema)

        if cache_system and provider.has("cache_control"):
            kwargs["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        else:
            kwargs["system"] = system

        if provider.has("adaptive_thinking"):
            kwargs["thinking"] = {"type": "adaptive"}

        if use_native_schema:
            kwargs["output_config"] = {
                "format": {"type": "json_schema", "schema": api_json_schema(schema)}
            }

        # 低階 raw event 串流：不用 SDK 的 snapshot 累加器——
        # 部分 Anthropic 相容端點（如 MiniMax M2.x 的強制 thinking block）
        # 事件序列會讓 accumulate_event 出現 IndexError。
        text_parts: list[str] = []
        model = role_cfg.model
        stop_reason: str | None = None
        input_tokens = output_tokens = cache_read = 0
        async with self._sem:
            stream = await client.messages.create(**kwargs, stream=True)
            async for event in stream:
                etype = getattr(event, "type", "")
                if etype == "message_start":
                    msg = event.message
                    model = msg.model or model
                    usage = getattr(msg, "usage", None)
                    if usage is not None:
                        input_tokens = getattr(usage, "input_tokens", 0) or 0
                        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
                elif etype == "content_block_delta":
                    delta = event.delta
                    if getattr(delta, "type", "") == "text_delta":
                        text_parts.append(delta.text)
                        await self._emit(role, delta.text)
                elif etype == "message_delta":
                    usage = getattr(event, "usage", None)
                    if usage is not None and getattr(usage, "output_tokens", None):
                        output_tokens = usage.output_tokens
                    delta = getattr(event, "delta", None)
                    if delta is not None:
                        stop_reason = getattr(delta, "stop_reason", None) or stop_reason

        return LLMResult(
            text="".join(text_parts),
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            stop_reason=stop_reason,
        )

    async def structured(
        self,
        role: str,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        max_tokens: int | None = None,
        cache_system: bool = False,
    ) -> T:
        """結構化輸出：解析失敗時重試。

        兩種失敗有別：(1) 輸出被截斷（thinking 模型吃光 token 預算，stop_reason=
        max_tokens）——不是模型講錯，是沒講完，下一輪「加大預算、原 prompt 不變」讓它把
        話講完；(2) 真正的 schema 不符——把錯誤回饋進 prompt 要求重答。混為一談會讓大師的
        推理被無謂丟棄。"""
        role_cfg = self.settings.roles[role]
        budget = max_tokens or role_cfg.max_tokens or self.settings.defaults.max_tokens
        attempts = self.settings.citation.max_rewrite_attempts + 1
        last_err: Exception | None = None
        current_prompt = prompt
        for _ in range(attempts):
            result = await self.complete(
                role,
                system=system,
                prompt=current_prompt,
                schema=schema,
                max_tokens=budget,
                cache_system=cache_system,
            )
            try:
                return parse_into(schema, result.text)
            except (ValidationError, ValueError) as e:
                last_err = e
                if result.stop_reason == "max_tokens" and budget < _MAX_TOKENS_CEILING:
                    # 截斷：保留原 prompt，加倍預算讓它把 JSON 寫完
                    budget = min(budget * 2, _MAX_TOKENS_CEILING)
                    current_prompt = prompt
                else:
                    current_prompt = (
                        f"{prompt}\n\nYour previous reply failed validation:\n{e}\n"
                        "Reply again with ONLY a valid JSON object matching the schema."
                    )
        raise RuntimeError(f"Structured output failed after {attempts} attempts: {last_err}")
