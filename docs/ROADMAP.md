# Cyber-Sages Roadmap

**Revision**: 2026-06-23（v13 — **Phase 6 重構：Cyber-Nüwa 引擎 → forge-sage skill**。DK 定調原
`cyber-sages distil` 蒸餾引擎為過度工程：專案一半給 AI agent 用、過往 12 位大師都是 agent
（Claude Code）手工打造的，**蒸餾者本質就是讀 SOP 的 agent**——蓋機器消費 SOP 是多餘一層。改為把
方法論固化成 checked-in 的 **forge-sage skill**（`.claude/skills/forge-sage/SKILL.md`，含 Pack 解剖／
DSL 參考／rule↔skill pattern／欄位紀律／品質 checklist／遷移 SOP）。Phase 6 重新定義＝forge-sage skill
＋全員遷移；可驗證性的**重放/品質分數獨立成 Phase 7**（補 Spec D 回測驗收）。連帶：#39 skill 沙盒從
E2 硬前置降為一般衛生（無機器產碼 RCE 面）。順帶修了 cite-check macro 衍生+籌碼符號對稱誤殺（#84/PR #85）。）
**Revision**: 2026-06-22（v12 — **Phase 5 / Spec D 決策結構落地**（branch `feat/spec-d-decision-structure`，353 passed）：D-1 chief 行內 `[E0xx]` 引用 + cite-check 收嚴（`_chief_claims` 引文字內 id 非全 store）、D-2 judge rationale/反駁 cite-check（`_citecheck_judge`、DebateVerdict.unverified）、D-3 risk 雙向 `[-0.3,+0.2]`（`clamped_adjustment` + 對稱揭露）、D-4 chief↔risk 固定 1 輪迭代（≤-0.2 觸發）。決議偏離：「≥3 id」走 prompt 軟引導非硬 schema（守 W7 degrade-don't-refuse）；chief 補全類別 digest。回測驗收延 Phase 6。下一步：Phase 6（Spec E2 蒸餾 + 全員遷移）或剩餘 open issues。）
**Revision**: 2026-06-20（v11 — **Spec F 收官**：P0（#60）/P1（#61）/P2（#63/#64）+ review polish（#66/#67）+ TW 專屬總經源 follow-up（#68→央行重貼現率 #69 / 主計總處 CPI #70 / P3 強化 #71）全數合併、Spec F 標 accepted。剩餘完整性項 C6 情緒 / C3 TW 現金流 / TW RS 轉 issue #65（需設計）。下一步：Phase 5（Spec D 決策結構）。）
**Revision**: 2026-06-17（v10 — **Spec F 資料源頭強固**插隊於 Phase 5 前：SPCX 幽靈 bar live 事故觸發，P0（#60）+ P1（#61）已合併、P2 進行中。鐵律 1「資料正確」優先於決策結構。見 `docs/specs/2026-06-17-F-data-source-robustness.md`）
**Revision**: 2026-06-14（v9 — **Phase 3 / Spec E1 全數完成（#38/#42/#44 合併、live 雙市場驗收通過）**。Phase 4 / Spec C **v2 大改寫**：DK 把 P4 擴大為「analyst 降級為數據源 + horizon(trading/value) 分流 + 大師為主體 + 陪審團結構」四條 PR；E2 量產延到「更後面」——先手工擴充大師（按類型）。E2 前置 issue #39/#40/#41/#43/#45）

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

### 9 個 Pillar 2 結構性弱點（P1–P9；P8/P9 為 2026-06-14 DK 追加）

| # | Gap | 影響 | Spec |
|---|---|---|---|
| P8 | analyst 下 outlook，與 sage 意見競爭 | 主體錯位（應 analyst 供數據、大師為主體）| C v2 |
| P9 | horizon 只是輸出視角、非分析模式 | 當沖與長期混為「全面分析」 | C v2 |
| P1 | persona 只有語氣沒有行為規則 | mode collapse 溫床 | **E**（Persona Pack，✅ E1 完成）|
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
| C | Pillar 2 分析主體 + Horizon 分流 + 陪審團結構 | **P8（analyst 降級）, P9（horizon 分流）**, P2, P3, P6, P7（P1 移入 E） | A + E1 |
| D | Pillar 2 決策結構 | P4, P5 | A + C |
| **E** | **Sage Runtime + Cyber-Nüwa 蒸餾引擎** | P1 + issue #2 全部 | E1 依賴 A+B；E2 依賴 C |
| **F** | **資料源頭強固**（Pillar 1 回訪） | S1-S7 穩定性 + C1-C8 完整性（SPCX 幽靈 bar 觸發） | A + B（見 `docs/specs/2026-06-17-F-...md`） |

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

**設計定案（2026-06-14，7 項實作決議見 Spec E「E1 實作決議」附錄）**：directional
floor 只夾 confidence 不翻 stance；skill private evidence 獨立命名空間（`S-<key>-NNN`）；
sop_trace 軟揭露 cite-check；目錄帶 epoch 但多版本選取延後 E2；補齊多年衍生欄位；
epoch 選 **2019**（Apple 時期）。拆三條 PR：

1. **框架**（data-agnostic）：Pack loader（目錄/單檔共存）+ rules DSL evaluator +
   skill 框架 + `SageSignal.sop_trace`/`not_evaluable` + 三段執行 + clamp + 軟揭露
   cite-check 接線；degraded 8 位走原路徑。← **✅ 已合併 PR #38**
2. **多年欄位資料**（純 Spec A 性質）：`roe_5y_avg`/`gross_margin_trend_5y`/
   `earnings_stability_5y`（`data/longterm.py` 共享純計算；US SEC 多年 10-K + TW FinMind
   季度聚合，單位口徑見 Spec E 附錄 PR2 落地註）。← **開工中**（branch `feat/e1-multiyear-fundamentals`）
3. **pilot Pack**：Buffett-2019 + Munger-2019 手工 Pack（persona/rules/sop/skills），
   舊單檔 `buffett.yaml`/`munger.yaml` 退役為目錄 Pack；共用計算放 `personas/skills_lib.py`；
   clamp 生效 + skill 可溯源 + sop_trace 軟揭露整合測試（真實 Pack 檔 + mock gateway）；
   手工過程寫成 `docs/specs/2026-06-14-E1-pilot-pack-handcrafting.md`（＝Nüwa 規格輸入）。
   ← **✅ 全數合併（#38/#42/#44）**；live NVDA/2330 驗證完成（sop_trace 每步錨定、rule_conflict
   不翻 stance、台股 owner_earnings not_evaluable 全兌現）→ **E1 驗收條件全數達成**。

### Phase 4 — Spec C v2：分析主體 + Horizon 分流 + 陪審團結構

> 2026-06-14 DK 把 P4 從「修陪審團機制」擴大為「梳理整條分析流程」。見 Spec C v2。

兩個上層重構 + 原機制（四條 PR，**機制全數合併、實機驗證**）：

1. ✅ **Analyst 降級**（P8，PR1 #46）：`AnalystReport` 去 `outlook` → 中性 findings（仍過
   cite-check）；方向性判斷由 sage 獨佔——「analyst 只供數據、**大師才是主體**」。
2. ✅ **Horizon 分流**（P9，PR2 #48）：`--horizon trading|value` 旗標（預設 value）；persona
   宣告 `horizons`、越圍 abstain；證據重心與 action plan 口徑隨 horizon。當沖（數天~數週）與
   長期價值（3~10y）不再混為「全面分析」。
3. ✅ **交易型 Pack 試點**（PR3 #50）：Livermore 升級 Pack + 手工新增 Minervini/Raschke，
   trading council 成立 5 席；NVDA `--horizon trading` 實機通過（**未來 roster 按類型 curate**）。
4. **陪審團結構**（P2/P3/P6/P7，PR4a #52 + PR4b #54）：Debate 雙盲對稱（P3）、outlier 雙邊
   反駁（P6）、neutral 三類獨立訊號（P7）——✅ 機制合併 + NVDA 實機驗證正常；兩階段 Council
   scout→deep（P2）✅ 機制合併，但 **token ≥30% 下降未達標**（實機 N=9 反而更貴，→ issue #55：
   scout 需瘦身 prompt + 便宜小模型才划算）。

### Phase 4.5 — 手工擴充大師（按類型/原型 curate）

DK 2026-06-14 定調：**E2 量產要排到「更後面的後面」——先手工做更多大師、累積足夠面向，
才能提供更完善的蒸餾方法**。手工樣本不足就量產只會固化不成熟規格。此階段逐步補齊各原型
（價值/成長/動能/宏觀/風險/事件…）的代表大師，每位仍走 E1 手工 Pack 流程。

**進度**：
- ✅ Batch 1（PR #56）：Trump（政策催化劑，weight 0.7）/ Chanos（鑑識空頭，帶 2 條 hard rules）/ Icahn（行動派，純 SOP）。
- ✅ Batch 2（PR #57）：Soros（反身性宏觀）/ Roaring Kitty（散戶情緒，epoch=2021）/ Masayoshi Son（power-law 集中豪賭）——刻意補「敢站多的賭徒」平衡目前偏空/保守的 roster（價值紀律派 + Taleb + Chanos 預設都壓低信心）。PLTR 實機驗證「roster 平衡 ≠ verdict 平衡」：要後者得靠下方四象限選擇讓使用者自組陪審團。
- ✅ Batch 3（PR #59）：**Paul Tudor Jones**（防禦型短線交易者）——補「保守×短期」最瘦象限（2→3 席：Taleb/Raschke/PTJ）。招牌『絕不持有跌破 200 日均線的部位』靠 skill 算 `price_vs_sma_200_pct` 衍生欄位 + `below-200dma` 硬規則落地（DSL 無法直接比 last_price<sma_200 雙欄）。與 Livermore/Minervini 進攻型趨勢區隔：他們追突破、PTJ 先問「會賠多少」。
- 🔜 Batch 4+（待定）：剩餘候選原型——量化/系統化、PE/私募控制、央行/政策制定者視角。

**象限化選擇（DK 2026-06-15 定調，✅ 已實作）**：「**保守↔激進 × 短期↔長期**」四象限組合來組 council，加上不過濾的「大師會堂」＝五種模式。
- **短期/長期** 軸 = persona 的 `horizons: [trading, value]`（`council.py` 按 horizon 分席）。
- **保守/激進** 軸 = persona 的 `aggression: [conservative, aggressive]`（純資料、比照 horizons；中庸者兩者皆列）+ CLI `--aggression conservative|aggressive`。
- **大師會堂（預設）**：不給 `--aggression` 時全員出席、`n_sages=None` 不截斷——一併修掉舊 default `--sages 10` 把低權重大師（Trump/Taleb/Wood/Icahn）切掉的問題；`--sages N` 改為純手動省 token 上限。
- brief / payload 揭露本次模式：激進陪審團偏多、保守陪審團偏空是「組成使然」非標的訊號，judge 須據此理解。
- 動機：手工擴充時刻意補齊各性格原型避免 roster 偏斜；象限選擇讓使用者主動挑「想聽哪種性格的陪審團」——PLTR 實機證明「roster 平衡 ≠ verdict 平衡」，要後者得用戶自選。

### Phase F — Spec F：資料源頭強固（Pillar 1 回訪，SPCX 幽靈 bar 觸發）

插隊於 Phase 5 之前——鐵律 1「資料正確」優先。SPCX live run 報「昨日成交量 0」（實際漲到 ~$202）
暴露三裂縫：幽靈 bar、跨源不獨立（兩條 Yahoo 路徑 correlated failure）、重大錯誤只降級不中止。

- ✅ **P0**（PR #60）：fatal 級別 + stage 2 中止（S4）、幽靈/0 量 bar 防呆（S1）、盤中獨立
  第二價格源 + 跨源 fatal（S2/C1）、分維度健康度評分卡取代全域封頂 0.5（S7）。
- ✅ **P1**（PR #61）：IPO/短歷史明說承認不降級（S5，依 D1）、forward P/E sanity 確定性閘門
  （S6）、二手 fundamentals 嚴格隔離（C2，DK option 2）、review follow-up #3/#7、
  EvidenceStore id 改單調計數器。
- ✅ **P2**（PR #63/#64）：Finnhub 真正異源第二價格源（D4，收口 correlated failure）、市值 vs
  shares×price 矛盾 sanity（S6 延伸）、C5 台灣 TWD/USD FX、C7 ATR/RS（US-only vs ^GSPC）。
- ✅ **review polish**（PR #66 行為 batch / #67 docs batch，issue #62）。
- ✅ **TW 專屬總經源 follow-up**（issue #68，已關閉）：央行重貼現率（PR #69）+ 主計總處 CPI 年增率
  （PR #70）+ P3 強化 `is_independent_source` / `pe_sanity` 雙向覆蓋（PR #71）。台股宏觀從「套美國
  fed funds」升級為「台灣重貼現率 + 台灣 CPI」在地維度；P2 #64-2 yfinance 效能經評估 decline。
- 🔜 **剩餘完整性項**（issue #65）：
  - ✅ **C3 TW 現金流量表**（2026-06-22）：FinMind YTD 累計確定性去累計化還原單季 → OCF/capex/FCF/
    D&A/利息保障（單季 + TTM）。capex 對齊美股正值慣例、FCF=OCF−capex；非曆年制安全降級。實機
    2330 驗證（四季還原加總=FY）。
  - ✅ **TW RS**（2026-06-22）：相對強弱 vs 加權指數 ^TWII，同步 yfinance 包 `to_thread_with_timeout`
    避免阻塞 event loop（best-effort）。實機 2330 驗證（rs_vs_benchmark_3m_pct）。
  - 🔜 **C6 news 情緒量化**（仍 defer，需設計）：LLM 情緒分數非確定性、非第一手，與反幻覺鐵律衝突
    ——須確定性詞典法或另立 sentiment phase；先觀察大師是否真需量化情緒、抑或讀 headline 已足。

**Spec F 收官**——Pillar 1 回訪完成，下一步 Phase 5（Spec D 決策結構）。

弱點編號沿用 spec 內 S1-S7（穩定性）/ C1-C8（完整性），不另佔 W 序號（W 系列為 Spec A/B 既有）。

### Phase 5 — Spec D：決策結構 ✅ **實作完成（2026-06-22，待 PR review）**

Chief / Judge 強制 evidence id 引用（行內 `[E001]`）、risk officer 雙向
`[-0.3, +0.2]`、chief↔risk 固定 1 輪迭代。branch `feat/spec-d-decision-structure`（353 passed，
自 Spec F 的 342 起算 +11）：

1. ✅ **D-1 Chief 行內引用**：CHIEF_SYSTEM 要求 thesis / key_risks / what_would_change_my_mind
   量化宣稱附行內 `[E0xx]`；`_chief_claims` 由「引全 store」改「引文字內行內 id」——數字須由
   chief **實際引用**的 evidence 推導，攔截「引對 id 卻寫錯數字」與 no_cite。chief prompt 補全
   類別 digest（讓任何數字有可引 id）。retry 1 次仍失敗軟揭露（W7 一貫）。
2. ✅ **D-2 Judge 行內引用**：JUDGE_SYSTEM 同步；`_citecheck_judge` 驗 rationale + outlier_rebuttals
   （結構欄 ids ∪ 行內 ids），DebateVerdict 加 `unverified` 回填，brief/details/payload 揭露。
3. ✅ **D-3 Risk 雙向**：`conviction_adjustment` `[-0.3,+0.2]`，`RiskNote.clamped_adjustment` 單一
   真相來源；決議 3 對稱揭露（brief「風控官上調/下調 ±0.x（理由）」+ payload `risk_officer` 區塊）。
4. ✅ **D-4 chief↔risk 固定 1 輪**：clamped ≤ -0.2 時 chief 自動回應重寫 thesis 關鍵段
   （`_RISK_REBUTTAL_THRESHOLD`），重寫版同樣過 cite-check。

**決策偏離留痕**：決議 1「≥3 id / 段落級」**未用硬 schema validator**（草案字面「schema 驗證失敗」）
——改 prompt 軟引導，避免 retry 耗盡 raise 中止 synthesis 與 W7 degrade-don't-refuse 衝突；量化
宣稱的硬約束仍由 cite-check no_cite 把關。回測驗收（5 案例）依範圍變更延 Phase 6。詳見 Spec D。

### Phase 6 — forge-sage skill + 全員遷移（**原 Spec E2 蒸餾引擎，2026-06-23 重構**）

**重構（DK 2026-06-23）**：原規劃 `cyber-sages distil` 蒸餾**引擎**判定為過度工程並撤銷。
理由：專案一半給 AI agent 用、過往 12 位大師全是 agent（Claude Code）讀文本手工打造的——
**「蒸餾者」本質就是讀 SOP 的 agent（Claude 自己），而 Claude 也是本專案消費者**。蓋一台 LLM
pipeline 自動消費 SOP，是在程式碼裡僵硬複刻「agent 讀規格→產 Pack」、且更差（`extract`/
`consolidate` 本質是 LLM 判斷非確定性程式）；唯一買到「量產規模」又與「quality > scale」doctrine
相悖。詳見 Spec E 的 E2 重構段落。

**Phase 6 重新定義 ＝ 兩件事**：

1. ✅ **forge-sage skill**（`.claude/skills/forge-sage/SKILL.md`，checked-in）：把 12 個 Pack
   累積的方法論固化成 agent 原生 skill——任何 agent 讀完就能 forge 新大師或遷移舊 yaml。涵蓋
   Pack 解剖（§0 做到哪一層的判斷）、sources/epoch、persona 兩條分席軸、rules DSL 完整參考、
   **rule↔skill 依賴 pattern**（PTJ）、純 SOP Pack 取捨（Icahn）、skills_lib 共用、**欄位紀律**
   （`_annual`／百分比單位／台股差異）、品質 checklist、遷移 SOP。README 有指引。
2. 🔜 **全員遷移**（套用 forge-sage skill，agent 任務非 engine run）：
   - 7 舊單檔 yaml（burry/damodaran/druckenmiller/graham/lynch/taleb/wood）→ 完整 Pack、退役舊檔。
   - 6 半 Pack（chanos/icahn/roaringkitty/son/soros/trump）確認「刻意純 SOP」留痕 or 補齊 rules/skills。
   - 每條新 rule / SOP step 在 `weight_rationale` / `note` 留出處痕跡（provenance discipline）。

**前置 issue 狀態**：#41 ✅ / #45 ✅（已 CLOSED）；#39（skill 沙盒）**降為一般衛生**——無機器
產碼，skills 仍由 agent 手寫，同現存 6 個手寫 skills.py 信任模型，不再硬阻擋；#40（skill 例外隔離）
+ #43（非曆年制多年欄位）遷移時順手處理或獨立小 PR。

### Phase 7 — 重放/回測 + persona 品質分數（**從原 E2 拆出，先記錄後處理**）

原本綁在 E2 的「可驗證性」獨立成 Phase 7（與 forge-sage 方法論是兩件事：一個是 agent SOP、
一個是真程式碼）。範圍：

- **最小重放工具** `cyber-sages replay TICKER --as-of DATE`：把 evidence 截斷在歷史時點重跑
  pipeline。lookahead bias（模型訓練看過事後結果）**無法消除、只強制揭露**（replay 報告固定標注）。
- **persona 品質分數**：新 Pack 進 council 與既有大師辯論 N 個歷史案例，量 (a) sop_trace 每步有
  evidence 錨點、(b) 立場與該大師已知公開立場不矛盾、(c) 辯論被指出的矛盾數——給遷移後的 Pack 一把品質尺。
- **補 Spec D 回測驗收**（Phase 5 延下來的 5 案例回測條件）。
- 未來可擴建為完整勝率回測器（見下方「未來 Roadmap」）。

狀態：**僅記錄，後續再處理**。不在當前 Phase 6 範圍。

## 未來 Roadmap（out of current scope，僅作 placeholder）

- **更多資產情境**：Crypto（CoinGecko / 鏈上數據）、港股 / A 股 / 日股、其他
- **完整歷史回測器**：Phase 6 的最小重放工具擴建為勝率驗證系統
- **更多 persona**：Nüwa 量產，新大師 = 新文本來源 + 一次蒸餾
- **場景化的問題分析套件**：基本面長持 / 短炒 / 期權 / 跨市場對沖等情境化 brief 模板

這些都等 Phase 6 收口後再開。
