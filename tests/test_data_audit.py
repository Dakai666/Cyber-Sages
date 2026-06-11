from datetime import date, timedelta

from cyber_sages.config import AuditConfig
from cyber_sages.data.evidence import Evidence, EvidenceStore
from cyber_sages.verify.data_audit import deterministic_checks

CFG = AuditConfig(max_price_divergence_pct=2.0, max_quote_age_days=5,
                  max_fundamentals_age_days=120)


def healthy_store() -> EvidenceStore:
    store = EvidenceStore(ticker="TEST")
    today = date.today()
    store.add(Evidence(category="quote", field="last_price", value=100.0,
                       unit="USD", source="a", as_of=today))
    store.add(Evidence(category="quote", field="latest_close", value=99.5,
                       unit="USD", source="b", as_of=today - timedelta(days=1)))
    store.add(Evidence(category="fundamentals", field="revenue_annual",
                       value=1e9, unit="USD", source="edgar", as_of=today - timedelta(days=60)))
    store.add(Evidence(category="fundamentals", field="net_income_annual",
                       value=2e8, unit="USD", source="edgar", as_of=today - timedelta(days=60)))
    store.add(Evidence(category="history", field="sma_20", value=98.0,
                       unit="USD", source="computed", as_of=today))
    store.add(Evidence(category="news", field="headline_1", value="ok",
                       source="news", as_of=today))
    return store


def errors(findings):
    return [f for f in findings if f.severity == "error"]


def test_healthy_store_passes():
    assert errors(deterministic_checks(healthy_store(), CFG)) == []


def test_missing_fundamentals_is_error():
    store = healthy_store()
    store.items = [e for e in store.items if e.category != "fundamentals"]
    found = errors(deterministic_checks(store, CFG))
    assert any(f.check == "completeness" for f in found)


def test_stale_quote_is_error():
    store = healthy_store()
    for e in store.items:
        if e.field == "latest_close":
            e.as_of = date.today() - timedelta(days=30)
    found = errors(deterministic_checks(store, CFG))
    assert any(f.check == "freshness" for f in found)


def test_cross_source_divergence_is_error():
    store = healthy_store()
    for e in store.items:
        if e.field == "last_price":
            e.value = 120.0  # 20% off latest_close
    found = errors(deterministic_checks(store, CFG))
    assert any(f.check == "cross_source" for f in found)


def test_severely_stale_single_field_is_error():
    # NVDA 真實案例：換 XBRL 標籤後撈到 4 年前的營收，
    # 其他欄位新鮮，整類檢查不會發現——逐欄位檢查必須抓到
    store = healthy_store()
    store.add(Evidence(category="fundamentals", field="revenue_annual",
                       value=26.9e9, unit="USD", source="edgar",
                       as_of=date.today() - timedelta(days=1500)))
    found = errors(deterministic_checks(store, CFG))
    assert any(f.check == "freshness" and "stale" in f.message for f in found)


def test_eps_inconsistency_is_warning():
    store = healthy_store()
    store.add(Evidence(category="fundamentals", field="eps_diluted_annual",
                       value=10.0, unit="USD/shares", source="edgar"))
    store.add(Evidence(category="fundamentals", field="shares_outstanding",
                       value=1e9, unit="shares", source="edgar"))
    # implied EPS = 2e8 / 1e9 = 0.2，與 10.0 嚴重不符
    findings = deterministic_checks(store, CFG)
    assert any(f.check == "internal_consistency" and f.severity == "warning"
               for f in findings)
