# 附錄 — E1 Pilot Pack 手工打造過程（Buffett-2019 / Munger-2019）

**Date**: 2026-06-14
**屬於**: Spec E（Sage Runtime + Cyber-Nüwa），E1 PR3
**用途**: 這份文件記錄「人怎麼把一位大師打造成 Persona Pack」的每一步。**這就是 Phase 6
Cyber-Nüwa（E2）要自動化的規格**——Nüwa 的蒸餾管線（ingest→extract→consolidate→emit→
validate）應能重現下列人工判斷，產出品質不顯著低於手工版的 Pack。

## 0. 一句話總結

> 一個 Pack ＝ **身分（persona.yaml）+ 硬規則（rules.yaml）+ 決策流程（sop.yaml）+
> 確定性技能（skills.py）**。差異化不靠「獨特工具」，而靠**規則門檻、流程順序、裁量風格**
> ——Buffett 與 Munger 用同一把尺（owner earnings），量出不同結論。

## 1. 輸入：第一手文本（sources manifest）

每位大師先定**主要來源**（本人第一手 > 辯論紀錄 > 他人拆解），寫進 `persona.yaml` 的
`sources`。手工階段我憑既有認知撰寫；E2 Nüwa 階段這裡是實際要 ingest 的語料清單。

| 大師 | epoch | 主要來源 |
|---|---|---|
| Buffett | 2019 | 股東信 1977–2019、AGM Q&A、The Essays of Warren Buffett |
| Munger | 2019 | Poor Charlie's Almanack、Daily Journal/BRK AGM Q&A、Psychology of Human Misjudgment |

**epoch 選 2019**（Apple 時期）：對科技龍頭已開放、最貼近當代 NVDA 類標的；lookahead bias
由未來 `replay` 揭露（Spec E2 Q2）。

## 2. persona.yaml — 身分

手工步驟：
1. 沿用舊單檔 yaml 的 `philosophy/focus/voice`（已是大師精煉描述），**按 epoch 微調**
   （如 Buffett 2019 加一句「消費型科技特許權可納入能力圈」）。
2. 寫 `weight_rationale`：為什麼這個權重——第一手量、實證強度、與他人的互補性。
3. 列 `sources` manifest。

→ **Nüwa 對應**：philosophy/focus/voice 從文本摘要抽取；weight 由來源量 + 公開實證
啟發式給初值（人複核）。

## 3. rules.yaml — 硬規則 DSL（程式判定、零幻覺）

手工步驟：把大師「反覆強調、可量化」的原則翻成 `{field, op, value}`。每條問三件事：
1. **這條規則對應哪個 canonical 欄位？** 必須是 EvidenceStore 真的會 emit 的欄位名
   （見 §6 的欄位紀律——這是最容易出錯的地方）。
2. **方向與動作**：紅線型 → `cap_confidence`（封頂）；加分型 → `bullish_floor`/
   `bearish_floor`（保底，且依 E1 決議只夾 confidence、不翻 stance）。
3. **多源驗證**（E2 硬 gate）：同一條 hard_rule 候選至少 2 個獨立出處才進 `rules.yaml`；
   單一出處的降級進 `exceptions`。手工階段我以公認的招牌原則代行此判斷。

實際產出（節錄）：

| 大師 | rule | 條件 | 動作 |
|---|---|---|---|
| Buffett | no-leverage | `debt_to_equity > 1.0` | cap 0.5 |
| Buffett | wide-moat | `roe_5y_avg > 20` ∧ `gross_margin_trend_5y >= 0` | floor 0.6 |
| Buffett | predictable-earnings | `earnings_stability_5y >= 0.7` | floor 0.55 |
| Munger | avoid-mediocre | `roe_5y_avg < 10` | cap 0.4 |
| Munger | quality-compounder | `roe_5y_avg > 18` ∧ `earnings_stability_5y >= 0.6` | floor 0.6 |

**彈性靠 `exceptions`**（自然語言，SOP pass 由 LLM 裁量但強制引用 evidence）：壟斷市占可
推翻 P/E 保守、owner earnings 看不到時改用 FCF 近似等。

→ **Nüwa 對應**：extract 階段從文本抽「條件式原則」候選（每條附 passage id）；consolidate
做跨源一致性 + 欄位名對齊 canonical schema；矛盾候選標記給人裁決。

## 4. sop.yaml — 決策流程（流程本身就是視角）

手工步驟：寫下這位大師**實際**怎麼一步步看一支股——順序就是他的思維結構。每步綁
`look_at`（要看的 evidence 類別/欄位）或 `use_skill`（確定性技能）。

- **Buffett**：能力圈 → 護城河 → owner earnings → 安全邊際 → 經營與槓桿 → 結論。
- **Munger**：是不是好生意 → **invert（什麼會摧毀它）** → 品質 vs 價格 → 避免愚蠢 → 結論。

兩人流程**刻意不同調**：Munger 把「倒過來想致命傷」與「避免蠢事」獨立成步、且先於估值。
這讓兩位即使看同一份資料、用同一個 owner earnings，也走出不同推理路徑——這正是對抗
P1（mode collapse）的核心。

→ **Nüwa 對應**：extract 大師反覆描述的「分析順序/檢查清單」為有序 step；每步的 `look_at`/
`use_skill` 由 step 語意對映到 canonical 欄位與已註冊 skill。

## 5. skills.py — 確定性技能（絕不讓 LLM 算數）

手工步驟：找出大師依賴、且**能由現有 evidence 確定性算出**的計算，宣告 `requires`
（canonical 欄位）+ 套 `@skill`。共用計算（owner earnings 框架）放 `personas/skills_lib.py`，
各 Pack 的 `skills.py` 只宣告 requires 並 import——避免重複（E1 決議 a）。

- Buffett/Munger 共用：`owner_earnings`（NI + D&A − CapEx）、`owner_earnings_yield`
  （owner earnings / market_cap，安全邊際代理）。
- `requires` 缺欄位 → Runtime 記 `not_evaluable`，大師在 SOP pass 誠實說「這次看不到」。

→ **Nüwa 對應**：見下方 §7 開放問題 #1——E2 不應讓蒸餾產任意 Python；skill 改為
framework 具名白名單（issue #39），Nüwa 只「選用」與「設 requires」。

## 6. 欄位紀律（手工最容易踩的雷，Nüwa 必須自動把關）

rules / skills 引用的欄位名**必須**對上 provider 真實 emit 的 canonical 名，否則規則永遠
`not_evaluable`、skill 永遠降級——靜默失效。實作時踩到的具體點：

- 年度欄位是 `*_annual`：`net_income_annual` / `depreciation_amortization_annual` /
  `capex_annual`（**不是** Spec 草圖寫的 `capex`/`depreciation_amortization`）。
- 多年欄位單位是**百分比**：`roe_5y_avg` = `28.5`（28.5%），故門檻寫 `> 20` 非 `> 0.20`。
  `gross_margin_trend_5y` 單位 `%/yr`、`earnings_stability_5y` 值域 [0,1]。
- 台股無 `capex`/`D&A`/`market_cap` → owner earnings 系列在 2330 必為 not_evaluable
  （誠實，非 bug）；但多年 `roe_5y_avg` 等 PR2 已支援台股，moat 類規則對 2330 仍可評。

→ **Nüwa 對應**：consolidate 階段對 emit 的 canonical schema 做欄位名 + 單位校驗，抽出的
規則/技能引用不存在的欄位即報錯回修（validate gate 的一部分）。

## 7. 留給 E2 / 後續的開放問題

1. **skill 載入安全**（issue #39）：E1 用 `exec_module` 跑人工 skills.py；E2 Nüwa 自動產
   pack 前，skill 須改 framework 具名白名單 / DSL，移除任意程式碼執行路徑。
2. **多源驗證自動化**：手工階段「≥2 出處才進 rules」由人代行；E2 要在 extract→consolidate
   以 passage id 真正落實。
3. **非曆年制財年**（issue #43）：多年欄位對非 12/31 財年公司覆蓋率下降；pilot 不受影響。
4. **品質驗證**：E2 要把手工版 Buffett 與 Nüwa 蒸餾版對照（同 3 案例辯論、品質分數）。

## 8. E1 驗收對照

- [x] Buffett + Munger 手工 Pack 完成（含 skills）— 本 PR
- [x] 手工打造過程寫成 `docs/specs/` 附錄（本文件）= Nüwa 自動化規格輸入
- [x] confidence clamp 生效測試（觸發 ceiling/floor 的 evidence 下 LLM 信心被收口）—
      `tests/test_pilot_packs.py` + `tests/test_sage_runtime.py`
- [x] skill 輸出登錄為可溯源 private evidence、sop_trace 過 cite-check（軟揭露）
- [x] **跑 NVDA / 2330 各一 run，sop_trace 每步有 evidence 錨點** — 2026-06-14 完成
      （`--sages 4 --no-debate --no-macro --depth quick`，含兩 pilot pack；Munger weight 1.1
      為第 4 位，故 `--sages 4` 才同時上場）。

### Live run 結果（2026-06-14）

**NVDA（US）**：Buffett 6 步、Munger 5 步，**每步皆有 evidence 錨點**，含私有 skill 衍生
`S-buffett-owner_earnings`（owner earnings ≈ 116.9B）、`S-buffett-owner_earnings_yield`
（≈ 2.35%）。三段機制全在 production 兌現：
- **rule_conflict 揭露 + 不翻 stance**（決議 #1）：Buffett `wide-moat` 觸發（bullish）但他判
  neutral（現價對他不夠便宜）→ 記 `rule_conflicts`、stance 維持 neutral，未被程式硬翻。
- **unverified 軟揭露**（決議 #3）：Munger 2 條 sop_trace 數字未過 cite-check → 標記、不 refuse。
- 兩位結論一致主張「好生意 ≠ 好價格、owner earnings yield 偏低、安全邊際不足」——與其公開
  風格相符，且每句錨在 evidence。

**2330（TW）**：驗證 not_evaluable 與跨市場——
- Buffett/Munger 的 `owner_earnings`/`owner_earnings_yield` skill 正確降 `not_evaluable`
  並**列出缺哪些欄位**（台股無 `net_income_annual`/`depreciation_amortization_annual`/
  `capex_annual`/`market_cap`）——Buffett 誠實說「算不出台積電的 owner earnings」，非假裝。
- 多年 `roe_5y_avg` 等（PR2 台股支援）照常可評：Munger `quality-compounder` 規則觸發
  （bullish）但他判 neutral → rule_conflict 記錄、不翻 stance。
- 兩位 6/5 步 sop_trace 全部有 evidence 錨點。

→ 結論：E1 全 stack（Pack loader / DSL / skill / 三段執行 / clamp / not_evaluable /
sop_trace 軟揭露）在 US + TW 雙市場 live 驗證通過。runs/NVDA-2026-06-14_185644、
runs/2330-2026-06-14_190500。
