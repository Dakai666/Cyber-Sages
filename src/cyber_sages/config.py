"""Configuration loading: config.yaml + .env."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

ProviderFeature = Literal["cache_control", "adaptive_thinking", "json_schema_output"]


class ProviderConfig(BaseModel):
    base_url: str | None = None
    api_key_env: str
    features: list[ProviderFeature] = Field(default_factory=list)

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env)

    def has(self, feature: ProviderFeature) -> bool:
        return feature in self.features


class RoleConfig(BaseModel):
    provider: str
    model: str
    # 強制 thinking 的模型（如 MiniMax M2.x）會先吃掉 token 預算才吐 JSON，
    # 推理重的角色需更大上限避免輸出被腰斬。None = 用 defaults.max_tokens。
    max_tokens: int | None = None


class AuditConfig(BaseModel):
    max_price_divergence_pct: float = 2.0
    max_quote_age_days: int = 5
    max_fundamentals_age_days: int = 120
    max_macro_age_days: int = 45      # 總經多為月頻，一個月+緩衝
    # W2：yfinance 二手 trailing P/E 與 SEC 第一手年報 EPS 反算的 implied P/E 偏離上限。
    # 注意口徑差：yfinance trailing 用 TTM、implied 用最近年報（FY），高成長股年度<TTM
    # 會自然偏離，故閾值放寬到 25%（>此視為 yfinance 二手值可疑 → error）。
    # TODO(issue #19)：補算 US eps_ttm 做 TTM-vs-TTM 口徑對齊後，此閾值可收回嚴格 10%。
    max_pe_divergence_pct: float = 25.0
    # 單一財報欄位嚴重過時（如換 XBRL 標籤撈到舊值）即 error；年報自然落後 ~1 年，
    # 超過 ~16 個月視為異常。與 max_fundamentals_age_days（整類新鮮度）分開。
    max_fundamentals_stale_field_days: int = 500


class CitationConfig(BaseModel):
    numeric_tolerance_pct: float = 1.0
    max_rewrite_attempts: int = 2


class DefaultsConfig(BaseModel):
    sages: int = 10
    debate_rounds: int = 1
    max_tokens: int = 8192
    include_macro: bool = True        # 預設帶入 FRED 總經（有 key 才會真正抓）


class Settings(BaseModel):
    providers: dict[str, ProviderConfig]
    roles: dict[str, RoleConfig]
    audit: AuditConfig = AuditConfig()
    citation: CitationConfig = CitationConfig()
    defaults: DefaultsConfig = DefaultsConfig()

    def role(self, name: str) -> tuple[RoleConfig, ProviderConfig]:
        role = self.roles[name]
        return role, self.providers[role.provider]


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_settings(config_path: Path | None = None) -> Settings:
    load_dotenv(project_root() / ".env")
    load_dotenv()  # also honor CWD .env
    path = config_path or project_root() / "config.yaml"
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return Settings.model_validate(raw)
