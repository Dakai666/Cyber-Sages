# Spec C — Pillar 2 分析主體 + Horizon 分流 + 陪審團結構

**Status**: accepted（2026-06-13 機制定案 + **2026-06-14 v2 大改寫**，見文末決議）
**Date**: 2026-06-12（v2 修訂 2026-06-14）
**Dependencies**: A + B + **E1（Sage Runtime，已完成 PR #38/#42/#44）**——council 結構在新 Runtime 之上實作。
**範圍變更**:
- 2026-06-13 — C-1（hard_rules）與 C-2（weight_rationale）移入 Spec E（已於 E1 落地）。
- **2026-06-14（v2）— DK 把 P4 從「修陪審團機制」擴大為「重新定義分析主體 + horizon 分流」。**
  原 C-3~C-6（P2/P3/P6/P7 機制）保留，但改在 horizon-aware council 之上做。

## 背景

Pillar 2 的「健全有意義」原本聚焦兩件事：每位大師有清楚行為準則、陪審團結構鼓勵實質分歧
（→ E1 的 Persona Pack 已解 P1；本 spec 剩機制面 P2/P3/P6/P7）。**2026-06-14 DK 再點出兩個
更上層的結構問題**，使 P4 的本質從「修機制」升級為「梳理整條分析流程」：

1. **analyst 與 sage 角色重疊（P8）**：analyst 現在不只供數據，還各自下 `outlook`
   （bullish/bearish）+ claims——等於有兩層意見在競爭。專案定位是「analyst 只提供數據、
   **大師專家才是主體**」，現況沒做到。
2. **horizon 被混為一談（P9）**：technical analyst 給 short/mid/long 三段讀數、synthesis 輸出
   三個 `HorizonView`，但**整條管線、選哪些大師、action plan 都是同一套「全面分析」**。
   當沖（數天）與長期價值（3~10 年）是**本質不同的買賣行為**，用同一批大師、同一份證據權重、
   同一個進出場計劃會把兩者搞混。

## 涵蓋的 Gaps

| # | Gap | 位置 |
|---|---|---|
| **P8** | analyst 下 outlook，與 sage 意見競爭（analyst 應只供數據） | `agents/analysts.py` `AnalystReport.outlook` |
| **P9** | horizon 只是輸出視角、非分析模式；當沖與長期混為「全面分析」 | `agents/synthesis.py` `HorizonView`；無 horizon 選大師/權重/計劃 |
| P2 | Council 同 model 統計稀釋論點不成立 | `config.yaml` 全 sage 同 model |
| P3 | Debate 不對稱（bull 看不到 bear） | `agents/debate.py` |
| P6 | Outlier 逐點反駁只在「敗方有 outlier」時觸發 | `debate.py` |
| P7 | neutral 大師被算進 consensus 卻不被視為 outlier | `council.py` tally |

（P1 已由 E1 Persona Pack 解決；P4/P5 屬 Spec D。）

## 設計定案（2026-06-14，DK 逐項拍板）

| # | 決議 | 拍板 |
|---|------|------|
| 1 | horizon 切分機制 | **`--horizon trading\|value` 旗標、單模式一跑**，驅動「選哪些大師 + 證據權重 + action plan 口徑」。預設 `value`（專案核心：能不能買進長抱）。 |
| 2 | horizon 分級 | **二分 trading / value**（trading＝數天~數週、value＝數年）。day/swing 先合為 trading；純當沖 intraday 留未來資料層擴充（現抓日線，trading=數天~數週剛好夠用）。 |
| 3 | analyst 定位 | **降為中性 findings、不表態**：去 `outlook`，只負責「核實 + 標註 + 結構化數據」（仍過 cite-check 當數據護欄）；**所有方向性判斷由 sage 獨佔**。 |
| 4 | sage × horizon | **Pack 宣告適用 `horizons`；越圍則 abstain**（persona 級 not_evaluable）。Buffett 不答當沖、Livermore 不答 10 年長持——延伸 E1「看不到就誠實說」哲學到 persona 層。 |
| 5 | 交易型 roster | **先用現有 Livermore+Druckenmiller + 手工新增 1~2 位短線大師**讓 trading council 成立；大擴充延後。 |
| 6 | roster 長期方針 | 這兩位是**暫時手工補位**；**未來大師選定按「類型/原型」curate**——每個面向（價值/成長/動能/宏觀/風險/事件…）都有代表，覆蓋完整方法面。 |

## Horizon taxonomy

| mode | 期間 | 主體大師（暫定）| 證據重心 | action plan 口徑 |
|---|---|---|---|---|
| `value`（預設）| 數年（3~10y）| Buffett/Munger（Pack）、Graham/Damodaran/Lynch/Burry/Taleb/Wood | 多年基本面、護城河、owner earnings、估值 | 分批建倉、寬停損、長期翻盤條件 |
| `trading` | 數天~數週 | Livermore/Druckenmiller + 手工新增 1~2 位 | 技術面、籌碼/即時流向、動能、波動 | 緊停損、明確進出場、短線失效訊號 |

純當沖（intraday）out of scope——需 tick/分鐘級資料，現況只抓日線；二分法刻意繞過、先可用。

## Analyst 降級的下游影響清單（P8 落地需一併改）

- `AnalystReport.outlook`（schema）移除；`claims` 改為中性 finding（描述事實 + 引用，不帶方向）。
- analyst prompts：去除「judge bullish/bearish」語氣，改「surface & annotate，不下結論」。
- `council.py` `_reports_text`：sage 看到的是中性 findings，不再看 analyst 立場。
- `debate.py` / `synthesis.py`：凡讀 `report.outlook` 處改為不依賴 analyst 方向（方向只從 sage council 來）。
- brief 渲染：analyst 段退為「已核實證據摘要」，sage council 升為主體論述。
- 測試：既有測 analyst outlook 的 case 調整。

## PR 拆分（P4 = 一個 Phase，四條 PR）

1. **Analyst 降級**（P8）：`AnalystReport` 去 outlook → 中性 findings；更新 prompts + 上述下游；brief 重排（sage 為主體）。可獨立先行（不依賴 horizon）。
2. **Horizon 框架**（P9 + 決議 1/2/4）：`--horizon` 旗標；persona 加 `horizons` 欄（Pack 與 legacy 都標）；council 按 horizon abstain + quorum 調整；action plan 口徑隨 horizon；evidence 重心隨 horizon（哪些 analyst/類別進 digest）。
3. **交易型 Pack 試點**（決議 5）：Livermore 升級為 Pack + 手工新增 1~2 位短線大師（rules/sop/skills 比照 E1）；trading council 跑通 NVDA/2330 各一 run。
4. **陪審團結構**（P2/P3/P6/P7）：兩階段 council（scout→deep）、debate 雙盲對稱、outlier 雙邊、neutral 獨立訊號——全部 horizon-aware，建在新 council 之上、不做兩次。

## 陪審團機制（C-3 ~ C-6，保留，改 horizon-aware）

#### C-3. Council model 多樣化（P2）
兩階段：(1) cheap scout（同 model、small prompt）全員產粗 stance + 信心區間；(2) deep sampler
只挑 consensus + outlier representatives 各 ~3 位深入。**horizon-aware**：scout/deep 只跑該
horizon 適用、未 abstain 的大師。

#### C-4. Debate 對稱化雙盲（P3）
bull 與 bear 同時收到「council 意見 + 對手是誰」、互不可見對手論點；出完後互餵對手論點做 1 輪
反駁；裁判看完整版（決議 3）。

#### C-5. Outlier 規則雙邊（P6）
無論 winner 是誰，任一方有 outlier，敗方 outlier 必須逐點反駁（5B/4N/1S 時 bear 的 1 個 outlier
也得被守住）。

#### C-6. Neutral 獨立訊號（P7）
`SageSignal` 加 `neutral_reason` enum（`out_of_circle` / `insufficient_signal` /
`balanced_forces`）；tally 分開計數、brief 分開呈現。**注意與 horizon abstain 區分**：abstain＝
「這題不在我的 horizon」（persona 級 not_evaluable，不計入 council），neutral＝「在我 horizon 內
但我判中性」。

## 排程（2026-06-14 DK 定調）

```
P4（本 spec）：analyst 降級 + horizon 分流 + 交易試點 + 陪審團結構
   ↓
擴充手工大師（按類型/原型 curate，逐步補齊各面向）   ← DK：先手工做更多大師
   ↓                                                   累積出更完善的方法面向
Spec D（Phase 5）：決策結構（chief/judge evidence id、risk 雙向）
   ↓
E2 Cyber-Nüwa（更後面的後面）：有足夠手工樣本後才談量產蒸餾
```

> **DK 2026-06-14 定調**：E2 量產延到「更後面的後面」——**得先手工製作更多大師、累積足夠
> 面向，才能提供更完善的蒸餾方法**。手工樣本不足就量產，只會固化不成熟的規格。

## 驗收條件（v2）

- [x] `--horizon trading|value` 旗標可用，預設 value；錯誤值 fail-loud。（PR1/PR2 #46/#48）
- [x] analyst 不再輸出 outlook；sage 為唯一方向性來源；brief 以 sage council 為主體。（PR1 #46）
- [x] persona 宣告 `horizons`；越圍大師於該 horizon abstain、不計入 council，並揭露。（PR2 #48）
- [x] trading council 成立（5 席：Livermore/Minervini/Raschke + Druckenmiller/Taleb），NVDA `--horizon trading` 實機通過。（PR3 #50；2330 待補）
- [x] value run 與 trading run 的 action plan 口徑明顯不同（停損/進出場/翻盤條件）。（PR3 實機：trading 分批試單/緊停損 vs value 分批建倉/SMA200 寬停損）
- [~] 兩階段 council 跑通；token 成本下降 ≥ 30% **未達標**。機制已跑通（PR4b #54），但 2026-06-15
  實機 NVDA value（9 席）量測：two-stage 反比 single-stage **貴 ~45%**（total token）。主因：
  `sage_scout` 預設與 `sage` 同 model + scout 仍夾完整 digest，9 份 scout input 推高總量、只省
  3 個 deep。**達標前提**：scout 換便宜小模型 + scout prompt 瘦身（精簡 evidence）+ N≫deep_budget。
  追蹤 → **issue #55**。
- [x] debate 雙盲：bull/bear 第一輪互不可見對手論點。（PR4a #52；實機驗證雙方各有獨立開場+反駁）
- [x] outlier 規則覆蓋 5B/4N/1S；neutral 三類 `neutral_reason` 分開呈現、與 abstain 區分。（PR4a #52）

## 決議（2026-06-13，DK 授權按最優解定案；機制層，仍有效）

1. **hard_rules 用受限 DSL + 自然語言 exceptions**（已於 E1 落地）。
2. **cheap classifier 不綁定特定 model**：config.yaml 加 `sage_scout` role，由部署者指定。
3. **雙盲裁判看完整版**：雙方兩輪完整論點都給裁判；裁判只跑一次，成本可控。
4. **兩階段 Council 各階段內部平行**：scout 全員平行 → 程式分組 → deep 組內平行。
5. **neutral 細分**：`neutral_reason` enum（`out_of_circle` / `insufficient_signal` /
   `balanced_forces`），tally 分開計數、brief 分開呈現。

## 相關檔案

- `src/cyber_sages/agents/analysts.py`（P8 降級）
- `src/cyber_sages/agents/council.py`（horizon abstain、兩階段、tally、neutral）
- `src/cyber_sages/agents/debate.py`（雙盲、outlier 雙邊）
- `src/cyber_sages/agents/synthesis.py`（action plan 口徑隨 horizon）
- `src/cyber_sages/personas/*`（persona 加 `horizons`；交易型新 Pack）
- `src/cyber_sages/cli.py` / `pipeline.py`（`--horizon` 旗標貫穿）
- `config.yaml`（`sage_scout` role）

## 參考

- 2026-06-12 全專案 audit 紀錄；2026-06-14 DK P4 方向定調對話
- Spec E（Persona Pack / Sage Runtime，E1 已完成）
- Issue #2 / E2：Cyber-Nüwa（排在手工擴充大師之後的後面）
