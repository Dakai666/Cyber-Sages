# Spec B — Pillar 1 管線硬化

**Status**: accepted（2026-06-13 決議定案，見文末）
**Date**: 2026-06-12
**Dependencies**: A（先有資料才能驗證）
**範圍變更**: 2026-06-13 — W3（council cache）與 W5（silent exception）**前置到 Phase 0**
（cache 越早做後續每個 Phase 的迭代越便宜；W5 保護 Spec A 動資料層）。本 spec 剩 W4 / W7 / W8 / W9。

## 背景

Pillar 1 的反幻覺哲學靠「閘門 + 確定性 + 結構化 log」撐起，但 audit 發現：(1) 多處 silent `except Exception: pass` 讓 schema 變更無聲失敗；(2) Council 階段 10 個 sage 拿到同一份 shared_prompt 計費 10 次，prompt cache 完全沒用上；(3) Chief 寫的 brief 主體是 Pillar 1 唯一未經 cite-check 的環節。

這條 spec 把「該擋的擋下、該省的省下、該 log 的 log 出來」整個補完。

## 涵蓋的 Gaps

- **W3**：Council prompt cache 沒用（`src/cyber_sages/agents/council.py:97` + `src/cyber_sages/llm/gateway.py:64-138`）
- **W4**：cite-check 笛卡兒積過寬（`src/cyber_sages/verify/citation_check.py:94-102`）+ 符號不對稱（`(a - b) / abs(b) * 100` 讓變動率永遠正）
- **W5**：6 處 silent exception（`us_stocks.py:85, 212, 243` + `tw_stocks.py:110, 123, 329`）
- **W7**：Chief thesis 整段沒 cite-check（`src/cyber_sages/agents/synthesis.py:98-100`）
- **W8**：macro 過期 4 個月只 warning（`src/cyber_sages/verify/data_audit.py:108-117`，`max_macro_age_days=45` 太鬆）
- **W9**：部分 collector 失敗不觸發降級（`src/cyber_sages/pipeline.py:108-114` 沒把 missing categories 傳給 audit）

## 範圍

### In scope

- **silent failure → log + audit warning**：6 處 `except: pass` 改為 `logger.warning(...)` + 把失敗的 evidence category 加到 audit findings
- **audit expected-categories 缺失 → error**：`pipeline.py` 收集完傳 missing list 給 audit，缺 quote/history/fundamentals 等核心類別就 error
- **Council user-message cache_control**：gateway 加 `cache_control` 到 user prompt 的最後一段（evidence digest 結尾），讓 Anthropic provider 自動 cache
- **cite-check 笛卡兒積收斂**：
  - 加 evidence 配對白名單（如 revenue × margin_ratio → net_income 是合理的；但 P/E × shares × EPS 算股價就不該配）
  - 符號不對稱：把 `(a - b) / abs(b) * 100` 改成同時接受 `(a - b) / b` 與 `(b - a) / a`，讓 LLM 寫「下跌 5%」對得上 evidence 的正 5%
- **Chief thesis cite-check**：synthesis 加「thesis 與 risks / what_would_change_my_mind 內的數字都過 `_check_claim`」步驟
- **macro freshness 收緊**：`max_macro_age_days` 預設 45 → 60（monthly + buffer）

### Out of scope

- Council model 多樣化（Spec C）
- Chief / Judge prompt 重寫（Spec D）
- 結構化 log library 選型（先用 stdlib logging，後續評估）

## 驗收條件（草案）

- [ ] 6 處 silent except 全部移除，0 處 `except: pass`
- [ ] collect 階段缺核心類別（quote / history / fundamentals）→ audit error → degraded
- [ ] Council 10 個 sage 的 prompt token 成本下降 ≥ 60%（Anthropic cache hit）
- [ ] cite-check 偽造率測試：刻意構造「下跌 5%」對正向 evidence，必須 verified
- [ ] chief.thesis / risks / what_would_change_my_mind 內的數字 100% 過 cite-check
- [ ] macro 過期 60 天才 warning（行為與預期一致）

## 決議（2026-06-13，DK 授權按最優解定案）

1. **log 用 stdlib `logging`**。專案規模還不需要 structlog；等有集中式 log
   消費需求再評估，現在加是過度設計。
2. **cite-check 配對白名單手列**。配對規則少（合理衍生組合一隻手數得完），
   手列可審查、可測試；「自動學習」對反幻覺閘門而言是引狼入室。
3. **缺漏分級**：`quote` / `history` / `fundamentals` 缺 → **error → degraded**
   （核心三類）；`news` / `chips` / `macro` 缺 → warning + brief 強制揭露。
   分級表寫成模組常數，不散落在 if 裡。
4. **cache breakpoint 在 shared_prompt 尾端**（evidence digest 結尾處）。
   persona 差異全在 system prompt，user prompt 是共享前綴——breakpoint 放尾端
   讓整段 digest 命中 cache（Anthropic 前綴比對）。Phase 0 已前置實作。
5. **chief cite-check 失敗**：retry 1 次（驗證錯誤回饋進 prompt，與 analyst
   階段同機制）→ 仍失敗則標 `unverified` 並在 brief 揭露。不 refuse——
   degraded-but-disclosed 一貫優於 silent failure 或整條 run 報廢。

## 相關檔案

- `src/cyber_sages/agents/council.py:97`
- `src/cyber_sages/agents/synthesis.py:98-100`
- `src/cyber_sages/llm/gateway.py:64-138`
- `src/cyber_sages/pipeline.py:108-114`
- `src/cyber_sages/verify/citation_check.py:94-102`
- `src/cyber_sages/verify/data_audit.py:108-117`
- `src/cyber_sages/data/us_stocks.py:85, 212, 243`
- `src/cyber_sages/data/tw_stocks.py:110, 123, 329`

## 參考

- 2026-06-12 全專案 audit 紀錄
- Issue #1（已併入 PR #3）：引用驗證容錯
