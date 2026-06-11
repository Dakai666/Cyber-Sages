from cyber_sages.config import CitationConfig
from cyber_sages.data.evidence import Evidence, EvidenceStore
from cyber_sages.verify.citation_check import (
    Claim,
    check_claim,
    check_claims,
    extract_meaningful_numbers,
)

CFG = CitationConfig(numeric_tolerance_pct=1.0)


def make_store() -> EvidenceStore:
    store = EvidenceStore(ticker="TEST")
    store.add(Evidence(category="fundamentals", field="revenue_annual",
                       value=416_161_000_000, unit="USD", source="SEC EDGAR"))
    store.add(Evidence(category="quote", field="trailing_pe",
                       value=35.343, source="yfinance"))
    store.add(Evidence(category="history", field="return_1m_pct",
                       value=5.2, unit="%", source="computed"))
    return store


def test_number_extraction_normalizes_suffixes():
    nums = extract_meaningful_numbers("revenue of $416.2B, P/E 35.3x, up 5.2%")
    assert nums == [416.2e9, 35.3, 5.2]


def test_bare_integers_ignored():
    # 年份、SMA 視窗、排名不該被當成需驗證的數字
    assert extract_meaningful_numbers("In 2025 the 50-day SMA crossed; top 3 player") == []


def test_accurate_claim_passes():
    store = make_store()
    claim = Claim(text="FY revenue was $416.2B", evidence_ids=["E001"])
    assert check_claim(claim, store, CFG).verified


def test_tampered_number_caught():
    store = make_store()
    claim = Claim(text="FY revenue was $520.0B", evidence_ids=["E001"])
    result = check_claim(claim, store, CFG)
    assert not result.verified
    assert "not found" in result.reason


def test_nonexistent_evidence_id_caught():
    store = make_store()
    claim = Claim(text="P/E is 35.3x", evidence_ids=["E999"])
    result = check_claim(claim, store, CFG)
    assert not result.verified
    assert "nonexistent" in result.reason


def test_uncited_claim_fails():
    store = make_store()
    result = check_claim(Claim(text="Margins look strong", evidence_ids=[]), store, CFG)
    assert not result.verified


def test_qualitative_claim_with_citation_passes():
    store = make_store()
    claim = Claim(text="Momentum is positive", evidence_ids=["E003"])
    assert check_claim(claim, store, CFG).verified


def test_negative_numbers_extracted_with_sign():
    # smoke test 真實案例：MACD -2.77 曾被抓成 2.77 造成假陽性
    assert extract_meaningful_numbers("MACD 柱狀圖為 -2.77，1 個月報酬 -0.73%") == [-2.77, -0.73]


def test_chinese_magnitude_suffixes():
    # 市值 $4.283兆 / Waymo 2.2億美元收購
    import pytest
    assert extract_meaningful_numbers("市值約 $4.283兆，交易金額 2.2億 美元") == pytest.approx([4.283e12, 2.2e8])


def test_derived_ratio_verified():
    # ROE = net_income / equity 的正確算術不該被誤判
    store = EvidenceStore(ticker="TEST")
    store.add(Evidence(category="fundamentals", field="net_income_annual",
                       value=112_010_000_000, unit="USD", source="edgar"))
    store.add(Evidence(category="fundamentals", field="stockholders_equity",
                       value=73_733_000_000, unit="USD", source="edgar"))
    claim = Claim(text="ROE 約 1.52 倍，極高資本報酬率", evidence_ids=["E001", "E002"])
    assert check_claim(claim, store, CFG).verified


def test_derived_margin_pct_verified():
    store = EvidenceStore(ticker="TEST")
    store.add(Evidence(category="fundamentals", field="net_income_annual",
                       value=112_010_000_000, unit="USD", source="edgar"))
    store.add(Evidence(category="fundamentals", field="revenue_annual",
                       value=416_161_000_000, unit="USD", source="edgar"))
    claim = Claim(text="年度淨利率約 26.9%", evidence_ids=["E001", "E002"])
    assert check_claim(claim, store, CFG).verified


def test_price_gap_difference_verified():
    # 台股案例：股價在 SMA200 之上的「價差（元）」是兩引用值的單純差，不該被誤判
    store = EvidenceStore(ticker="2330", market="TW")
    store.add(Evidence(category="quote", field="latest_close", value=2250.0,
                       unit="TWD", source="FinMind"))
    store.add(Evidence(category="history", field="sma_200", value=1691.35,
                       unit="TWD", source="computed"))
    claim = Claim(text="股價 2250 高於 SMA200 1691.35，乖離 558.65 TWD（溢價 33.0%）",
                  evidence_ids=["E001", "E002"])
    assert check_claim(claim, store, CFG).verified


def test_signed_chip_flow_magnitude_verified():
    # 三大法人買賣超證據帶負號（賣超），中文以「賣超」表方向、數字寫絕對值
    store = EvidenceStore(ticker="2330", market="TW")
    store.add(Evidence(category="chips", field="foreign_net_buy", value=-4_904_284,
                       unit="shares", source="FinMind"))
    claim = Claim(text="外資賣超約 490 萬股", evidence_ids=["E001"])
    assert check_claim(claim, store, CFG).verified


def test_macro_unit_scale_thousands_verified():
    # 總經 nonfarm 以「千人」為單位存 172，分析師展開成 172K（172000 人）
    store = EvidenceStore(ticker="AAPL")
    store.add(Evidence(category="macro", field="nonfarm_payrolls_mom_change",
                       value=172.0, unit="thousands of persons", source="FRED PAYEMS"))
    claim = Claim(text="非農就業月增 172K", evidence_ids=["E001"])
    assert check_claim(claim, store, CFG).verified


def test_tampered_number_still_caught_with_richer_candidates():
    # 放寬候選值後，仍須擋下無法由任一引用值推導的捏造數字
    store = make_store()
    claim = Claim(text="FY revenue was $520.0B", evidence_ids=["E001"])
    assert not check_claim(claim, store, CFG).verified


def test_number_inside_string_evidence_verified():
    # 新聞類 evidence 的 value 是字串，claim 引用其中的數字也要能驗
    store = EvidenceStore(ticker="TEST")
    store.add(Evidence(category="news", field="headline_1",
                       value="Waymo buys Apple test site for $220M", source="news"))
    claim = Claim(text="Waymo 以 2.2億 美元收購測試場地", evidence_ids=["E001"])
    assert check_claim(claim, store, CFG).verified


def test_tampered_ratio_still_caught():
    # 衍生值比對不能讓竄改的比率矇混過關
    store = EvidenceStore(ticker="TEST")
    store.add(Evidence(category="fundamentals", field="net_income_annual",
                       value=112_010_000_000, unit="USD", source="edgar"))
    store.add(Evidence(category="fundamentals", field="revenue_annual",
                       value=416_161_000_000, unit="USD", source="edgar"))
    claim = Claim(text="年度淨利率高達 45.0%", evidence_ids=["E001", "E002"])
    assert not check_claim(claim, store, CFG).verified


def test_report_aggregation():
    store = make_store()
    report = check_claims(
        [
            Claim(text="revenue $416.2B", evidence_ids=["E001"]),
            Claim(text="revenue $999B", evidence_ids=["E001"]),
        ],
        store, CFG,
    )
    assert len(report.unverified) == 1
    assert not report.all_verified
