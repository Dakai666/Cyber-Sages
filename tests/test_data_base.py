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
    # 可用性收斂在工廠：缺 key → None，呼叫端只需判斷 is not None
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    assert make_macro_provider() is None
    monkeypatch.setenv("FRED_API_KEY", "dummy")
    mp = make_macro_provider()
    assert isinstance(mp, MacroProvider) and isinstance(mp, FredMacroProvider)
