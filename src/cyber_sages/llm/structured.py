"""Structured-output helpers shared by both provider paths.

Anthropic 原生 path 用 output_config.format(json_schema)；
相容端點（MiniMax 等）path 用 prompt 內 schema + 解析 + Pydantic 驗證。
"""

from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

# Anthropic structured outputs 不支援的 JSON Schema 關鍵字（驗證仍由 Pydantic 在本地做）
_UNSUPPORTED_KEYS = {
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "pattern", "minItems", "maxItems",
}


def api_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic model → Anthropic structured-outputs 可接受的 JSON schema。"""

    def clean(node: Any) -> Any:
        if isinstance(node, dict):
            cleaned = {k: clean(v) for k, v in node.items() if k not in _UNSUPPORTED_KEYS}
            if cleaned.get("type") == "object":
                cleaned["additionalProperties"] = False
            return cleaned
        if isinstance(node, list):
            return [clean(v) for v in node]
        return node

    return clean(model.model_json_schema())


def schema_prompt(model: type[BaseModel]) -> str:
    """給不支援 json_schema output 的端點：把 schema 塞進指令。"""
    return (
        "\n\nRespond with a single JSON object only — no prose before or after, "
        "no markdown fences. It must conform exactly to this JSON Schema:\n"
        + json.dumps(model.model_json_schema(), ensure_ascii=False)
    )


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Any:
    """從 LLM 回應文字裡盡力撈出 JSON 物件。"""
    candidates: list[str] = []
    fenced = _FENCE_RE.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    candidates.append(text)
    last_err: Exception | None = None
    for cand in candidates:
        try:
            return json.loads(cand.strip())
        except json.JSONDecodeError as e:  # noqa: PERF203
            last_err = e
    raise ValueError(f"No parseable JSON in response: {last_err}")


def parse_into(model: type[T], text: str) -> T:
    return model.model_validate(extract_json(text))
