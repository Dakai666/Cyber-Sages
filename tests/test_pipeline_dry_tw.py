"""Mock-LLM 台股全管線 dry-run：2330 個股（chips + macro）與 0050 ETF，不打任何 API。

與 test_pipeline_dry.py 的 AAPL 版互補。fixture（tests/conftest.py）用相對日期動態
生成（非靜態 JSON），讓新鮮度檢查永遠以「剛抓到的資料」口徑通過——這裡要驗的正是
兩條核心保證：
1. 資料齊全時 TW 管線七階段跑完且不降級（audit.degraded == False）
2. ETF 豁免基本面：0050 缺財報只能是 warning，不會被誤降級到 0.5（issue #5 的動機）
"""

import pytest

from cyber_sages.config import load_settings
from cyber_sages.data.macro import FredMacroProvider
from cyber_sages.pipeline import run_pipeline
from cyber_sages.report import build_agent_payload, render_brief
from tests.conftest import make_macro_evidence
from tests.test_pipeline_dry import FakeGateway


async def test_tw_stock_pipeline_full(patch_tw_provider, monkeypatch):
    """2330 個股：chips + macro 齊全，七階段跑完、不降級。"""
    patch_tw_provider(etf=False)
    monkeypatch.setattr(FredMacroProvider, "available", lambda: True)

    async def fake_macro(self):
        return make_macro_evidence()

    monkeypatch.setattr(FredMacroProvider, "get_macro", fake_macro)

    settings = load_settings()
    gateway = FakeGateway()
    stages_seen: list[tuple[str, str]] = []

    result = await run_pipeline(
        "2330", settings, gateway,  # type: ignore[arg-type]
        n_sages=3,
        on_stage=lambda s, st, d: stages_seen.append((s, st)),
    )

    # 市場路由與七階段
    assert result.store.market == "TW"
    assert result.store.instrument == "stock"
    assert {s for s, _ in stages_seen} == {"collect", "audit", "analyze", "cite",
                                           "council", "debate", "synthesize"}

    # 核心保證：資料齊全且新鮮 → 不降級（fixture 為相對日期，永不過期）
    assert result.audit.degraded is False, [f.message for f in result.audit.errors]

    # chips 與 macro 都進了 evidence store
    assert result.store.by_category("chips")
    assert result.store.by_category("macro")

    # macro evidence 在場 → 5 位分析師全出席（含 Macro Analyst）
    assert gateway.calls.count("analyst") == 5
    assert gateway.calls.count("sage") == 3
    assert "chief" in gateway.calls and "risk" in gateway.calls

    # 未降級 → 風控調整完整生效：0.75 - 0.05 = 0.70（不被 0.5 封頂）
    assert result.verdict.conviction == pytest.approx(0.70, abs=0.01)

    # 簡報與 payload：市場標記 + 三時間軸 + 陪審團
    brief = render_brief(result)
    assert "（TW）決策簡報" in brief
    assert "三時間軸" in brief and "陪審團" in brief
    payload = build_agent_payload(result)
    assert payload["market"] == "TW"
    assert payload["council"]["signals"][0]["sage"]


async def test_tw_etf_pipeline_fundamentals_exempt(patch_tw_provider):
    """0050 ETF：無個股財報只能是 warning——絕不因缺基本面降級到 0.5。"""
    patch_tw_provider(etf=True)

    settings = load_settings()
    gateway = FakeGateway()

    result = await run_pipeline(
        "0050", settings, gateway,  # type: ignore[arg-type]
        n_sages=2, include_macro=False,
    )

    assert result.store.market == "TW"
    assert result.store.instrument == "etf"
    assert not result.store.by_category("fundamentals")

    # ETF 豁免的本體：缺財報 = completeness warning（非 error）→ 不降級
    assert result.audit.degraded is False, [f.message for f in result.audit.errors]
    fund_findings = [f for f in result.audit.findings
                     if f.check == "completeness" and "fundamentals" in f.message]
    assert fund_findings and all(f.severity == "warning" for f in fund_findings)
    assert "ETF" in fund_findings[0].message

    # 未降級 → conviction 不被 0.5 封頂（0050 曾被誤降級的迴歸保護）
    assert result.verdict.conviction == pytest.approx(0.70, abs=0.01)

    # 個股財報缺席時，仍有 profile/quote 撐住 4 位分析師（macro 關閉故無第 5 位）
    assert gateway.calls.count("analyst") == 4
