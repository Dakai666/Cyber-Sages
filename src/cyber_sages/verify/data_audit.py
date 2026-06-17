"""Stage 2 — 資料審核閘門。

兩層：
1. 確定性檢查（程式）：必要資料齊全、新鮮度、跨來源一致性、基本 sanity。
2. LLM 稽核員：找程式抓不到的語意異常（如 EPS 與 net_income/shares 對不上）。

fatal → 管線在 stage 2 中止（分析前提已壞，後續一切無意義，產「無法分析」短報告）；
error → 管線降級（degraded mode，最終報告強制揭露）；warning → 通過但標記。

fatal 只由確定性閘門產生——LLM 稽核員（非確定性）的任何發現一律壓到 warning，
不能讓模型獨自把整場分析喊停（見 run_audit 的 clamp）。
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from cyber_sages.config import AuditConfig, Settings
from cyber_sages.data.evidence import EvidenceStore
from cyber_sages.llm.gateway import LLMGateway

# 三級嚴重度：fatal（中止）> error（降級）> warning（通過但標記）。
Severity = Literal["fatal", "error", "warning"]


class AuditFinding(BaseModel):
    severity: Severity
    check: str
    message: str
    evidence_ids: list[str] = Field(default_factory=list)


class AuditReport(BaseModel):
    findings: list[AuditFinding] = Field(default_factory=list)
    auditor_summary: str = ""

    @property
    def fatals(self) -> list[AuditFinding]:
        return [f for f in self.findings if f.severity == "fatal"]

    @property
    def errors(self) -> list[AuditFinding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def blocked(self) -> bool:
        """分析前提已壞 → 管線應在 stage 2 中止，不產出大師團/行動計畫。"""
        return len(self.fatals) > 0

    @property
    def degraded(self) -> bool:
        """核心維度受損但分析仍可進行 → 降級 + 揭露。fatal 同時意味降級。"""
        return len(self.errors) > 0 or self.blocked


# W9 — 缺漏分級表（Spec B 決議 3，模組常數，不散落在 if 裡）。
# 核心三類缺 → error → 管線降級（信心封頂 0.5）；其餘 → warning + brief 強制揭露。
# 唯一情境例外（在 _missing_severity 處理）：ETF 無發行人損益表，fundamentals 降為 warning。
CORE_CATEGORIES: tuple[str, ...] = ("quote", "history", "fundamentals")
CATEGORY_SEVERITY: dict[str, Severity] = {
    "quote": "error",
    "history": "error",
    "fundamentals": "error",
    "news": "warning",
    "chips": "warning",
    "macro": "warning",
}
# 不變式：核心類別 ≡ 降級類別（決議 3）。鎖在 import 時，未來改分級忘了同步即炸。
assert CORE_CATEGORIES == tuple(k for k, v in CATEGORY_SEVERITY.items() if v == "error")


# ---------- Spec F / S7：分維度資料健康度評分卡 ----------
# 全域「信心一律封頂 0.5」太粗魯——讀者無法判斷壞的是不是自己在乎的維度（macro 缺對短線
# 技術裁定無傷，但財報過期專打 value thesis）。把資料品質拆成五個維度逐一評級，揭露明確
# 指出壞在哪，confidence_cap 由最壞維度推導（核心維度受損才腰斬，僅周邊受損則輕罰）。

# 維度 → evidence 類別。price=當前價可信度；technical=技術面；fundamentals=基本面；
# sentiment=情緒/籌碼；macro=總經背景。
DIMENSION_CATEGORIES: dict[str, tuple[str, ...]] = {
    "price": ("quote",),
    "technical": ("history",),
    "fundamentals": ("fundamentals",),
    "sentiment": ("news", "chips"),
    "macro": ("macro",),
}
# 核心維度受損 → 信心腰斬（0.5）；僅周邊（sentiment/macro）受損 → 輕罰（0.7）。
CORE_DIMENSIONS: frozenset[str] = frozenset({"price", "technical", "fundamentals"})
_CATEGORY_DIMENSION: dict[str, str] = {
    c: dim for dim, cats in DIMENSION_CATEGORIES.items() for c in cats}


class DimensionHealth(BaseModel):
    status: Literal["healthy", "degraded", "missing", "fatal"]
    reason: str = ""
    evidence_count: int = 0
    oldest_as_of: date | None = None


class DataHealthCard(BaseModel):
    dimensions: dict[str, DimensionHealth]
    overall: Literal["ok", "degraded", "blocked"]
    # 由最壞維度推導的信心上限。None＝乾淨不封頂、或 blocked（走中止不套 cap）。降級時為
    # 0.5（核心受損）/ 0.7（僅周邊受損）。blocked 的真相看 overall，不看 cap。
    confidence_cap: float | None = None


def _finding_dimension(f: AuditFinding, store: EvidenceStore) -> str | None:
    """把一條 finding 歸到某個維度：先看其 evidence 類別，再退而求其次解析訊息中的類別字。"""
    for eid in f.evidence_ids:
        ev = next((e for e in store.items if e.id == eid), None)
        if ev and ev.category in _CATEGORY_DIMENSION:
            return _CATEGORY_DIMENSION[ev.category]
    for cat, dim in _CATEGORY_DIMENSION.items():
        if cat in f.message:  # "Missing quote data" / "macro series" / "chips 來源抓取失敗"
            return dim
    return None


def build_health_card(audit: AuditReport, store: EvidenceStore) -> DataHealthCard:
    """從 audit findings + store 攤出五維度健康度。LLM 稽核員的 warning 不降級維度
    （與全域 degraded 語意一致：只有確定性閘門的 error/fatal 才算數）。"""
    by_dim: dict[str, list[AuditFinding]] = {d: [] for d in DIMENSION_CATEGORIES}
    for f in audit.findings:
        dim = _finding_dimension(f, store)
        if dim in by_dim:
            by_dim[dim].append(f)

    dims: dict[str, DimensionHealth] = {}
    for dim, cats in DIMENSION_CATEGORIES.items():
        items = [e for e in store.items if e.category in cats]
        fs = by_dim[dim]
        if any(f.severity == "fatal" for f in fs):
            status = "fatal"
        elif any(f.severity == "error" for f in fs):
            status = "degraded"
        elif not items:
            status = "missing"  # 周邊類別缺（核心缺會產生 error → 上面已歸 degraded）
        else:
            status = "healthy"
        reason = "；".join(f.message for f in fs if f.severity in ("fatal", "error"))[:240]
        # D1：technical 缺若因 IPO/新上市，明說承認（reason 帶 N 交易日），不是「資料壞了」。
        if dim == "technical" and status == "missing":
            ipo_days = _short_history_days(store)
            if ipo_days is not None:
                reason = f"僅 {ipo_days} 個交易日歷史（疑新上市 IPO），技術面有限"
        oldest = min((e.as_of for e in items if e.as_of), default=None)
        dims[dim] = DimensionHealth(status=status, reason=reason,
                                    evidence_count=len(items), oldest_as_of=oldest)

    capped = {d for d, h in dims.items() if h.status in ("degraded", "fatal")}
    if any(dims[d].status == "fatal" for d in dims):
        # blocked 不到合成、不套 cap；給 0.0 會讓下游誤把「沒有結論」當「0% 信心結論」，
        # 故 cap=None（overall=="blocked" 才是 blocked 的真相來源）。
        overall, cap = "blocked", None
    elif capped:
        # D3：最壞維度決定——核心受損腰斬，僅周邊受損輕罰。
        overall = "degraded"
        cap = 0.5 if capped & CORE_DIMENSIONS else 0.7
    else:
        overall, cap = "ok", None
    return DataHealthCard(dimensions=dims, overall=overall, confidence_cap=cap)


def _etf_relaxed(category: str, store: EvidenceStore) -> bool:
    """ETF 無發行人損益表：fundamentals 缺屬預期口徑，降級與訊息都據此放寬。
    ETF 例外的唯一真相來源——severity 與 message 都查它，不在兩處各判一次。"""
    return category == "fundamentals" and store.instrument == "etf"


def _short_history_days(store: EvidenceStore) -> int | None:
    """有 IPO/新上市標記（trading_days_available）則回交易日數，否則 None。"""
    for e in store.items:
        if e.category == "profile" and e.field == "trading_days_available":
            return int(e.value)
    return None


def _missing_severity(category: str, store: EvidenceStore) -> Severity:
    """某類別缺失/抓取失敗時的嚴重度（查分級表，ETF fundamentals 例外降 warning）。"""
    if _etf_relaxed(category, store):
        return "warning"
    return CATEGORY_SEVERITY.get(category, "warning")


def deterministic_checks(store: EvidenceStore, cfg: AuditConfig) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    today = date.today()

    def field_evs(category: str, field: str):
        return [e for e in store.items if e.category == category and e.field == field]

    # 1. 必要資料齊全。嚴重度查 CATEGORY_SEVERITY（決議 3：核心三類缺即降級）；
    #    財報欄位名隨市場不同（美股年報 / 台股季報口徑）。
    if store.market == "TW":
        fundamentals_fields = ["revenue_latest_quarter", "net_income_latest_quarter", "eps_latest_quarter"]
    else:
        fundamentals_fields = ["revenue_annual", "net_income_annual"]
    required = [
        ("quote", ["last_price", "latest_close"], "no usable price"),
        ("history", ["sma_20"], "no price history / indicators"),
        ("news", None, "no recent news"),
        ("fundamentals", fundamentals_fields, "no first-hand financials"),
    ]
    # 台股籌碼面：缺三大法人買賣超只是警示（個股當日可能無法人進出資料）
    if store.market == "TW":
        required.append(("chips", None, "no institutional/margin (籌碼) data"))
    for category, fields, msg in required:
        evs = store.by_category(category)  # type: ignore[arg-type]
        if fields is not None:
            evs = [e for e in evs if e.field in fields]
        if not evs:
            severity = _missing_severity(category, store)
            message = f"Missing {category} data: {msg}"
            # ETF fundamentals 例外的 severity 與訊息都源自 _etf_relaxed，集中一處
            if _etf_relaxed(category, store):
                message = "Missing fundamentals data: ETF has no issuer financials " \
                          "(估值改看技術/籌碼/折溢價)"
            # D1：history 缺若伴隨 IPO/新上市標記，屬特例——降為 warning + 明說承認，不當資料錯誤。
            days = _short_history_days(store)
            if category == "history" and days is not None:
                severity = "warning"
                message = f"技術面有限：僅 {days} 個交易日歷史（疑新上市 IPO），" \
                          "不足以算技術指標——仍可依基本面/新聞/情緒分析"
            findings.append(AuditFinding(
                severity=severity, check="completeness", message=message,
            ))

    # 2. 新鮮度
    for ev in field_evs("quote", "latest_close"):
        if ev.as_of and (today - ev.as_of).days > cfg.max_quote_age_days:
            findings.append(AuditFinding(
                severity="error", check="freshness",
                message=f"Quote is {(today - ev.as_of).days} days old (max {cfg.max_quote_age_days})",
                evidence_ids=[ev.id],
            ))
    fund = store.by_category("fundamentals")
    if fund:
        newest = max((e.as_of for e in fund if e.as_of), default=None)
        if newest and (today - newest).days > cfg.max_fundamentals_age_days:
            findings.append(AuditFinding(
                severity="warning", check="freshness",
                message=f"Newest financial statement is {(today - newest).days} days old",
            ))
        # 逐欄位過期檢查：單一欄位嚴重過時（如公司換 XBRL 標籤導致撈到舊值）
        # 不能被同類其他新鮮欄位掩護。
        for e in fund:
            if e.as_of and (today - e.as_of).days > cfg.max_fundamentals_stale_field_days:
                findings.append(AuditFinding(
                    severity="error", check="freshness",
                    message=f"Field {e.field} is {(today - e.as_of).days} days stale "
                            f"({e.as_of}) — likely wrong/legacy source tag, must not be used",
                    evidence_ids=[e.id],
                ))

    # 2b. 總經新鮮度：macro 多為月頻，最新一筆過期則警示（不降級，總經本就落後）
    macro = store.by_category("macro")
    if macro:
        newest_macro = max((e.as_of for e in macro if e.as_of), default=None)
        if newest_macro and (today - newest_macro).days > cfg.max_macro_age_days:
            findings.append(AuditFinding(
                severity="warning", check="freshness",
                message=f"Newest macro series is {(today - newest_macro).days} days old "
                        f"(max {cfg.max_macro_age_days})",
            ))

    # 3. 跨來源價格一致性——比「同一時點的兩條當前價讀數」（fast_info 報價 vs 盤中最後成交）。
    #    舊版比 last_price(當前) vs latest_close(昨收)＝不同時點的量，正常日內波動 >2% 就會
    #    誤報；SPCX 案例更是兩條 Yahoo 路徑一起 stale 成同值（192.5）而漏接。改比兩條當前價：
    #    背離＝其一已 stale/錯，當前價本身不可信 → fatal（分析前提已壞，見 Spec F S2）。
    live = field_evs("quote", "last_price")
    intraday = field_evs("quote", "last_price_intraday")
    if live and intraday:
        a, b = float(live[0].value), float(intraday[0].value)
        if b > 0:
            div = abs(a - b) / b * 100
            if div > cfg.max_price_divergence_pct:
                findings.append(AuditFinding(
                    severity="fatal", check="cross_source",
                    message=f"當前價跨源背離 {div:.1f}%：fast_info {a} vs 盤中最後成交 {b} "
                            f"(max {cfg.max_price_divergence_pct}%) — 其一已 stale，當前價不可信",
                    evidence_ids=[live[0].id, intraday[0].id],
                ))
    elif live:
        # 缺獨立的盤中讀數（盤前/週末/抓取失敗）→ 跨源把關「沒跑」，不是「通過」。明說出來，
        # 否則讀者與 judge 會把「無 finding」誤讀成「跨源一致」。掛 last_price 的 id 讓它歸到
        # health_card 的 price 維度揭露（warning 不降級，但讀者看得到把關缺席）。
        findings.append(AuditFinding(
            severity="warning", check="cross_source",
            message="跨源檢查跳過：缺盤中最後成交讀數，當前價僅 fast_info 單一來源（無獨立驗證）",
            evidence_ids=[live[0].id],
        ))

    # 4. sanity：價格類數值必須為正。非正值報價＝分析前提已壞（不是「品質差一點」），
    #    後續整套估值/技術/行動計畫都建立在這個錯數上，故 fatal → 中止，不降級硬出報告。
    for ev in store.by_category("quote"):
        if ev.field in ("last_price", "latest_close") and float(ev.value) <= 0:
            findings.append(AuditFinding(
                severity="fatal", check="sanity",
                message=f"Non-positive price in {ev.id}: {ev.value} — 報價無效，無法分析",
                evidence_ids=[ev.id],
            ))

    # 5. 內部一致性：EPS ≈ net_income / shares（寬容差，股數會變動）
    eps = field_evs("fundamentals", "eps_diluted_annual")
    ni = field_evs("fundamentals", "net_income_annual")
    sh = field_evs("fundamentals", "shares_outstanding")
    if eps and ni and sh and float(sh[0].value) > 0:
        implied = float(ni[0].value) / float(sh[0].value)
        reported = float(eps[0].value)
        if reported > 0 and abs(implied - reported) / reported > 0.25:
            findings.append(AuditFinding(
                severity="warning", check="internal_consistency",
                message=f"EPS {reported} vs implied net_income/shares {implied:.2f} "
                        "diverge >25% (share count drift or data issue)",
                evidence_ids=[eps[0].id, ni[0].id, sh[0].id],
            ))

    # 6. W2 — US trailing P/E cross-check：yfinance 的 trailing_pe 是它自算的二手值，
    # 用 SEC 第一手 EPS 反算 implied P/E 比對，偏離過大代表二手值可疑 → error。
    # 僅 US：trailing_pe 由 yfinance info 提供，TW 端不發此欄位。
    # issue #19：改用 eps_ttm（最近四季合計）→ 與 yfinance trailing 同口徑（TTM-vs-TTM），
    # 閾值收回嚴格 10%。eps_ttm 缺（季度不足）時不退回年報——寧可不比，也不在錯口徑上誤報。
    if store.market == "US":
        pe = field_evs("quote", "trailing_pe")
        eps_ttm = field_evs("fundamentals", "eps_ttm")
        price = field_evs("quote", "last_price") or field_evs("quote", "latest_close")
        if pe and eps_ttm and price and float(eps_ttm[0].value) > 0:
            yf_pe = float(pe[0].value)
            implied_pe = float(price[0].value) / float(eps_ttm[0].value)
            if yf_pe > 0:
                # 分母用 yf_pe（非 max）：度量「yfinance 二手值偏離 SEC 第一手真相的相對誤差」，
                # 刻意不對稱——與既有 internal_consistency 的 |implied-reported|/reported 同風格。
                div = abs(yf_pe - implied_pe) / yf_pe * 100
                if div > cfg.max_pe_divergence_pct:
                    findings.append(AuditFinding(
                        severity="error", check="cross_source",
                        message=f"P/E divergence {div:.0f}%: yfinance trailing P/E {yf_pe:.1f} "
                                f"vs SEC-implied (price/eps_ttm) {implied_pe:.1f} "
                                f"(max {cfg.max_pe_divergence_pct:.0f}%; TTM-vs-TTM 口徑對齊)",
                        evidence_ids=[pe[0].id, eps_ttm[0].id, price[0].id],
                    ))

        # 7. S6 — forward P/E 內部矛盾 sanity（US，同源 yfinance/estimate 的三角一致性）。
        # (a) |P/E| 過大：EPS≈0 使比率爆量（SPCX forward_pe −2242x），此比率已無估值意義 →
        #     warning 明示「勿用 multiple 估值」，避免分析師/讀者把它當有效估值錨點。
        for field in ("trailing_pe", "forward_pe"):
            for ev in field_evs("quote", field):
                if abs(float(ev.value)) > cfg.max_abs_pe_meaningful:
                    findings.append(AuditFinding(
                        severity="warning", check="pe_sanity",
                        message=f"{field} {ev.value} 絕對值過大（EPS≈0）—此比率無估值意義，"
                                "勿用 P/E multiple 估值",
                        evidence_ids=[ev.id],
                    ))
        # (b) forward_pe × forward_eps 應 ≈ last_price。偏離大＝PE 是對「另一個價」算的
        #     （常是 staleness：fast_info 落後而 PE 用了真實當前價）→ error（資料內部矛盾）。
        fpe = field_evs("quote", "forward_pe")
        feps = field_evs("estimate", "forward_eps")
        lp = field_evs("quote", "last_price")
        if fpe and feps and lp and float(lp[0].value) > 0:
            implied = float(fpe[0].value) * float(feps[0].value)
            div = abs(implied - float(lp[0].value)) / float(lp[0].value) * 100
            if div > cfg.max_forward_pe_consistency_pct:
                findings.append(AuditFinding(
                    severity="error", check="internal_consistency",
                    message=f"forward_pe×forward_eps={implied:.2f} 與 last_price={lp[0].value} "
                            f"偏離 {div:.0f}%（max {cfg.max_forward_pe_consistency_pct:.0f}%）"
                            "—P/E 似對另一價計算，可能報價 stale 或來源錯配",
                    evidence_ids=[fpe[0].id, feps[0].id, lp[0].id],
                ))
    return findings


class AuditorOutput(BaseModel):
    findings: list[AuditFinding] = Field(default_factory=list)
    summary: str


AUDITOR_SYSTEM = """\
You are a meticulous financial data auditor. You receive a raw evidence dump for one stock.
Today's date is {today}. This system runs on live data, so timestamps at or near {today}
are CURRENT and correct.

Your ONLY job is to find data quality problems BEFORE analysts use this data:
- values that contradict each other across sources
- magnitudes that look implausible for this company
- internal arithmetic that does not add up (e.g. EPS vs net_income/shares)
- unit confusion (e.g. a value that looks like millions recorded as raw dollars)

CRITICAL — do NOT do any of the following:
- Do NOT flag data as fake, simulated, or erroneous merely because dates look recent or
  fall after your training cutoff. You have no reliable internal sense of "today"; the
  authoritative current date is {today} as stated above.
- Do NOT report staleness/freshness or missing-data problems — those are checked
  deterministically by separate code. Focus only on cross-field/cross-source consistency.
- For `estimate`-category values (forward_eps, target_mean_price, *_growth_est, analyst_*):
  do NOT flag forward-vs-actual divergence — they are forward-looking analyst consensus, so
  forward_eps > trailing EPS or a target price above spot is expected, not an error. You MAY
  still flag genuine estimate problems: negative/zero target price, an empty recommendation,
  zero contributing analysts, or an extreme outlier (e.g. target ~10x spot).
- `reference`-category values (industry_pe_*, market_pe_trailing) are external industry/market
  aggregates, NOT this company's data: do NOT flag them as inconsistent with the company's own
  P/E or fundamentals (a stock trading away from its industry multiple is a finding for analysts,
  not a data error).

You do NOT give investment opinions. Only report genuine data problems; an empty findings
list is the correct answer for clean data. Reference evidence ids."""


async def run_audit(
    store: EvidenceStore, settings: Settings, gateway: LLMGateway,
    *, fetch_failures: dict[str, str] | None = None,
) -> AuditReport:
    findings = deterministic_checks(store, settings.audit)
    # W9 — collector 抓取失敗顯式入帳（completeness 只知「缺」，這裡記「為何缺」）。
    # 嚴重度同分級表：核心類別抓取失敗即降級，而非靜默少一塊資料。
    # 若 completeness 已對同一類別記了「缺」，把錯因併入該條，避免兩條 ~重複 finding（#30）。
    for category, err in (fetch_failures or {}).items():
        reason = err.split("\n")[0][:200]  # 取首行並截斷：避免多行/超長錯誤破壞 brief 渲染
        existing = next(
            (f for f in findings if f.check == "completeness"
             and f.message.startswith(f"Missing {category} data:")), None)
        if existing:
            existing.message += f"（來源抓取失敗：{reason}）"
        else:
            findings.append(AuditFinding(
                severity=_missing_severity(category, store), check="collector_error",
                message=f"{category} 來源抓取失敗：{reason}",
            ))
    summary = ""
    try:
        out = await gateway.structured(
            "data_auditor",
            # .replace 而非 .format：prompt 未來若含 JSON 範例的字面 {}，.format 會炸
            system=AUDITOR_SYSTEM.replace("{today}", date.today().isoformat()),
            prompt=f"Ticker: {store.ticker}\n\nEvidence dump:\n{store.digest()}",
            schema=AuditorOutput,
        )
        # LLM 稽核員只當「語意顧問」：其發現一律壓到 warning，不獨自觸發降級或中止。
        # 降級/中止保留給可信的確定性閘門，避免非確定性的稽核員在相同資料上忽高忽低地
        # 把 conviction 砍半、甚至誤判 fatal 把整場喊停（曾因「未來日期」幻覺誤判全表）。
        for f in out.findings:
            findings.append(f if f.severity == "warning"
                            else f.model_copy(update={"severity": "warning"}))
        summary = out.summary
    except Exception as e:
        findings.append(AuditFinding(
            severity="warning", check="auditor_agent",
            message=f"LLM auditor unavailable ({e}); only deterministic checks ran",
        ))
    return AuditReport(findings=findings, auditor_summary=summary)
