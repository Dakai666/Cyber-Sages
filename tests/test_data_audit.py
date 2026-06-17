from datetime import date, timedelta

from cyber_sages.config import AuditConfig, Settings
from cyber_sages.data.evidence import Evidence, EvidenceStore
from cyber_sages.verify.data_audit import (
    AuditFinding,
    AuditorOutput,
    AuditReport,
    build_health_card,
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


def test_cross_source_divergence_is_fatal():
    # Spec F / S2：兩條當前價讀數（fast_info vs 盤中最後成交）背離 → fatal（當前價不可信）。
    store = healthy_store()
    store.add(Evidence(category="quote", field="last_price_intraday", value=120.0,
                       unit="USD", source="yfinance intraday", as_of=date.today()))
    found = deterministic_checks(store, CFG)  # last_price=100 vs intraday=120 → 20%
    cs = [f for f in found if f.check == "cross_source"]
    assert cs and all(f.severity == "fatal" for f in cs)


def test_cross_source_skipped_emits_warning_not_silent():
    # 無盤中讀數時不做「當前 vs 昨收」的舊誤判，但也不能默默跳過——加一條 warning，
    # 否則讀者會把「無 finding」誤讀成「跨源通過」。
    store = healthy_store()
    cs = [f for f in deterministic_checks(store, CFG) if f.check == "cross_source"]
    assert cs and all(f.severity == "warning" for f in cs)
    assert "跳過" in cs[0].message


def test_cross_source_agrees_within_threshold():
    store = healthy_store()
    store.add(Evidence(category="quote", field="last_price_intraday", value=101.0,
                       unit="USD", source="yfinance intraday", as_of=date.today()))
    assert not any(f.check == "cross_source" for f in deterministic_checks(store, CFG))


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


async def test_collector_failure_merges_into_completeness_no_duplicate():
    # #30：類別同時「缺」(completeness) 且「抓取失敗」(fetch_failures) 時，錯因併入
    # completeness 那條，不另出第二條 ~重複 finding。
    store = healthy_store()
    store.items = [e for e in store.items if e.category != "news"]  # news 真的缺
    settings = Settings.model_construct(audit=CFG)
    fake = _FakeGateway(AuditorOutput(findings=[], summary="ok"))
    report = await run_audit(store, settings, fake,  # type: ignore[arg-type]
                             fetch_failures={"news": "feed 503"})
    news = [f for f in report.findings if "news" in f.message]
    assert len(news) == 1                              # 合併、不重複
    assert news[0].check == "completeness"
    assert "feed 503" in news[0].message               # 錯因已併入
    assert not report.degraded                         # news 非核心，仍 warning


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


# ---------- Spec F：fatal tier + halt ----------

def test_nonpositive_price_is_fatal():
    # Spec F / S4：非正值報價＝分析前提已壞 → fatal（中止），不再只是 error（降級）。
    store = healthy_store()
    for e in store.items:
        if e.field == "last_price":
            e.value = 0.0
    findings = deterministic_checks(store, CFG)
    sanity = [f for f in findings if f.check == "sanity"]
    assert sanity and all(f.severity == "fatal" for f in sanity)


def test_audit_report_blocked_and_degraded_semantics():
    # fatal ⇒ blocked 且 degraded（中止隱含降級）；純 error ⇒ degraded 但非 blocked。
    from cyber_sages.verify.data_audit import AuditReport
    fatal = AuditReport(findings=[AuditFinding(severity="fatal", check="sanity", message="x")])
    assert fatal.blocked and fatal.degraded and fatal.fatals
    err = AuditReport(findings=[AuditFinding(severity="error", check="completeness", message="y")])
    assert err.degraded and not err.blocked
    clean = AuditReport(findings=[AuditFinding(severity="warning", check="freshness", message="z")])
    assert not clean.degraded and not clean.blocked


async def test_llm_auditor_fatal_is_clamped_to_warning():
    # Spec F：fatal 只能由確定性閘門產生——LLM 稽核員回 fatal 一律壓到 warning，
    # 不能讓非確定性模型獨自把整場分析喊停。
    store = healthy_store()
    settings = Settings.model_construct(audit=CFG)
    fake = _FakeGateway(AuditorOutput(
        findings=[AuditFinding(severity="fatal", check="hallucinated",
                               message="model thinks everything is broken")],
        summary="bogus",
    ))
    report = await run_audit(store, settings, fake)  # type: ignore[arg-type]
    assert not report.blocked                        # 模型不能觸發中止
    assert any(f.check == "hallucinated" and f.severity == "warning"
               for f in report.findings)


# ---------- Spec F / S7：分維度健康度評分卡 ----------

def _card(store):
    findings = deterministic_checks(store, CFG)
    return build_health_card(AuditReport(findings=findings), store)


def test_health_card_healthy_store_is_ok_no_cap():
    # healthy_store 無 macro evidence → macro 維度 missing（非 degraded，不封頂）；
    # 核心三維度與 sentiment 皆 healthy，overall 仍 ok。
    card = _card(healthy_store())
    assert card.overall == "ok" and card.confidence_cap is None
    assert all(card.dimensions[d].status == "healthy"
               for d in ("price", "technical", "fundamentals", "sentiment"))
    assert card.dimensions["macro"].status == "missing"


def test_health_card_core_degrade_caps_half_and_names_dimension():
    # 核心維度（fundamentals）受損 → 該維度 degraded、cap 0.5，揭露指名是 fundamentals。
    store = healthy_store()
    store.add(Evidence(category="fundamentals", field="revenue_annual", value=1e9,
                       unit="USD", source="edgar",
                       as_of=date.today() - timedelta(days=1500)))  # 嚴重過期 → error
    card = _card(store)
    assert card.overall == "degraded" and card.confidence_cap == 0.5
    assert card.dimensions["fundamentals"].status == "degraded"
    assert card.dimensions["technical"].status == "healthy"  # 其他維度不受牽連


def test_health_card_missing_noncore_does_not_cap():
    # macro 缺對短線技術裁定無傷：sentiment/macro 缺 = missing（非 degraded），不封頂。
    store = healthy_store()
    store.items = [e for e in store.items if e.category != "news"]
    card = _card(store)
    assert card.overall == "ok" and card.confidence_cap is None
    assert card.dimensions["sentiment"].status == "missing"


def test_pe_sanity_flags_meaningless_magnitude():
    # S6：|forward_pe| 過大（SPCX −2242x）→ warning「勿用 multiple 估值」，不再無聲發出。
    store = healthy_store()
    store.add(Evidence(category="quote", field="forward_pe", value=-2242.222,
                       source="yfinance", as_of=date.today()))
    findings = deterministic_checks(store, CFG)
    pe = [f for f in findings if f.check == "pe_sanity"]
    assert pe and all(f.severity == "warning" for f in pe)


def test_forward_pe_internal_inconsistency_is_error():
    # S6：forward_pe × forward_eps 與 last_price 嚴重偏離 → error（PE 對另一價算，疑 staleness）。
    store = healthy_store()  # last_price=100
    store.add(Evidence(category="quote", field="forward_pe", value=20.0,
                       source="yf", as_of=date.today()))
    store.add(Evidence(category="estimate", field="forward_eps", value=6.0,
                       unit="USD/share", source="yf", as_of=date.today()))  # 20×6=120 vs 100 → 20%
    found = errors(deterministic_checks(store, CFG))
    assert any(f.check == "internal_consistency" and "forward_pe" in f.message for f in found)


def test_forward_pe_consistency_passes_when_aligned():
    store = healthy_store()  # last_price=100
    store.add(Evidence(category="quote", field="forward_pe", value=20.0,
                       source="yf", as_of=date.today()))
    store.add(Evidence(category="estimate", field="forward_eps", value=5.0,
                       unit="USD/share", source="yf", as_of=date.today()))  # 20×5=100 ✓
    assert not any(f.check == "internal_consistency" and "forward_pe" in f.message
                   for f in deterministic_checks(store, CFG))


def test_health_card_fatal_blocks():
    store = healthy_store()
    store.add(Evidence(category="quote", field="last_price_intraday", value=200.0,
                       unit="USD", source="intraday", as_of=date.today()))  # vs 100 → fatal
    card = _card(store)
    assert card.overall == "blocked"
    assert card.confidence_cap is None  # blocked 不套 cap（避免被當 0% 信心結論）
    assert card.dimensions["price"].status == "fatal"
