# Cyber-Sages Roadmap

**Revision**: 2026-06-13（v2 — 開放問題定案 + Sage Runtime / Cyber-Nüwa 升級為核心軌）

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
| W1 | 3 個 persona 招牌焦點缺資料 | Pillar 1+2 同步受傷 | A |
| W2 | US P/E 是 yfinance 二手值, audit 沒 cross-check | US 估值每 run 受影響 | A |
| W3 | Council prompt cache 沒用 | 10x token 浪費 | B（**前置到 Phase 0**） |
| W4 | cite-check 笛卡兒積過寬, 偽造可過 | 反幻覺閘門失效 | B |
| W5 | 6 處 silent exception | schema 變更 = 整欄消失 | B（**前置到 Phase 0**） |
| W6 | 沒 retry/backoff | 高流量時段 run 不穩 | A |
| W7 | Chief thesis 整段沒 cite-check | brief 主體是 Pillar 1 唯一未驗證 | B |
| W8 | macro 過期 4 個月只 warning | 巨集分析師拿舊資料 | B |
| W9 | 部分 collector 失敗不觸發降級 | 髒資料當滿資料 | B |
| W10 | 252 天年化用於 TW | 略高估 TW 波動率 | A |
| — | 測試覆蓋洞（us_stocks / indicators / gateway / macro / finmind / report） | 後續改動無保護 | A + B |

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

### Phase 0 — 護欄與速贏（先有保護網，再動大刀）

小、低風險、互相獨立，合計 1–2 個工作天量級：

1. **Issue #5**：TW 端到端 pipeline 整合測試（2330 個股 + 0050 ETF 兩個 scenario）
   ——Spec A 要大改 `tw_stocks.py`，這是它的迴歸保護網，**必須最先做**
2. **Issue #7**：run 輸出標 commit hash（已實際造成 review 誤判）
3. **Issue #6**：TTM_FIELDS 模組常數化 / `_AuditorOutput` 公開 / ETF TaiwanStockInfo 實測
4. **Issue #8**：MacroProvider 併入 `MarketDataProvider` 協議（**A 路徑**：協議加
   `get_macro`，個股 provider no-op 回 `[]`）
5. **B-W3 前置**：Council shared_prompt 尾端加 cache_control——之後每個 Phase 的
   開發迭代都燒 token，越早做後面所有實驗越便宜
6. **B-W5 前置**：6 處 silent `except: pass` 改 `logger.warning` + audit finding
   ——保護 Phase 1 動資料層時不被 schema 變更無聲咬掉整欄

### Phase 1 — Spec A：資料層擴充（鐵律 1 的「資料正確」）

含 **issue #4 併入**（法人 5/20 日累計、融資券趨勢、fetch 視窗 10→3 天——與 A 的
「確定性衍生欄位」哲學同構）。欄位優先序**由 Spec E 的 persona skill 需求反推**
（P0 清單見 Spec A 決議）。驗收核心：每位大師的招牌焦點至少 1 個量化錨點、
US P/E 確定性 cross-check、全 provider retry/backoff、245 天校準、測試 ≥ 70。

### Phase 2 — Spec B 剩餘：管線硬化（鐵律 1 的「分析有意義」）

W4 cite-check 笛卡兒積收斂 + 符號對稱、W7 chief thesis 過 cite-check、
W8 macro freshness、W9 缺核心類別觸發降級。做完這個 Phase，
**Pillar 1 收口**——之後燒給專家的每一個 token 都站在可信資料與硬閘門上。

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
