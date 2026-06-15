"""Spec C v2 PR4b — P2 兩階段 council：scout 全員 → 只深入代表，全員仍計票。

不打網路：合成 personas + 假 gateway（記錄 scout / deep 呼叫），驗證取樣機制與成本下降代理
（deep 呼叫數 < 房間人數）。實際 token ≥30% 下降於實機 run 驗證。
"""

from __future__ import annotations

from types import SimpleNamespace

from cyber_sages.agents.council import _consensus_stance, _select_reps, run_council
from cyber_sages.agents.schemas import SageSignal, ScoutSignal
from cyber_sages.data.evidence import EvidenceStore
from cyber_sages.personas.pack import Persona


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        defaults=SimpleNamespace(sages=10),
        roles={"sage": SimpleNamespace(provider="p")},
        providers={"p": SimpleNamespace(has=lambda feat: False)},
        citation=SimpleNamespace(numeric_tolerance_pct=1.0),
    )


def _personas(n: int) -> list[Persona]:
    # weight 由高到低 n..1（degraded 單檔型，無 pack）
    return [Persona(key=f"k{i}", name=f"N{i}", philosophy="p", focus="f", voice="v",
                    weight=float(n - i)) for i in range(n)]


class _StageGateway:
    """依 system 內的大師名回固定 stance；記錄每次 (role, schema, name) 以分辨 scout vs deep。"""
    def __init__(self, stance_map: dict[str, str]):
        self.stance_map = stance_map
        self.calls: list[tuple[str, str, str | None]] = []

    def _name(self, system: str) -> str | None:
        for n in self.stance_map:
            if f"You are {n}." in system:
                return n
        return None

    async def structured(self, role, *, system, prompt, schema, **kw):
        name = self._name(system)
        st = self.stance_map.get(name, "neutral")
        self.calls.append((role, schema.__name__, name))
        if schema.__name__ == "ScoutSignal":
            return ScoutSignal(stance=st, confidence=0.5, one_liner="快讀")
        return SageSignal(stance=st, confidence=0.6, thesis="深度", what_would_change_my_mind="w")

    def of(self, schema_name: str) -> list[tuple[str, str, str | None]]:
        return [c for c in self.calls if c[1] == schema_name]


# ---------- 取樣選代表 ----------


def test_select_reps_caps_consensus_and_includes_dissent():
    ps = _personas(9)
    scouted = [(p, SageSignal(sage=p.name, confidence=0.5, thesis="t",
                              what_would_change_my_mind="w",
                              stance=("bullish" if i <= 6 else "bearish")))
               for i, p in enumerate(ps)]
    reps = [p.name for p in _select_reps(scouted)]
    assert reps[:3] == ["N0", "N1", "N2"]      # 共識（bullish）代表：weight 最高三位
    assert set(reps[3:]) == {"N7", "N8"}       # 離群（bearish）代表：獨立配額，異議不被淹沒
    assert len(reps) == 5


def test_select_reps_unanimous_has_no_dissent():
    ps = _personas(5)
    scouted = [(p, SageSignal(sage=p.name, stance="bullish", confidence=0.5,
                              thesis="t", what_would_change_my_mind="w")) for p in ps]
    reps = [p.name for p in _select_reps(scouted)]
    assert reps == ["N0", "N1", "N2"]          # 全共識 → 只取共識代表上限、無離群配額


def test_consensus_stance_rules():
    assert _consensus_stance({"bullish": 5, "bearish": 1, "neutral": 3}) == "bullish"
    assert _consensus_stance({"bullish": 1, "bearish": 5, "neutral": 3}) == "bearish"
    assert _consensus_stance({"bullish": 3, "bearish": 3, "neutral": 1}) == "neutral"  # 平手
    assert _consensus_stance({"bullish": 2, "bearish": 0, "neutral": 4}) == "neutral"  # 不過 neutral


# ---------- run_council 兩階段整合 ----------


async def test_two_stage_scouts_all_but_deeps_only_reps(monkeypatch):
    ps = _personas(9)  # 房間 9 > deep 預算 6 → 觸發兩階段
    monkeypatch.setattr("cyber_sages.agents.council.load_personas", lambda limit=None: ps)
    sm = {f"N{i}": ("bullish" if i <= 6 else "bearish") for i in range(9)}
    gw = _StageGateway(sm)
    c = await run_council(EvidenceStore(ticker="X", market="US"), [], _settings(), gw, n_sages=9)

    assert len(gw.of("ScoutSignal")) == 9      # 全員先 scout
    deep = gw.of("SageSignal")
    assert len(deep) == 5                       # 只深入代表（3 共識 + 2 離群）——成本下降代理
    assert len(c.signals) == 9                  # 全員仍計票（不丟票）
    assert c.bullish == 7 and c.bearish == 2    # 投票反映整個房間、非僅代表
    assert set(c.scouted_only) == {"N3", "N4", "N5", "N6"}
    deep_names = {x[2] for x in deep}
    assert {"N0", "N1", "N2"} <= deep_names     # 共識代表（weight 最高三位）獲深入
    assert {"N7", "N8"} <= deep_names           # 離群者獲深入（反單一視角）


async def test_single_stage_when_house_within_budget(monkeypatch):
    ps = _personas(5)  # 5 ≤ 6 → 不 scout、全員深入（小房間兩階段反而貴）
    monkeypatch.setattr("cyber_sages.agents.council.load_personas", lambda limit=None: ps)
    gw = _StageGateway({f"N{i}": "bullish" for i in range(5)})
    c = await run_council(EvidenceStore(ticker="X", market="US"), [], _settings(), gw, n_sages=5)
    assert gw.of("ScoutSignal") == []           # 無 scout
    assert len(gw.of("SageSignal")) == 5        # 全員深入
    assert c.scouted_only == [] and len(c.signals) == 5


async def test_scout_role_uses_sage_scout_when_configured(monkeypatch):
    ps = _personas(9)
    monkeypatch.setattr("cyber_sages.agents.council.load_personas", lambda limit=None: ps)
    sm = {f"N{i}": "bullish" for i in range(9)}

    # 預設（無 sage_scout role）→ scout 回退用 sage
    gw1 = _StageGateway(sm)
    await run_council(EvidenceStore(ticker="X", market="US"), [], _settings(), gw1, n_sages=9)
    assert {c[0] for c in gw1.of("ScoutSignal")} == {"sage"}

    # 設定 sage_scout role → scout 走 sage_scout（可指便宜模型）
    s = _settings()
    s.roles["sage_scout"] = SimpleNamespace(provider="p")
    gw2 = _StageGateway(sm)
    await run_council(EvidenceStore(ticker="X", market="US"), [], s, gw2, n_sages=9)
    assert {c[0] for c in gw2.of("ScoutSignal")} == {"sage_scout"}


async def test_two_stage_scout_only_signal_counts_but_has_no_sop_trace(monkeypatch):
    ps = _personas(9)
    monkeypatch.setattr("cyber_sages.agents.council.load_personas", lambda limit=None: ps)
    sm = {f"N{i}": ("bullish" if i <= 6 else "bearish") for i in range(9)}
    c = await run_council(EvidenceStore(ticker="X", market="US"), [],
                          _settings(), _StageGateway(sm), n_sages=9)
    by = {s.sage: s for s in c.signals}
    # scout-only 大師：有 stance/confidence（計票），thesis 是 scout 一句話、無 sop_trace
    assert by["N4"].sop_trace == [] and by["N4"].thesis == "快讀"
    # 深入代表：thesis 是深度版
    assert by["N0"].thesis == "深度"