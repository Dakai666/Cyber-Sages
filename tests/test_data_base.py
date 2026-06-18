"""Provider 協議一致性：個股走 MarketDataProvider，額外能力走各自的 runtime_checkable
協議（ChipsProvider / MacroProvider），管線以 isinstance 偵測。純單元，不打 API。"""

from cyber_sages.data.base import (
    ChipsProvider,
    MacroProvider,
    MarketDataProvider,
    make_macro_provider,
    make_provider,
)
from cyber_sages.data.macro import FredMacroProvider
from cyber_sages.data.tw_stocks import TWStockProvider
from cyber_sages.data.us_stocks import USStockProvider


def test_stock_providers_satisfy_market_protocol():
    assert isinstance(USStockProvider(), MarketDataProvider)
    assert isinstance(TWStockProvider(), MarketDataProvider)


def test_only_tw_provides_chips():
    # chips 是台股特有能力 → 只有 TW 符合 ChipsProvider，US 不該誤判
    assert isinstance(TWStockProvider(), ChipsProvider)
    assert not isinstance(USStockProvider(), ChipsProvider)


def test_macro_is_separate_market_independent_protocol():
    assert isinstance(FredMacroProvider(), MacroProvider)
    # 個股 provider 不是 MacroProvider（macro 不綁 ticker，不混入個股協議）
    assert not isinstance(USStockProvider(), MacroProvider)
    assert not isinstance(TWStockProvider(), MacroProvider)
    # 反向：總經來源不是個股 provider
    assert not isinstance(FredMacroProvider(), MarketDataProvider)


def test_make_provider_routes_by_market():
    assert isinstance(make_provider("US"), USStockProvider)
    assert isinstance(make_provider("TW"), TWStockProvider)


def test_make_macro_provider_gated_on_fred_key(monkeypatch):
    # 可用性收斂在工廠：缺 key + 非 TW → None，呼叫端只需判斷 is not None
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    assert make_macro_provider() is None
    monkeypatch.setenv("FRED_API_KEY", "dummy")
    mp = make_macro_provider()
    assert isinstance(mp, MacroProvider) and isinstance(mp, FredMacroProvider)


def test_make_macro_provider_tw_includes_cbc(monkeypatch):
    from cyber_sages.data.tw_macro import TWMacroProvider

    # 央行源僅 TW 市場併入（台灣國內利率對美股無關，不污染 US run）。
    # 無 FRED key + TW → 只剩央行單源（不需金鑰）。
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    tw_only = make_macro_provider("TW")
    assert isinstance(tw_only, TWMacroProvider)
    # 同條件 US → None（央行不併入、FRED 又缺 key）。
    assert make_macro_provider("US") is None
    # 有 FRED key + TW → 合併視圖（FRED + 央行），仍滿足 MacroProvider 協議。
    monkeypatch.setenv("FRED_API_KEY", "dummy")
    merged = make_macro_provider("TW")
    assert isinstance(merged, MacroProvider)
    assert not isinstance(merged, (FredMacroProvider, TWMacroProvider))  # 是合併視圖


async def test_merged_macro_tolerates_one_source_failing():
    from cyber_sages.data.base import _MergedMacroProvider
    from cyber_sages.data.evidence import Evidence

    class _Good:
        market = "MACRO"
        async def get_macro(self):
            return [Evidence(category="macro", field="x", value=1.0, unit="%", source="g")]

    class _Bad:
        market = "MACRO"
        async def get_macro(self):
            raise RuntimeError("source down")

    # 一源拋例外只丟該源結果，不拖垮其餘（best-effort 合併）。
    merged = _MergedMacroProvider([_Good(), _Bad()])
    evs = await merged.get_macro()
    assert [e.field for e in evs] == ["x"]
