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


def test_persona_default_aggression_is_both():
    # 未宣告 aggression 的 persona 預設兩者皆適用（向後相容，同 horizons）
    p = Persona(key="x", name="X", philosophy="p", focus="f", voice="v")
    assert p.aggression == ["conservative", "aggressive"]


def test_roster_aggression_tags():
    by = {p.name: p.aggression for p in load_personas()}
    assert by["Warren Buffett"] == ["conservative"] and by["Jim Chanos"] == ["conservative"]
    assert by["Masayoshi Son"] == ["aggressive"] and by["George Soros"] == ["aggressive"]
    # 中庸者兩個都列、兩種陪審團都出席
    assert set(by["Aswath Damodaran"]) == {"conservative", "aggressive"}
    assert set(by["Peter Lynch"]) == {"conservative", "aggressive"}


async def test_aggression_filter_seats_only_matching_temperament():
    # 四象限：value + conservative → 只座位保守派；激進專屬者（Son）退場、中庸者（Lynch）仍出席。
    council = await run_council(EvidenceStore(ticker="NVDA", market="US"), [],
                               _settings(), _gw(), horizon="value", aggression="conservative")
    seated = {s.sage for s in council.signals} | set(council.scouted_only)
    assert "Warren Buffett" in seated and "Jim Chanos" in seated   # 保守派出席
    assert "Peter Lynch" in seated                                  # 中庸者兩邊都出席
    assert "Masayoshi Son" in council.abstained                     # 激進專屬者退場
    assert "Masayoshi Son" not in seated


async def test_grand_assembly_seats_all_value_sages_no_truncation():
    # 大師會堂（n_sages=None、無 aggression）：全體 value-eligible 出席，不被預設上限截斷——
    # 修掉舊 default --sages 10 把 Trump/Taleb/Wood/Icahn 切掉的問題。
    council = await run_council(EvidenceStore(ticker="NVDA", market="US"), [],
                               _settings(), _gw(), horizon="value", n_sages=None)
    seated = {s.sage for s in council.signals} | set(council.scouted_only)
    # 必載：前幾次 review 反覆點名「被舊 default 截掉」的 4 位，現在都該在席（>= 而非 == 才耐 roster 成長）
    for name in ("Donald Trump", "Nassim Taleb", "Cathie Wood", "Carl Icahn"):
        assert name in seated, f"{name} 應在大師會堂出席（不該被截斷）"
    assert len(seated) >= 14   # 全體 value-eligible（加 value persona 不該壞此測試）


async def test_value_horizon_abstains_trading_only_sages():
    council = await run_council(EvidenceStore(ticker="NVDA", market="US"), [],
                               _settings(), _gw(), horizon="value")
    seated = [s.sage for s in council.signals]
    assert "Warren Buffett" in seated                 # 價值大師出席
    assert "Jesse Livermore" in council.abstained     # 純交易者 abstain（非本 horizon）
    assert "Jesse Livermore" not in seated


async def test_trading_horizon_seats_only_trading_sages():
    # n_sages=None＝大師會堂（與新預設一致）：守護「該 horizon 的都出席」contract——
    # 若未來 trading-eligible >10，舊 n_sages=10 會 silently 切掉一位卻不 fail。
    council = await run_council(EvidenceStore(ticker="NVDA", market="US"), [],
                               _settings(), _gw(), horizon="trading", n_sages=None)
    seated = {s.sage for s in council.signals}
    # 只有 trading 適用者出席（Livermore / Minervini / Raschke 純交易 + Druckenmiller / Taleb /
    # Soros 兼職 + Trump 催化劑型 + Roaring Kitty 散戶情緒）。純價值大師一律 abstain。
    assert seated <= {"Jesse Livermore", "Mark Minervini", "Linda Raschke",
                      "Stanley Druckenmiller", "Nassim Taleb", "Donald Trump",
                      "George Soros", "Keith Gill (Roaring Kitty)"}
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


async def test_zero_applicable_aggression_fails_loud(monkeypatch):
    # 第二條軸同樣 fail-loud：只 conservative 的 roster + --aggression aggressive → 0 席，須 raise
    # （不可靜默產出「0 席、中性共識」）。錯誤訊息帶 aggression 標籤，curator 一眼知是哪條軸 misfire。
    only_conservative = [Persona(key="b", name="B", philosophy="p", focus="f", voice="v",
                                 horizons=["value"], aggression=["conservative"])]
    monkeypatch.setattr("cyber_sages.agents.council.load_personas",
                        lambda limit=None: only_conservative)
    with pytest.raises(RuntimeError, match="aggression"):
        await run_council(EvidenceStore(ticker="X", market="US"), [], _settings(),
                          _gw(), horizon="value", aggression="aggressive")


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
