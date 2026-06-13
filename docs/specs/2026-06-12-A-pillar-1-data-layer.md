# Spec A — Pillar 1 資料層擴充

**Status**: accepted（2026-06-13 決議定案，見文末）
**Date**: 2026-06-12
**Dependencies**: Phase 0 護欄（issue #5 TW e2e 測試先行）
**範圍變更**: 2026-06-13 併入 issue #4（TW 籌碼趨勢欄位——同屬「確定性衍生欄位」哲學）

## 背景

Cyber-Sages 對「第一手資料 + 確定性計算」的承諾，是整個反幻覺哲學的起點。但 audit 發現有兩條裂縫：(1) 部分大師的招牌焦點**根本沒對應 evidence**——Damodaran 拿不到 WACC、Lynch 拿不到 forward growth、Wood 拿不到 5 年預測，這 3 位大師的 thesis 必然是 LLM 自由發揮；(2) US 估值分析師依賴 yfinance `info` 的 P/E 與 dividend yield，但這是 yfinance 自己算的二手值，audit 沒做 cross-check。

這條 spec 專注把 evidence 池擴充到「至少每個 persona 焦點都拿得到 1 個量化錨點」，並修補 US 估值的二手鏈。

## 涵蓋的 Gaps

- **W1**（Pillar 1+2 同步）：3 個 persona 缺資料
  - Damodaran：缺 WACC input（debt / equity ratio）、可比公司 multiples
  - Lynch：缺 forward EPS / analyst consensus
  - Wood：缺 5 年預測、TAM proxy（產業 R&D spend / 顛覆性產品營收 YoY）
  - 其他需要的：Graham 缺 working capital、Burry 缺 short interest / debt 結構、Munger 缺 management comp
- **W2**：US P/E cross-check（`src/cyber_sages/data/us_stocks.py:87-93` vs SEC EPS 算回來）
- **W6**：所有 `httpx.AsyncClient` 呼叫加 retry/backoff（`us_stocks.py:129,219` + `tw_stocks.py:61` + `macro.py:42`）
- **W10**：TW 波動率年化用 245 而非 252（`src/cyber_sages/data/indicators.py:41`）
- **測試覆蓋**：`us_stocks.py`（265 行）、`indicators.py`（RSI/MACD/波動率計算）、`finmind.py`（REST 客戶端）

## 範圍

### In scope

- 補欄位（優先序見決議 1，P0 清單由 Spec E 的 persona skill `requires` 反推）
- US P/E 確定性 cross-check（`audit.py` 加 derived P/E 比對）
- 全 provider retry/backoff（指數退避 + jitter，max 3 retry）
- `indicators.py` 245 天校準
- `tests/test_us_stocks.py`、`tests/test_indicators.py`、`tests/test_finmind.py` 新增
- **Issue #4 併入**（PR #20 完成）：法人 5/20 日累計（`foreign_net_buy_5d/20d`、
  `institutional_net_buy_5d`）、融資券趨勢（`margin_balance_change_5d_pct`、
  `short_balance_change_5d_pct`）、fetch 視窗法人 `days_ago(10)` → `days_ago(35)`、
  融資券 → `days_ago(12)`。
  > ⚠ **修正（2026-06-13，PR #20）**：原寫「→ `days_ago(3)`（省 70% 配額）」與「20 日累計」
  > **互斥**——3 天資料算不出 20 日累計。20 日累計是 issue #4 自評 P2 的核心理由（敘事性
  > 結論的方向佐證），實質價值 > 配額節省，故**取 A 段累計、捨 C 段縮窗**，籌碼端視窗反而
  > 加長。無 token 用戶的 chips 配額策略（限流時降級 / user toggle）若要做另開 follow-up。

### Out of scope

- 跨資產（crypto / A 股 / 港股）—— 留給未來
- LLM 對 P/E 計算的判斷—— 純確定性 cross-check
- 欄位 schema 重設計（沿用現有 `Evidence(category=..., field=...)` 模式）

## 驗收條件（草案）

- [ ] 至少 8/10 persona 焦點各有 ≥ 1 個量化 evidence
- [x] US `info.P/E` vs SEC EPS 反算的 implied P/E 偏離超閾值 → audit error（PR #18 完成）
  > 閾值實作為 **25%**（非草案的 10%）：yfinance trailing 用 TTM、implied 用 FY 年報，
  > 口徑差使高成長股自然偏離；補算 US `eps_ttm` 做 TTM-vs-TTM 後可收回 10%（follow-up #19）。
- [ ] 所有 data fetch 失敗時 retry 3 次 backoff，3 次都失敗才 raise / warn
- [ ] TW run 的 `volatility_30d_annualized_pct` 改用 245 校準
- [ ] `us_stocks.py` / `indicators.py` / `finmind.py` 各自有專屬測試檔
- [ ] 48 → 新測試數 ≥ 70

## 決議（2026-06-13，DK 授權按最優解定案）

1. **補欄位優先序**：P0 = Spec E persona skills 的 `requires` 聯集——
   `debt_to_equity`、`interest_coverage`、`capex`、`depreciation_amortization`
   （owner earnings 輸入）、`free_cash_flow`、`working_capital`、`net_net_value`、
   `roe_5y_avg`、`gross_margin_trend_5y`、`peg` 輸入（trailing growth）。
   P1 = forward EPS consensus、產業 multiples。P2 = TAM proxy（R&D spend 等）。
   原則：**先餵飽 skills（確定性計算），再餵敘事性欄位**。
2. **sector peer multiples**：用 **Damodaran NYU Stern 公開資料集**（產業層級
   multiples，免費 CSV、年更、出自大師本人之手——與 persona 哲學同源）。
   個股級 peers 比對降為 P2，不用 FMP（多一個付費二手依賴不值得）。
3. **short interest**：US 用 yfinance/FINRA short interest（標 second-hand）；
   TW 無同等品，用**融券餘額 + 借券賣出餘額**（FinMind，第一手）當 proxy，
   evidence note 明示「proxy，非 short interest」。不可得時標缺失、不降級
   （chips 類非核心類別）。
4. **forward EPS**：用 analyst consensus（yfinance/Finnhub），**新增 evidence
   category `estimate`**——cite-check 與 audit 把 estimate 當「預估值」處理
   （可引用、不做 freshness error、brief 中與事實欄位視覺區隔）。公司自家指引
   留 P2（first-hand 但 bias，需配套揭露機制才上）。
5. **working capital 定義**：`working_capital = current_assets - current_liabilities`
   （標準定義，audit / Damodaran 用）；另出 `net_net_value = current_assets -
   total_liabilities`（Graham 專用，給其 skill 求值）。兩者都是確定性計算欄位。
6. **retry/backoff 不分層**：統一 3 次指數退避 + jitter（PR #16 完成），不引入分層
   budget 的複雜度。
   > ⚠ **修正（PR #20）**：原寫「FinMind 配額壓力靠 issue #4 的縮窗解決」已不成立——
   > issue #4 的 20 日累計反而需要加長籌碼視窗（見 in-scope 修正）。配額節省未在本 Phase
   > 達成，待後續 follow-up。

## 相關檔案

- `src/cyber_sages/data/us_stocks.py`（全檔, 265 行）
- `src/cyber_sages/data/tw_stocks.py`（全檔, 349 行）
- `src/cyber_sages/data/macro.py`（全檔, 91 行）
- `src/cyber_sages/data/finmind.py`（全檔, 50 行）
- `src/cyber_sages/data/indicators.py:41`（年化天數）
- `src/cyber_sages/verify/data_audit.py`（加 derived P/E check）
- `tests/`（新增 test_us_stocks.py / test_indicators.py / test_finmind.py）

## 參考

- 2026-06-12 全專案 audit 紀錄
- README 34 行（已自承 yfinance P/E 為二手值的歷史 bug）
- Issue #1（已併入 PR #3）：引用驗證容錯
