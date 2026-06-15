"""Spec C v2 PR2 — horizon 分流：按 horizon 座位/abstain + 口徑常數守衛。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cyber_sages.agents.council import _HORIZON_NOTE, run_council, tally
from cyber_sages.agents.schemas import SageSignal
from cyber_sages.agents.synthesis import _HORIZON_PLAN
from cyber_sages.data.evidence import EvidenceStore
from cyber_sages.personas.pack import Persona, load_personas


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        defaults=SimpleNamespace(sages=10),
        roles={"sage": SimpleNamespace(provider="p")},
        providers={"p": SimpleNamespace(has=lambda feat: False)},
        citation=SimpleNamespace(numeric_tolerance_pct=1.0),
    )


def _gw():
    class G:
        async def structured(self, role, *, system, prompt, schema, **kw):
            if schema.__name__ == "ScoutSignal":   # P2 第一輪 scout（value 房間 >6 觸發兩階段）
                return schema(stance="neutral", confidence=0.5, one_liner="快讀中性")
            return schema(stance="neutral", confidence=0.5, thesis="t",
                          what_would_change_my_mind="w")
    return G()


def test_persona_default_horizons_is_both():
    # 未宣告 horizons 的 persona 預設兩者皆適用（向後相容：舊 persona 照常全程出席）
    p = Persona(key="x", name="X", philosophy="p", focus="f", voice="v")
    assert p.horizons == ["trading", "value"]


def test_roster_horizon_tags():
    by = {p.name: p.horizons for p in load_personas()}
    assert by["Warren Buffett"] == ["value"] and by["Charlie Munger"] == ["value"]
    assert by["Jesse Livermore"] == ["trading"]
    assert by["Mark Minervini"] == ["trading"] and by["Linda Raschke"] == ["trading"]
    assert "trading" in by["Stanley Druckenmiller"] and "value" in by["Stanley Druckenmiller"]


async def test_value_horizon_abstains_trading_only_sages():
    council = await run_council(EvidenceStore(ticker="NVDA", market="US"), [],
                               _settings(), _gw(), horizon="value")
    seated = [s.sage for s in council.signals]
    assert "Warren Buffett" in seated                 # 價值大師出席
    assert "Jesse Livermore" in council.abstained     # 純交易者 abstain（非本 horizon）
    assert "Jesse Livermore" not in seated


async def test_trading_horizon_seats_only_trading_sages():
    council = await run_council(EvidenceStore(ticker="NVDA", market="US"), [],
                               _settings(), _gw(), horizon="trading", n_sages=10)
    seated = {s.sage for s in council.signals}
    # 只有 trading 適用者出席（PR3 roster：Livermore / Minervini / Raschke 純交易 +
    # Druckenmiller / Taleb 兼職）。純價值大師一律 abstain。
    assert seated <= {"Jesse Livermore", "Mark Minervini", "Linda Raschke",
                      "Stanley Druckenmiller", "Nassim Taleb"}
    assert {"Jesse Livermore", "Mark Minervini", "Linda Raschke"} <= seated  # 純交易者皆出席
    assert "Warren Buffett" in council.abstained      # 價值大師對短線 abstain
    assert "Charlie Munger" in council.abstained


async def test_zero_applicable_horizon_fails_loud(monkeypatch):
    # review C：某 horizon 無任何適用大師時 fail-loud，不可靜默產出「0 席、中性共識」。
    only_value = [Persona(key="b", name="B", philosophy="p", focus="f", voice="v",
                          horizons=["value"])]
    monkeypatch.setattr("cyber_sages.agents.council.load_personas",
                        lambda limit=None: only_value)
    with pytest.raises(RuntimeError, match="沒有任何適用大師"):
        await run_council(EvidenceStore(ticker="X", market="US"), [], _settings(),
                          _gw(), horizon="trading")


def test_tally_three_way_tie_yields_neutral_zero():
    # review D：trading run 可能 3 席 1B/1S/1N——consensus 留 neutral、weighted 0.0、不 crash。
    sigs = [
        SageSignal(sage="A", stance="bullish", confidence=0.6, thesis="t",
                   what_would_change_my_mind="w"),
        SageSignal(sage="B", stance="bearish", confidence=0.6, thesis="t",
                   what_would_change_my_mind="w"),
        SageSignal(sage="C", stance="neutral", confidence=0.6, thesis="t",
                   what_would_change_my_mind="w"),
    ]
    personas = [Persona(key=k, name=k, philosophy="p", focus="f", voice="v")
                for k in ("A", "B", "C")]
    v = tally(sigs, personas)
    assert v.consensus == "neutral" and v.weighted_score == 0.0
    assert v.bullish == 1 and v.bearish == 1 and v.neutral == 1


def test_horizon_guidance_constants_distinct():
    # 兩個 horizon 的 council note 與 action-plan 口徑都存在且不同（避免複製貼上同一段）
    assert set(_HORIZON_NOTE) == {"value", "trading"}
    assert _HORIZON_NOTE["value"] != _HORIZON_NOTE["trading"]
    assert set(_HORIZON_PLAN) == {"value", "trading"}
    assert _HORIZON_PLAN["value"] != _HORIZON_PLAN["trading"]
