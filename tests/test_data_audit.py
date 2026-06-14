from datetime import date, timedelta

from cyber_sages.config import AuditConfig, Settings
from cyber_sages.data.evidence import Evidence, EvidenceStore
from cyber_sages.verify.data_audit import (
    AuditFinding,
    AuditorOutput,
    deterministic_checks,
    run_audit,
)

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


def test_stale_field_threshold_is_configurable():
    # 逐欄位過期門檻改由 config 驅動（不再 hardcode 500）
    store = healthy_store()
    store.add(Evidence(category="fundamentals", field="revenue_annual",
                       value=1e9, unit="USD", source="edgar",
                       as_of=date.today() - timedelta(days=300)))
    loose = AuditConfig(max_fundamentals_stale_field_days=400)
    strict = AuditConfig(max_fundamentals_stale_field_days=200)
    assert not any(f.check == "freshness" and "stale" in f.message
                   for f in errors(deterministic_checks(store, loose)))
    assert any(f.check == "freshness" and "stale" in f.message
               for f in errors(deterministic_checks(store, strict)))


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


class _FakeGateway:
    def __init__(self, output):
        self._output = output

    async def structured(self, role, *, system, prompt, schema, **kw):
        return self._output


def test_macro_freshness_threshold_is_60_days():
    # W8：max_macro_age_days 45→60。55 天內不警示、65 天才警示。
    cfg = AuditConfig(max_macro_age_days=60)
    today = date.today()
    fresh = healthy_store()
    fresh.add(Evidence(category="macro", field="fed_funds_rate", value=4.5,
                       source="FRED", as_of=today - timedelta(days=55)))
    assert not [f for f in deterministic_checks(fresh, cfg)
                if f.check == "freshness" and "macro" in f.message]
    stale = healthy_store()
    stale.add(Evidence(category="macro", field="fed_funds_rate", value=4.5,
                       source="FRED", as_of=today - timedelta(days=65)))
    assert any(f.check == "freshness" and "macro" in f.message and f.severity == "warning"
               for f in deterministic_checks(stale, cfg))


def test_missing_history_is_error():
    # W9 決議 3：history 屬核心三類，缺 → error → 降級（舊版只是 warning）
    store = healthy_store()
    store.items = [e for e in store.items if e.category != "history"]
    found = errors(deterministic_checks(store, CFG))
    assert any(f.check == "completeness" and "history" in f.message for f in found)


def test_missing_news_stays_warning():
    # 非核心類別缺 → 仍只是 warning，不觸發降級
    store = healthy_store()
    store.items = [e for e in store.items if e.category != "news"]
    findings = deterministic_checks(store, CFG)
    news = [f for f in findings if f.check == "completeness" and "news" in f.message]
    assert news and all(f.severity == "warning" for f in news)
    assert not errors(findings)


def test_etf_missing_fundamentals_stays_warning():
    # ETF 無發行人損益表：fundamentals 缺是預期，降為 warning（_missing_severity 例外）
    store = EvidenceStore(ticker="0050", market="TW", instrument="etf")
    store.add(Evidence(category="quote", field="last_price", value=100.0,
                       unit="TWD", source="a", as_of=date.today()))
    store.add(Evidence(category="history", field="sma_20", value=98.0,
                       unit="TWD", source="computed", as_of=date.today()))
    findings = deterministic_checks(store, CFG)
    fund = [f for f in findings if f.check == "completeness" and "fundamentals" in f.message]
    assert fund and all(f.severity == "warning" for f in fund)


async def test_collector_failure_of_core_category_degrades():
    # W9：核心類別抓取失敗 → collector_error finding（error）→ 降級
    store = healthy_store()
    settings = Settings.model_construct(audit=CFG)
    fake = _FakeGateway(AuditorOutput(findings=[], summary="ok"))
    report = await run_audit(store, settings, fake,  # type: ignore[arg-type]
                             fetch_failures={"history": "yfinance timeout"})
    assert report.degraded
    assert any(f.check == "collector_error" and "history" in f.message
               for f in report.errors)


async def test_collector_failure_of_noncore_is_warning():
    # 非核心類別抓取失敗 → warning，不降級
    store = healthy_store()
    settings = Settings.model_construct(audit=CFG)
    fake = _FakeGateway(AuditorOutput(findings=[], summary="ok"))
    report = await run_audit(store, settings, fake,  # type: ignore[arg-type]
                             fetch_failures={"news": "feed 503"})
    assert not report.degraded
    assert any(f.check == "collector_error" and f.severity == "warning"
               for f in report.findings)


async def test_llm_auditor_error_is_clamped_to_warning():
    # 稽核員幻覺把現實資料判成 error（曾因「未來日期」誤判全表）不該獨自觸發降級；
    # 降級只保留給可信的確定性閘門。
    store = healthy_store()
    settings = Settings.model_construct(audit=CFG)
    fake = _FakeGateway(AuditorOutput(
        findings=[AuditFinding(severity="error", check="data_freshness",
                               message="dates are in the future → fake data")],
        summary="bogus",
    ))
    report = await run_audit(store, settings, fake)  # type: ignore[arg-type]
    assert not report.degraded                      # 不再被稽核員的 error 拉降級
    assert any(f.check == "data_freshness" and f.severity == "warning"
               for f in report.findings)            # 保留為 warning 供揭露
