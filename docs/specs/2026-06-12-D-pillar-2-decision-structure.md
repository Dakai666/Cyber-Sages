# Spec D — Pillar 2 決策結構

**Status**: accepted（2026-06-13 決議定案，見文末）
**Date**: 2026-06-12
**Dependencies**: A + C
**範圍變更**: 2026-06-13 — 驗收條件「至少 5 個回測案例」移到 Phase 6（Spec E2 的最小重放工具落地後補驗），不擋本 spec 收口

## 背景

Pillar 2 的最後一環是「首席合議 + 風控官覆核」。Audit 發現：(1) Chief 與 Judge 的 prompt 都沒要求 evidence id 引用，brief 主體（chief 寫的 thesis 整段）與裁定 rationale 是 Pillar 1 反幻覺哲學的**唯一漏網之魚**；(2) Risk officer 只能扣 conviction 不能加，chief 偏保守的狀況修不回來。

這條 spec 補完決策結構的最後一塊。

## 涵蓋的 Gaps

- **P4**：Chief + Judge prompt 不要求 evidence id 引用
  - `src/cyber_sages/agents/synthesis.py:17-41`（CHIEF_SYSTEM 沒要求 evidence 引用）
  - `src/cyber_sages/agents/debate.py:22-32`（JUDGE_SYSTEM 同上）
- **P5**：Risk officer 單向
  - `src/cyber_sages/agents/synthesis.py:115` `adj = max(-0.3, min(0.0, ...))`

## 範圍

### In scope

#### D-1. Chief prompt 強制 evidence id 引用

CHIEF_SYSTEM 加規則：
- `thesis` 內每個量化宣稱必須附 evidence id，否則 schema 驗證失敗
- `key_risks`、`what_would_change_my_mind` 同步套用
- 至少引用 3 個 evidence id（避免 chief 寫純敘事）

> 配套：synthesis 加 `cite_check_chief_thesis()` 步驟，用既有 `_check_claim` 驗 chief 的 thesis 文字。

#### D-2. Judge prompt 強制 evidence id 引用

JUDGE_SYSTEM 加規則：
- `rationale` 內每個量化宣稱必須附 evidence id
- `outlier_rebuttals[].rebuttal` 已有（line 29-32 強制），補上 evidence id 要求

#### D-3. Risk officer 雙向

`risk.conviction_adjustment` 從 `[-0.3, 0.0]` 改成 `[-0.3, +0.2]`：
- 負向：chief 過度樂觀、風險未充分揭露
- 正向：chief 偏保守（且證據強）、risk 願意補信心

理由：正向上限 0.2 < 負向下限 0.3 的絕對值，維持「risk 偏保守」的總體傾向（這是風控官的角色定位），但允許小幅修正。

#### D-4. Chief ↔ Risk 單輪迭代

當 risk 給出重大質疑（`risk.conviction_adjustment ≤ -0.2`），chief 自動做 1 輪回應（看到 risk 質疑後重寫 thesis 的關鍵段）。LLM 評估「chief 是否已回應 risk 質疑」，未回應則繼續 retry。

> 動態迭代 vs 固定 1 輪是 trade-off：固定 1 輪簡單可控、動態 1-2 輪品質更高但成本與延遲上升。

### Out of scope

- Council 結構（Spec C）
- Pipeline 硬化（Spec B）
- Brief 模板（Spec 之外，留給未來場景化時）

## 驗收條件（草案）

- [ ] chief.thesis / risks / what_would_change_my_mind 內的數字 100% 過 cite-check（與 Spec B-7 同驗收，可併）
- [ ] judge.rationale / outlier_rebuttals 100% 過 cite-check
- [ ] risk.conviction_adjustment 範圍為 `[-0.3, +0.2]`
- [ ] chief ↔ risk 迭代在 risk 質疑重時（≤ -0.2）觸發
- [ ] 至少 5 個回測案例：risk 雙向後整體 conviction 預測力提升

## 決議（2026-06-13，DK 授權按最優解定案）

1. **引用密度取段落級而非逐句**：thesis 每個段落至少 1 個 evidence id、全文
   至少 3 個（防純敘事），但敘事連接句不強制——保留整合空間，量化宣稱則
   一律要錨點（這部分本來就由 cite-check 把關，不靠 prompt 自律）。
2. **chief ↔ risk 固定 1 輪**。簡單可控、成本可預測；動態多輪等 Phase 6
   重放工具能量化「多一輪值多少」之後再評估。
3. **risk 上調必須標註**：brief 顯示「風控官上調 +0.x（理由）」。對稱性原則
   ——下調有揭露，上調也要；讀者（人類或 agent 法官）有權知道 conviction 的
   每一段來歷。
4. **引用寫法用行內 `[E001]`**。機器可 parse（cite-check 直接吃）、視覺輕量；
   brief 渲染時 report.py 可把 id 連到 evidence.json 條目，敘事性 anchor 由
   渲染層生成，不要求 LLM 寫。

## 相關檔案

- `src/cyber_sages/agents/synthesis.py:17-49, 115`
- `src/cyber_sages/agents/debate.py:22-32`
- `src/cyber_sages/agents/schemas.py`（FinalVerdict / DebateVerdict / RiskNote schema）

## 參考

- 2026-06-12 全專案 audit 紀錄
- Issue #1（已併入 PR #3）：引用驗證容錯
