# Cyber-Sages Roadmap

**Revision**: 2026-06-14（v5 — Phase 1+2 follow-up 全數清理（僅 #25 deferred），main 175 passed；下一步 Phase 3 / Spec E1）

## 願景

> 「良好正確無幻覺的消息來源, 良好的分析管道」 + 「健全有意義的大師陪審團」
> ——有此基礎後, 提供各類情境的問題分析, 以及配套的流程
> ——目前先專注金融股票分析

### 兩條鐵律（DK 2026-06-13 定調）

1. **根基先行**：資料來源得正確、分析得有意義，後續燒 tokens 給專家進一步討論才有用。
   → Pillar 1（資料層 + 管線硬化）**全部**排在專家升級之前。
2. **專家要有內涵，不是角色扮演**：單純 `personas/*.yaml` 不足。每位專家須有
   獨特視角與思想體系、**內建的 SOP 與 workflow、獨特輕量技能**，並能**針對性地
   處理前面階段獲取的資料與數據**。
   → persona 從「yaml 語氣檔」升級為「**Persona Pack**」（身分 + 硬規則 DSL +
   決策 SOP + 確定性 skill 模組），由 **Sage Runtime** 執行、由 **Cyber-Nüwa**
   蒸餾產出。見 Spec E。

## 現況摘要

完整 audit 見對話紀錄（2026-06-12 全專案審查）。摘要：

### 6 個不能動的隱藏強項

1. TTM 確定性計算 `src/cyber_sages/data/tw_stocks.py:179-201`
2. LLM auditor 強制降為 warning `src/cyber_sages/verify/data_audit.py:203-205`
3. 逐欄位 freshness 檢查 `src/cyber_sages/verify/data_audit.py:99-106`
4. Citation 接受衍生值 + 千分位+小數+量級 regex `src/cyber_sages/verify/citation_check.py:47-103`
5. Outlier 強制論點級反駁 `src/cyber_sages/agents/debate.py:124-142`
6. Provider-agnostic gateway `src/cyber_sages/llm/gateway.py:90-138`

### 10 個 Pillar 1 結構性弱點（W1–W10）

| # | Gap | 影響 | Spec |
|---|---|---|---|
| W1 | 3 個 persona 招牌焦點缺資料 | Pillar 1+2 同步受傷 | A（**✅ Phase 1 完成**，PR #18/#21/#23/#24） |
| W2 | US P/E 是 yfinance 二手值, audit 沒 cross-check | US 估值每 run 受影響 | A（**✅ Phase 1 完成**，PR #18；閾值已收嚴格 10%，補 `eps_ttm` 後 TTM-vs-TTM，PR #35） |
| W3 | Council prompt cache 沒用 | 10x token 浪費 | B（**✅ Phase 0 完成**，PR #13；dormant 待 sage 上 Anthropic） |
| W4 | cite-check 笛卡兒積過寬, 偽造可過 | 反幻覺閘門失效 | B |
| W5 | 6 處 silent exception | schema 變更 = 整欄消失 | B（**✅ Phase 0 完成**，PR #12） |
| W6 | 沒 retry/backoff | 高流量時段 run 不穩 | A（**✅ Phase 1 完成**，PR #16；Retry-After + yfinance timeout 已補，PR #36） |
| W7 | Chief thesis 整段沒 cite-check | brief 主體是 Pillar 1 唯一未驗證 | B |
| W8 | macro 過期 4 個月只 warning | 巨集分析師拿舊資料 | B |
| W9 | 部分 collector 失敗不觸發降級 | 髒資料當滿資料 | B |
| W10 | 252 天年化用於 TW | 略高估 TW 波動率 | A（**✅ Phase 1 完成**，PR #16） |
| — | 測試覆蓋洞（us_stocks / indicators / gateway / macro / finmind / report） | 後續改動無保護 | A + B（A 部分：✅ us_stocks / indicators / finmind / estimates / damodaran，PR #16/#18/#21/#24） |

### 7 個 Pillar 2 結構性弱點（P1–P7）

| # | Gap | 影響 | Spec |
|---|---|---|---|
| P1 | persona 只有語氣沒有行為規則 | mode collapse 溫床 | **E**（Persona Pack） |
| P2 | Council 同 model 統計稀釋不成立 | 10 個相似答案 | C |
| P3 | Debate 不對稱（bull 看不到 bear） | 結構性偏袒空方 | C |
| P4 | Chief + Judge prompt 不要求 evidence id | 裁定 / brief 可瞎掰 | D |
| P5 | Risk officer 只能扣不能加 | 不對稱 | D |
| P6 | Outlier 強制逐點反駁只在敗方有 outlier 時觸發 | 強共識時少數派無保障 | C |
| P7 | 4 個 neutral 大師不被視為 outlier | 中性判斷丟失 | C |

## Spec 全覽

| Spec | 主題 | 涵蓋 | 依賴 |
|---|---|---|---|
| A | Pillar 1 資料層擴充 | W1, W2, W6, W10 + issue #4 + 測試 | Phase 0 護欄 |
| B | Pillar 1 管線硬化 | W4, W7, W8, W9（W3/W5 前置） | A |
| C | Pillar 2 陪審團結構 | P2, P3, P6, P7（P1 移入 E） | A + E1 |
| D | Pillar 2 決策結構 | P4, P5 | A + C |
| **E** | **Sage Runtime + Cyber-Nüwa 蒸餾引擎** | P1 + issue #2 全部 | E1 依賴 A+B；E2 依賴 C |

## 開發 Phase（正式計劃）

> 順序原則：鐵律 1 ⇒ Phase 0–2 把 Pillar 1 做完；鐵律 2 ⇒ Phase 3 起建專家內涵。
> 每個 Phase 一條（或數條小）feature branch + PR，驗收條件見對應 spec。

### Phase 0 — 護欄與速贏 ✅ **全數完成（2026-06-13）**

小、低風險、互相獨立。6 項全部合併進 `main`（測試基線：main **91 passed**）：

1. ✅ **Issue #5**：TW 端到端 pipeline 整合測試（2330 個股 + 0050 ETF）— PR #11
2. ✅ **Issue #7**：run 輸出標 commit hash — PR #11
3. ✅ **Issue #6**：TTM_FIELDS 模組常數化 / `_AuditorOutput`→`AuditorOutput` 公開 /
   ETF profile + fundamentals 短路測試 / `get_fundamentals` 改用 `is_tw_etf` — PR #12
4. ✅ **Issue #8**：MacroProvider 統一協議契約——**實際採 B 路徑（非原列 A）**。A 不成立：
   macro 市場無關（`get_macro` 不綁 ticker、全市場抓一次），塞進個股協議只會讓每個
   provider 多一個 no-op 方法、仍需獨立 FRED 源。改比照 `ChipsProvider` 立獨立
   `runtime_checkable` 的 `MacroProvider` 協議；具體類別 `MacroProvider`→`FredMacroProvider`；
   新增 `make_macro_provider()` 工廠；pipeline chips 偵測改 `isinstance` — PR #14
5. ✅ **B-W3 前置**：Council prompt cache——**原描述「shared_prompt 尾端加 cache_control」
   經查證無法命中快取**（cache 是前綴比對 `tools→system→messages`，各異的 system 早於共享
   user prompt 會一併失效）。實際做法相反：shared evidence 當 cached **system** prefix、
   persona 接其後（gateway 新增 `cache_prefix` 參數 + provider 支援快取時先暖一位再 fan-out）。
   ⚠ **目前 dormant**：`sage` 角色在 MiniMax（`features` 無 cache_control），此為 cache-ready
   前置，≥60% token 節省要等 `sage` 改用 Anthropic provider 才實際生效 — PR #13
   （Spec B 決議 item 4 已標 2026-06-13 更正）
6. ✅ **B-W5 前置**：6 處 silent `except` 改 `logger.warning`（引入 stdlib logging +
   CLI 入口 `logging.basicConfig`）；narrow `ValueError`（日期解析）保留 — PR #12

### Phase 1 — Spec A：資料層擴充（鐵律 1 的「資料正確」）✅ **全數完成（2026-06-14）**

六條 PR 全部合併進 `main`（測試基線：main **149 passed**，自 Phase 0 的 91 起算）：

1. ✅ **W6 + W10**：統一 retry/backoff（`data/retry.py` `with_retry`，指數退避+jitter）+
   波動率年化 `trading_days` 參數化（US 252 / TW 245）+ test_indicators / test_finmind — PR #16
2. ✅ **W1 + W2 確定性衍生基本面**：US SEC 補 capex/D&A/流動資產負債/利息費用等原始欄位 +
   FCF/working_capital/net_net/debt_to_equity/interest_coverage/gross_margin/roe 衍生；
   TW 對齊（net_income_ttm + 同名衍生）；US implied P/E cross-check — PR #18
3. ✅ **issue #4**：法人 5/20 日累計 + 融資券 5 日趨勢（Closes #4）。**取捨留痕**：issue 內
   A 段「20 日累計」與 C 段「縮窗省配額」互斥，取 A 捨 C，籌碼視窗反而加長 — PR #20
4. ✅ **estimate 類別**：新 evidence category + forward EPS consensus（yfinance，US+TW）；
   cite-check 可引用、audit 不做 freshness error — PR #21
5. ✅ **short interest**：US FINRA 二手（short_percent_of_float / ratio / MoM）+ TW 借券/融券
   proxy（決議 3），都歸 chips 類別 — PR #23
6. ✅ **Damodaran 產業 multiples**：新 reference 類別 + vendored CSV 快照（US-only，sector 映射，
   Total Market baseline）（決議 2）— PR #24

**偏離 / 決策留痕**：
- W2 P/E 閾值實作為 **25%**（非草案 10%）：yfinance trailing(TTM) vs implied(FY) 口徑差；
  收回 10% 的乾淨解（補 US `eps_ttm`）見 follow-up #19。
- issue #4 fetch 視窗 10→**35/12**（非草案的 →3），原因見上 PR #20。

**衍生 follow-up issues（2026-06-14 全數清理）**：
- ✅ #17 `with_retry` 支援 `Retry-After`（整數/小數秒 + HTTP-date）— PR #36
- ✅ #19 US `eps_ttm`（最近四季合計，Q4 由 FY−Q1-3 反推）→ P/E cross-check 收回嚴格 10%
  — PR #35（live 9 檔美股含 NVDA 全過閾值）
- ✅ #22 yfinance 同步呼叫統一 `to_thread_with_timeout` 防卡死 — PR #36
- ⏸ #25 Damodaran `_INDUSTRY_MAP` 擴充 — 標 `needs-data` defer：需累積實際分析的
  unmapped log 素材才好補，無對映已安全降級到 market baseline。

**下一步：Phase 3（Spec E1：Sage Runtime + Buffett/Munger 手工 Persona Pack 試點）。**

### Phase 2 — Spec B 剩餘：管線硬化（鐵律 1 的「分析有意義」）✅ **全數完成（2026-06-14）**

兩條 PR 全部合併進 `main`（測試基線：main **160 passed**，自 Phase 1 的 149 起算）。
做完這個 Phase，**Pillar 1 收口**——之後燒給專家的每一個 token 都站在可信資料與硬閘門上。

1. ✅ **W4 + W7**：cite-check 笛卡兒積收斂（`_kind` 欄位語意分類 + `_PAIR_OPS` 手列配對
   白名單，決議 2）+ 符號對稱（僅變動率%放寬，讓中文「下跌 5%」對得上 -5% 真值）；
   chief brief 主體（thesis / key_risks / what_would_change_my_mind）過 cite-check，
   retry 1 次仍失敗則標 `unverified` 揭露不 refuse（決議 5）— PR #27
2. ✅ **W8 + W9**：macro freshness 45→60；缺漏分級抽成模組常數 `CATEGORY_SEVERITY`
   （決議 3），`history` 由 warning 升 error（核心三類），collector 抓取失敗經
   `fetch_failures` 顯式入帳（`collector_error` finding，核心類別失敗即降級）— PR #28

**偏離 / 決策留痕**：
- W4 符號對稱用了 `abs()`，**超出 spec 字面**「`(a-b)/b` 與 `(b-a)/a`」：兩式對 95 vs 100
  只得 `{-5, +5.26}`，仍對不上「下跌 5%」抽出的 `+5`，必須再加絕對值；已嚴格限縮在
  變動率% 一種運算（比率/利潤率符號有意義，不放寬）。reviewer 同意無更縮解。
- W4 `price × magnitude` 配對刻意排除：市值=股價×股數這類鏈式衍生不再由驗證層拼湊
  放行，源頭該以 `market_cap` 自身 evidence 呈現。
- W7 只做數字級驗證；chief 行內 `[E001]` 引用紀律屬 Spec D / Phase 5。

**review nits（2026-06-14 已全數補完 — PR #34）**：
- ✅ #30 `collector_error` 與 `completeness` 去冗餘：類別同時「缺」且「抓取失敗」時錯因
  併入 completeness 該條（並清洗 err 取首行+截斷），不再出兩條 ~重複 finding。
- ✅ #31 `CORE_CATEGORIES` 改 import-time 不變式 assertion（鎖「核心類別 ≡ 降級類別」）。
- ✅ #32 ETF fundamentals 例外集中到 `_etf_relaxed()` 單一真相來源（severity + msg 同源）。
- ✅ #33 補 `{magnitude, per_share}` 配對（淨利/EPS = shares）迴歸測試。

### Phase 3 — Spec E1：Sage Runtime + 手工 Persona Pack 試點（鐵律 2 起點）

Persona Pack 格式（persona.yaml + rules.yaml + sop.yaml + skills.py）+ Runtime
三段執行（skill pass 程式算 → rule pass 程式判 → SOP pass LLM 按專屬工作流走）。
**Buffett + Munger 兩位手工打造**為試點——手工過程的步驟紀錄，就是 Phase 6
Nüwa 要自動化的規格。其餘 8 位以舊格式向後相容運行，漸進遷移。

### Phase 4 — Spec C：陪審團結構

兩階段 Council（cheap scout → deep representative）、Debate 雙盲對稱化、
outlier 雙邊規則、neutral 獨立訊號（區分「訊號不足」與「意見分歧」）。
在新 Runtime 之上實作，避免做兩次。

### Phase 5 — Spec D：決策結構

Chief / Judge 強制 evidence id 引用（行內 `[E001]`）、risk officer 雙向
`[-0.3, +0.2]`、chief↔risk 固定 1 輪迭代。

### Phase 6 — Spec E2：Cyber-Nüwa 蒸餾引擎 + 全員遷移

`cyber-sages distil` 蒸餾管線（ingest → extract（每條附原文出處）→ consolidate
跨源一致性 → emit pack → validate）。其餘 8 位大師遷移為 Persona Pack。
**最小重放工具**（歷史時點截斷 evidence 重跑）在此 Phase 落地，
同時補 Spec D 驗收的回測條件與 persona 品質分數。

## 未來 Roadmap（out of current scope，僅作 placeholder）

- **更多資產情境**：Crypto（CoinGecko / 鏈上數據）、港股 / A 股 / 日股、其他
- **完整歷史回測器**：Phase 6 的最小重放工具擴建為勝率驗證系統
- **更多 persona**：Nüwa 量產，新大師 = 新文本來源 + 一次蒸餾
- **場景化的問題分析套件**：基本面長持 / 短炒 / 期權 / 跨市場對沖等情境化 brief 模板

這些都等 Phase 6 收口後再開。
