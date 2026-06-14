"""Mock-LLM 全管線 dry-run：用 AAPL fixture 資料 + 假 gateway，不打任何 API。"""

import json
from pathlib import Path

import pytest

from cyber_sages.agents.schemas import (
    ActionPlan,
    AnalystReport,
    DebateArgument,
    DebateVerdict,
    FinalVerdict,
    HorizonView,
    PriceLevel,
    RiskNote,
    SageSignal,
)
from cyber_sages.config import load_settings
from cyber_sages.data.evidence import Evidence
from cyber_sages.data.us_stocks import USStockProvider
from cyber_sages.pipeline import run_pipeline
from cyber_sages.report import build_agent_payload, render_brief
from cyber_sages.verify.citation_check import Claim

FIXTURE = Path(__file__).parent / "fixtures" / "aapl_evidence.json"


class FakeGateway:
    """依 schema 回傳合理的罐頭輸出；claims 引用真實 evidence id。"""

    def __init__(self):
        self.calls: list[str] = []

    async def structured(self, role, *, system, prompt, schema, **kwargs):
        self.calls.append(role)
        name = schema.__name__
        if name == "AuditorOutput":
            return schema(findings=[], summary="資料無異常")
        if name == "AnalystReport":
            return AnalystReport(
                summary="基本面穩健。",
                claims=[Claim(text="獲利能力強勁", evidence_ids=["E001"])],
            )
        if name == "SageSignal":
            return SageSignal(
                stance="bullish", confidence=0.7, thesis="符合我的選股哲學。",
                key_evidence_ids=["E001"], what_would_change_my_mind="基本面惡化",
            )
        if name == "DebateArgument":
            return DebateArgument(argument="論點如上 [E001]。")
        if name == "DebateVerdict":
            return DebateVerdict(
                winner="bull", rationale="多方證據較紮實",
                strongest_bull_point="現金流", strongest_bear_point="估值偏高",
                unresolved_risks=["宏觀不確定性"],
            )
        if name == "FinalVerdict":
            return FinalVerdict(
                stance="bullish", conviction=0.75, thesis="綜合判定看多。",
                action_plan=ActionPlan(
                    action="buy_dip", directive="回檔至 SMA50 附近分批買進。",
                    entry_zone=[PriceLevel(price=282.9, label="第一批",
                                           basis="SMA50", evidence_ids=["E001"])],
                    stop_loss=PriceLevel(price=260.0, label="停損",
                                         basis="SMA200 下方",
                                         evidence_ids=["E999"]),  # 故意給無效錨點
                    targets=[PriceLevel(price=315.0, label="第一目標",
                                        basis="52w 高點", evidence_ids=["E001"])],
                    position_hint="分3批，總倉位上限1/2",
                    invalidation="跌破 SMA200 且 RSI 不止穩",
                ),
                horizons=[
                    HorizonView(horizon="short", stance="neutral", summary="動能偏弱"),
                    HorizonView(horizon="mid", stance="bullish", summary="趨勢完好"),
                    HorizonView(horizon="long", stance="bullish", summary="護城河仍在"),
                ],
                supporting_points=["現金流強"], key_risks=["估值"],
                what_would_change_my_mind="營收連兩季下滑",
                dissent_summary="Taleb 提醒尾部風險。",
            )
        if name == "RiskNote":
            return RiskNote(concerns=["集中度"], data_quality_acceptable=True,
                            conviction_adjustment=-0.05)
        raise AssertionError(f"unexpected schema {name}")


@pytest.fixture()
def fixture_evidence() -> dict[str, list[Evidence]]:
    raw = json.loads(FIXTURE.read_text())
    by_cat: dict[str, list[Evidence]] = {}
    for item in raw["items"]:
        ev = Evidence.model_validate(item)
        by_cat.setdefault(ev.category, []).append(ev)
    return by_cat


@pytest.fixture(autouse=True)
def patch_provider(monkeypatch, fixture_evidence):
    async def fake(category):
        return fixture_evidence.get(category, [])

    monkeypatch.setattr(USStockProvider, "get_quote",
                        lambda self, t: fake("quote"))
    monkeypatch.setattr(USStockProvider, "get_history",
                        lambda self, t: fake("history"))

    async def fundamentals(self, t):
        return fixture_evidence.get("fundamentals", []) + fixture_evidence.get("profile", [])

    monkeypatch.setattr(USStockProvider, "get_fundamentals", fundamentals)
    monkeypatch.setattr(USStockProvider, "get_news",
                        lambda self, t: fake("news"))


async def test_full_pipeline_dry_run():
    settings = load_settings()
    gateway = FakeGateway()
    stages_seen: list[tuple[str, str]] = []

    result = await run_pipeline(
        "AAPL", settings, gateway,  # type: ignore[arg-type]
        n_sages=3, include_macro=False,  # dry-run 不打 FRED
        on_stage=lambda s, st, d: stages_seen.append((s, st)),
    )

    # 七階段全部跑完
    seen_keys = {s for s, _ in stages_seen}
    assert seen_keys == {"collect", "audit", "analyze", "cite", "council",
                         "debate", "synthesize"}
    # fixture 資料新鮮度過期是預期的（錄製日之後跑），但管線要能以降級模式走完
    assert result.verdict.stance == "bullish"
    assert len(result.council.signals) == 3
    assert result.council.bullish == 3
    # 風控調整有生效：0.75 - 0.05 = 0.70（若 audit 降級則封頂 0.5）
    expected = 0.5 if result.audit.degraded else 0.70
    assert result.verdict.conviction == pytest.approx(expected, abs=0.01)
    # 角色呼叫齊全
    assert gateway.calls.count("analyst") == 4
    assert gateway.calls.count("sage") == 3
    assert "chief" in gateway.calls and "risk" in gateway.calls

    # 無效錨點（E999）被標警示且 id 被清掉
    stop = result.verdict.action_plan.stop_loss
    assert "⚠ 無有效 evidence 錨點" in stop.basis
    assert stop.evidence_ids == []
    # 有效錨點保留
    assert result.verdict.action_plan.entry_zone[0].evidence_ids == ["E001"]

    # 決策簡報含行動計畫與時間軸
    brief = render_brief(result)
    assert "決策簡報" in brief and "回檔買進" in brief and "三時間軸" in brief

    # agent payload 結構完整
    payload = build_agent_payload(result)
    assert payload["verdict"]["action_plan"]["action"] == "buy_dip"
    assert payload["council"]["signals"][0]["sage"]
    assert "note_to_judge" in payload


async def test_pipeline_skip_debate():
    settings = load_settings()
    result = await run_pipeline(
        "AAPL", settings, FakeGateway(),  # type: ignore[arg-type]
        n_sages=2, skip_debate=True, include_macro=False,
    )
    assert result.debate is None
    assert result.bull is None
