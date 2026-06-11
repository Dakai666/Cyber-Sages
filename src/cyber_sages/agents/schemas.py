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


class FinalVerdict(BaseModel):
    stance: Stance
    conviction: float = Field(description="0 to 1 after risk adjustment")
    thesis: str = Field(description="Traditional Chinese, the synthesized view")
    supporting_points: list[str] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)
    what_would_change_my_mind: str = ""
    dissent_summary: str = Field(default="", description="Honest summary of minority view")
