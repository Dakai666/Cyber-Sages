"""Pipeline 各階段的結構化輸出 schema。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from cyber_sages.verify.citation_check import Claim, FailureKind

Stance = Literal["bullish", "bearish", "neutral"]

# P7（Spec C v2 C-6）：neutral 不是單一狀態——分三種，tally 分開計數、brief 分開呈現。
# 與 horizon abstain 嚴格區分：abstain＝「這題不在我的 horizon」（persona 級，不計入 council）；
# neutral＝「在我 horizon 內、我出席投票了，但我判中性」。三類：
# - out_of_circle：在我 horizon 內但不在我的能力圈/方法適用範圍（信心天生低）
# - insufficient_signal：資料/證據不足以形成方向判斷（缺關鍵欄位、not_evaluable 過多）
# - balanced_forces：多空力量我看是真的勢均力敵（有方向性證據，但相互抵銷）
NeutralReason = Literal["out_of_circle", "insufficient_signal", "balanced_forces"]

# 分析時間框架（Spec C v2 P9）：trading＝數天~數週（技術/籌碼/動能為主）、
# value＝數年（多年基本面/護城河/估值為主）。大師在 persona 宣告適用 horizon，越圍 abstain。
Horizon = Literal["trading", "value"]


class UnverifiedClaim(BaseModel):
    """cite-check 後仍未通過的 claim，保留 evidence id 與原因供 brief 直接呈現。

    kind 由驗證層（citation_check.FailureKind）權威給定，不在此用 reason 子串猜。
    """
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str = ""
    kind: FailureKind = "num_mismatch"

    @property
    def tag(self) -> str:
        # 讓讀者一眼分辨：壞引用 / 無引用 / 數字對不上
        return {"bad_ref": "BAD_REF", "no_cite": "NO_CITE",
                "num_mismatch": "NUM_MISMATCH", "ok": "OK"}[self.kind]

    def as_line(self) -> str:
        """給 LLM prompt 用的精簡單行（council/synthesis）；brief 另有 rich 渲染。"""
        return f"{self.text} ({self.reason})"


class AnalystReport(BaseModel):
    """P8（Spec C v2）：analyst 是**數據函式**，不是意見方——只核實、標註、結構化證據，
    不下 bullish/bearish 立場（方向性判斷由 sage council 獨佔）。故無 `outlook` 欄位。"""
    analyst: str = ""
    summary: str = Field(
        description="2-4 sentence NEUTRAL synthesis of what the evidence shows in "
        "Traditional Chinese — facts, inflections, levels. NO buy/sell/bullish/bearish verdict."
    )
    claims: list[Claim] = Field(
        description="Each key finding as a neutral, evidence-cited claim (a fact, not a call)"
    )
    unverified: list[UnverifiedClaim] = Field(default_factory=list)  # cite-check 後標記


class SopStepResult(BaseModel):
    """SOP pass 中大師逐步作答的單步紀錄——步驟名 + 結論 + 引用 evidence id。

    每步的結論文字裡的數字會過 cite-check（軟揭露：對不上標 unverified，不 refuse）。
    """
    step: str = Field(description="The SOP step id this conclusion answers")
    conclusion: str = Field(description="This step's finding in Traditional Chinese, in voice")
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="Evidence ids (shared E### or this sage's private S-<key>-###) anchoring it",
    )


class SageSignal(BaseModel):
    sage: str = ""
    stance: Stance
    confidence: float = Field(description="0 to 1")
    thesis: str = Field(description="3-5 sentences in Traditional Chinese, in this sage's voice")
    key_evidence_ids: list[str] = Field(default_factory=list)
    what_would_change_my_mind: str
    # P7：stance=neutral 時必填，說明「為何中性」（三類見 NeutralReason）；非中性時忽略。
    # LLM 填；若 neutral 卻漏填，council 程式回填 insufficient_signal（保守、不假裝有方向）。
    neutral_reason: NeutralReason | None = Field(
        default=None, description="If stance is neutral, WHY: out_of_circle / "
        "insufficient_signal / balanced_forces"
    )
    # 以下為 Persona Pack（Sage Runtime）專屬；degraded 舊單檔 persona 留空。
    # sop_trace 由 SOP pass 的 LLM 填；其餘三項由 Runtime 程式回填（LLM 不可信，程式收口）。
    sop_trace: list[SopStepResult] = Field(
        default_factory=list, description="Per-step reasoning trace following this sage's SOP"
    )
    not_evaluable: list[str] = Field(
        default_factory=list,
        description="Rules/skills this sage normally applies but couldn't (missing fields)",
    )
    rule_conflicts: list[str] = Field(
        default_factory=list,
        description="Hard-rule directional floors that disagreed with the LLM's stance",
    )
    unverified: list[UnverifiedClaim] = Field(
        default_factory=list, description="sop_trace steps whose numbers failed cite-check"
    )


class ScoutSignal(BaseModel):
    """P2 第一輪 scout 的輕量輸出——便宜模型快速粗判，不做深度 SOP（決議 4）。

    全員先出 scout，再只對「共識代表 + 離群代表」深入；scout 結果同時用於選代表與（未被選中
    深入者的）最終投票，故省去 N−代表 位的昂貴深度推理。
    """
    stance: Stance
    confidence: float = Field(description="0 to 1, a quick gut read")
    one_liner: str = Field(description="One-sentence rationale in Traditional Chinese")
    neutral_reason: NeutralReason | None = Field(
        default=None, description="If neutral: out_of_circle / insufficient_signal / balanced_forces")


class CouncilVerdict(BaseModel):
    signals: list[SageSignal]
    bullish: int = 0
    bearish: int = 0
    neutral: int = 0
    weighted_score: float = 0.0  # -1(全空) .. +1(全多)
    consensus: Stance = "neutral"
    outliers: list[str] = Field(default_factory=list)  # 與多數不同調的 sage 名單
    # P7：neutral 三類各自計數（out_of_circle / insufficient_signal / balanced_forces），
    # 加總 == neutral。brief 分開呈現，讓判讀者區分「沒人懂」與「真的勢均力敵」。
    neutral_by_reason: dict[str, int] = Field(default_factory=dict)
    # fail-loud：被席但即便加大預算重試後 structured output 仍失敗而缺席的大師。
    # 正常情況應為空——gateway 偵測截斷會自動加大 token 重試；非空代表硬錯誤，須揭露。
    absent: list[str] = Field(default_factory=list)
    # P9：本次 horizon 不在其適用範圍而不出席的大師（persona 級 not_evaluable）。
    # 與 absent 不同——這是設計上的誠實退場（Buffett 不答當沖），須在 brief 揭露。
    abstained: list[str] = Field(default_factory=list)
    # P2：只過 scout 粗判、未獲選深入的大師名單。其訊號仍計入投票（不丟票），但無深度
    # thesis/sop_trace。空＝單階段（全員深入，房間 ≤ deep 預算時）。brief 揭露「深入 N / 速覽 M」。
    scouted_only: list[str] = Field(default_factory=list)


class DebateArgument(BaseModel):
    side: Literal["bull", "bear"] = "bull"
    # P3 雙盲：argument＝第一輪「盲打」開場（未見對手論點）。
    argument: str = Field(description="Round-1 blind opening; Traditional Chinese, cite evidence ids")
    # 第二輪反駁由程式從 RebuttalArgument 回填（不由 LLM 直接填本欄）——見 RebuttalArgument 註。
    rebuttal: str = Field(
        default="", description="Round-2 rebuttal after seeing opponent's opening; 繁體中文"
    )


class RebuttalArgument(BaseModel):
    """P3 第二輪反駁的**單欄純文字**輸出（review A）。

    第二輪刻意不重用 DebateArgument：雙欄（argument/rebuttal）會讓 LLM 在「系統 prompt 說建立
    己方論點、使用者 prompt 說反駁對手」方向不一致時，有非零機率把反駁塞進 argument 留 rebuttal
    空、或反之——下游取錯欄即 silent failure（反駁段消失）。單欄 schema 杜絕塞錯欄。
    """
    rebuttal: str = Field(
        description="A single concise rebuttal paragraph in Traditional Chinese, cite evidence ids"
    )


class OutlierRebuttal(BaseModel):
    """裁判對敗方核心論點的具體反駁——避免敗方被名義上擊敗卻論點還活著。

    P6（雙邊）：不論敗方是多數或少數，其持敗方 stance 的代表大師核心論點都須被逐點反駁
    （少數派勝出時，落敗的多數派論點同樣得被守住）；類名沿用 OutlierRebuttal 不破壞下游。
    """
    sage: str
    thesis_point: str = Field(description="The outlier sage's core claim being rebutted")
    rebuttal: str = Field(
        description="Specific point-level counter in Traditional Chinese, cite evidence ids"
    )
    evidence_ids: list[str] = Field(default_factory=list)


class DebateVerdict(BaseModel):
    winner: Literal["bull", "bear", "draw"]
    rationale: str
    strongest_bull_point: str
    strongest_bear_point: str
    unresolved_risks: list[str] = Field(default_factory=list)
    outlier_rebuttals: list[OutlierRebuttal] = Field(
        default_factory=list,
        description="One point-level rebuttal per core dissenting sage on the LOSING side "
        "(whether the losing side is the majority or the minority)",
    )
    # fail-loud：補打後仍未被論點級反駁的敗方代表大師，brief 顯式警告而非假裝完成
    unrebutted_outliers: list[str] = Field(default_factory=list)


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
    # W7：thesis / key_risks / what_would_change_my_mind 內的數字過 cite-check 後，
    # 重試額度用完仍對不上 evidence 的，由 synthesis 程式回填於此（LLM 不填）供 brief 揭露。
    unverified: list[UnverifiedClaim] = Field(default_factory=list)
