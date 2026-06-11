"""Pipeline 各階段的結構化輸出 schema。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from cyber_sages.verify.citation_check import Claim

Stance = Literal["bullish", "bearish", "neutral"]


class AnalystReport(BaseModel):
    analyst: str = ""
    summary: str = Field(description="2-4 sentence summary in Traditional Chinese")
    outlook: Stance
    claims: list[Claim] = Field(
        description="Each key finding as a claim citing evidence ids"
    )
    unverified_claims: list[str] = Field(default_factory=list)  # cite-check 後標記


class SageSignal(BaseModel):
    sage: str = ""
    stance: Stance
    confidence: float = Field(description="0 to 1")
    thesis: str = Field(description="3-5 sentences in Traditional Chinese, in this sage's voice")
    key_evidence_ids: list[str] = Field(default_factory=list)
    what_would_change_my_mind: str


class CouncilVerdict(BaseModel):
    signals: list[SageSignal]
    bullish: int = 0
    bearish: int = 0
    neutral: int = 0
    weighted_score: float = 0.0  # -1(全空) .. +1(全多)
    consensus: Stance = "neutral"
    outliers: list[str] = Field(default_factory=list)  # 與多數不同調的 sage 名單


class DebateArgument(BaseModel):
    side: Literal["bull", "bear"] = "bull"
    argument: str = Field(description="Traditional Chinese, cite evidence ids inline")


class DebateVerdict(BaseModel):
    winner: Literal["bull", "bear", "draw"]
    rationale: str
    strongest_bull_point: str
    strongest_bear_point: str
    unresolved_risks: list[str] = Field(default_factory=list)


class RiskNote(BaseModel):
    concerns: list[str] = Field(default_factory=list)
    data_quality_acceptable: bool
    conviction_adjustment: float = Field(
        description="-0.3 to 0, subtract from chief's conviction for unpriced risks", default=0.0
    )


class PriceLevel(BaseModel):
    price: float
    label: str = Field(description="e.g. 分批買進下緣 / 停損 / 第一目標")
    basis: str = Field(description="Anchor in evidence, e.g. 'SMA200 265.48 [E017] 下方約3%'")
    evidence_ids: list[str] = Field(default_factory=list)


class HorizonView(BaseModel):
    horizon: Literal["short", "mid", "long"]  # 1-4週 / 1-6月 / 6月以上
    stance: Stance
    summary: str = Field(description="One sentence, Traditional Chinese")


Action = Literal["buy_now", "buy_dip", "hold", "reduce", "avoid", "sell"]


class ActionPlan(BaseModel):
    action: Action
    directive: str = Field(
        description="One imperative sentence answering 現在該怎麼做, Traditional Chinese"
    )
    entry_zone: list[PriceLevel] = Field(default_factory=list)
    stop_loss: PriceLevel | None = None
    targets: list[PriceLevel] = Field(default_factory=list)
    position_hint: str = Field(
        default="", description="Sizing guidance tied to conviction, e.g. 試單1/4倉、分3批"
    )
    invalidation: str = Field(
        default="", description="What makes this entire plan void (not just stop loss)"
    )


class FinalVerdict(BaseModel):
    stance: Stance
    conviction: float = Field(description="0 to 1 after risk adjustment")
    action_plan: ActionPlan
    horizons: list[HorizonView] = Field(
        default_factory=list, description="Exactly three: short, mid, long"
    )
    thesis: str = Field(description="Traditional Chinese, the synthesized view")
    supporting_points: list[str] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)
    what_would_change_my_mind: str = ""
    dissent_summary: str = Field(default="", description="Honest summary of minority view")
