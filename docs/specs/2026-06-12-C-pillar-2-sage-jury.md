# Spec C — Pillar 2 陪審團品質

**Status**: accepted（2026-06-13 決議定案，見文末）
**Date**: 2026-06-12
**Dependencies**: A + B + **E1（Sage Runtime）**——council 結構在新 Runtime 之上實作，避免做兩次
**範圍變更**: 2026-06-13 — C-1（hard_rules）與 C-2（weight_rationale）**移入 Spec E**
（persona 行為規則屬 Persona Pack 的一部分，格式已在 E 定案為受限 DSL）。本 spec 剩 C-3 ~ C-6（P2/P3/P6/P7）。

## 背景

Pillar 2 的「健全有意義」靠兩件事：每位大師有清楚的行為準則（不是含糊的語氣描述）、陪審團結構鼓勵實質分歧。Audit 發現 10 個 yaml 都只有 `philosophy` / `focus` / `voice` / `weight` 四到五欄（buffett 9 行、taleb 9 行、damodaran 9 行、wood 9 行），沒有任何硬門檻；Council 用同一個 model 跑 10 個相似 prompt，統計稀釋幻覺的論點在實務上不成立（mode collapse 風險）；Debate 結構讓 bull 看不到 bear 的反駁，系統性偏袒空方。

這條 spec 把「語氣區分」升級到「行為區分」，並把陪審團結構對稱化。

## 涵蓋的 Gaps

- **P1**：persona 只有語氣沒有行為規則（`src/cyber_sages/personas/*.yaml`）
- **P2**：Council 同 model 統計稀釋論點不成立（`config.yaml:21` 全 sage 走同 model）
- **P3**：Debate 不對稱（`src/cyber_sages/agents/debate.py:90-108` 是 bull → bear → judge 順序，bull 看不到 bear）
- **P6**：Outlier 強制逐點反駁只在「敗方有 outlier」時觸發（`debate.py:35-42`）
- **P7**：4 個 neutral 大師被算進 consensus 卻不被視為 outlier（`src/cyber_sages/agents/council.py:123-129`）

## 範圍

### In scope

#### C-1. Persona 行為規則化

每個 yaml 加 `hard_rules` 區塊，列該大師硬擋的數值條件與信心調整項：

```yaml
hard_rules:
  - if: "pe_ratio > 25 and growth_rate_5y < 5%"
    action: "bearish"
    confidence_floor: 0.6
  - if: "debt_to_equity > 1.0"
    action: "bearish"
    confidence_ceiling: 0.5
  - if: "margin_of_safety > 30%"
    action: "bullish"
    confidence_floor: 0.7
exceptions:
  - "if company has dominant market share (>40%) and ROE > 20%, override P/E rules"
```

> 規則語法是草案，brainstorm 階段決定（DSL? 純 LLM 自然語言?）。

#### C-2. Weight rationale 寫進 yaml

每個 yaml 加 `weight_rationale: "<一句話解釋為何這個 weight>"`，後人改 weight 不會無據可循。

#### C-3. Council model 多樣化

兩階段策略：
1. **Cheap classifier**（同 model，small prompt）給每個 sage 跑一次，產粗 stance（bullish/bearish/neutral）+ 信心區間
2. **Deep sampler**（M3 / Claude）只挑出 consensus + outlier 兩組的 representative 各 3 位深入跑

> 理由：10 個大師全 deep 是奢侈；2 階段讓 70% 成本節省用在 outlier 深度攻防。

#### C-4. Debate 對稱化（雙盲）

順序改成：(a) bull 與 bear 同時收到「council 意見 + 對手是誰」，互不可見對手論點；(b) 兩方出完後，把對手論點餵給對方做 1 輪反駁；(c) 裁判看完整版。

> Trade-off：雙盲讓第一輪 bear 不知道 bull 具體論點，裁判判定難度提升。brainstorm 時要討論權衡。

#### C-5. Outlier 規則放寬到雙邊

`debate.py:_outliers_needing_rebuttal` 改成：無論 winner 是誰，**只要任一方有 outlier，敗方的 outlier 必須逐點反駁**。當 5B/4N/1S（贏方壓倒性）時，bear 的 1 個 outlier 也得被 bear 守住。

#### C-6. Neutral 保留為獨立訊號類別

Council tally：consensus 只看 bull vs bear，neutral 計入「無人敢表態」指標，brief 顯示 `neutral: 4 (基本面訊號不足或意見分歧)`。`outliers` 定義擴大為「非 consensus 立場」，中性可列入。

### Out of scope

- Chief / Judge prompt 重寫（Spec D）
- Prompt cache 設定（Spec B）
- 新增 persona（純資料新增，零程式碼，但留給未來）

## 驗收條件（草案）

- [ ] 10 個 persona yaml 都有 `hard_rules` + `exceptions` 區塊
- [ ] 每個 yaml 都有 `weight_rationale`
- [ ] Council 兩階段架構跑通，token 成本下降 ≥ 30%
- [ ] Debate 雙盲：bull / bear 第一輪互不可見對手論點
- [ ] Outlier 規則覆蓋 5B/4N/1S 場景
- [ ] Neutral 計入「無人敢表態」獨立指標
- [ ] 不同 model 在相同 evidence 下結論相關性 < 0.7（測 mode collapse）

## 決議（2026-06-13，DK 授權按最優解定案）

1. **hard_rules 用受限 DSL + 自然語言 exceptions**（移至 Spec E 定案）：
   `field/op/value` 三元組 + `all`/`any` 巢狀，程式對 evidence 求值零幻覺；
   彈性由 exceptions（LLM 裁量但強制引用 evidence）補回。
2. **cheap classifier 不綁定特定 model**：config.yaml 加 `sage_scout` role，
   由部署者指定（預設指向當前最便宜可用端點）。模型選擇是 config 的事，
   不進 code——與 gateway 的 role 路由哲學一致。
3. **雙盲裁判看完整版**：雙方兩輪的完整論點（含第一輪、含對 outlier 的反駁）
   都給裁判。裁判 token 成本上升換裁定品質，值得；裁判只跑一次，成本可控。
4. **兩階段 Council 各階段內部平行**：scout 全員平行 → 程式分組（consensus /
   outlier representatives）→ deep 組內平行。總 latency ≈ 兩個串行 LLM 往返，
   可接受；不做 scout/deep 交錯的複雜編排。
5. **neutral 細分**：`SageSignal` 加 `neutral_reason` enum——
   `out_of_circle`（能力圈外，SOP 第一步觸發）/ `insufficient_signal`（關鍵
   evidence 缺失或 not_evaluable 過多）/ `balanced_forces`（多空證據相當）。
   tally 分開計數，brief 分開呈現——「4 位看不懂」與「4 位認為多空拉鋸」是
   完全不同的訊號。

## 相關檔案

- `src/cyber_sages/personas/*.yaml`（10 個檔案）
- `src/cyber_sages/agents/council.py:32-39, 111-134`
- `src/cyber_sages/agents/debate.py:35-42, 78-108`
- `config.yaml:21`

## 參考

- 2026-06-12 全專案 audit 紀錄
- Issue #2：Cyber-Nüwa 蒸餾引擎（persona 設計的更高層次）
