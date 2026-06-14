# Spec E — Sage Runtime + Cyber-Nüwa 蒸餾引擎

**Status**: accepted（設計定案，分 E1 / E2 兩階段實作）
**Date**: 2026-06-13
**Dependencies**: E1 依賴 Spec A + B（鐵律 1：資料正確、閘門收口才值得燒專家 token）；E2 依賴 Spec C（驗證機制用 council）
**取代**: Spec C 的 C-1（hard_rules）、C-2（weight_rationale）移入本 spec；issue #2 三個設計問題在此定案

## 背景與定調

> 「女媧,打造與蒸餾專家出來,如果只有單純的 personas.yaml 是不足的,我期望的是
> 每個專家得有內涵自己獨特視角與思想,然後落實內建的 sop 與 workflow,
> 簡單來說就是內建獨特輕量技能,並且能針對性地處理前面階段所獲取的資料與數據」
> —— DK 2026-06-13

現況：sage = 同一份 shared_prompt + 不同 system 語氣（`council.py:42-58`）。十位
大師看同樣的資料、走同樣的（隱性）流程、用同一個 model——區別只剩文風。這就是
P1（mode collapse 溫床）的根源，也是「persona 精煉度是專案靈魂」（issue #2）卡住
的地方。

本 spec 把 persona 升級為 **Persona Pack**（可執行的專家），分兩階段：

- **E1 — Sage Runtime**：Pack 格式 + 執行引擎 + 2 位手工試點（Buffett、Munger）
- **E2 — Cyber-Nüwa**：從大師真實文本自動蒸餾出 Pack 的引擎 + 其餘 8 位遷移

## E1：Persona Pack 與 Sage Runtime

### Pack 結構

```
personas/
  buffett-2008/            ← 目錄名 = key + epoch（時點鎖定，見 Q3 決議）
    persona.yaml           # 身分：key/name/epoch/weight/weight_rationale/
                           #   philosophy/focus/voice/sources（蒸餾來源 manifest）
    rules.yaml             # 硬規則 DSL + 自然語言 exceptions
    sop.yaml               # 決策工作流：有序步驟，每步綁 evidence 欄位與 skill
    skills.py              # 選配：該大師專屬的確定性計算
  taleb.yaml               ← 舊單檔格式向後相容（degraded persona，無 pass 1/2）
```

### rules.yaml — 受限 DSL（決議：不用自然語言當硬規則）

```yaml
hard_rules:
  - id: no-leverage
    if: {field: debt_to_equity, op: ">", value: 1.0}
    action: cap_confidence       # bearish_floor / bullish_floor / cap_confidence
    confidence_ceiling: 0.5
    note: "槓桿過高出我的能力圈"
  - id: moat-premium
    if: {all: [{field: roe_5y_avg, op: ">", value: 20},     # roe_5y_avg 單位為 %（28.5=28.5%），故 20 非 0.20
               {field: gross_margin_trend_5y, op: ">=", value: 0}]}
    action: bullish_floor
    confidence_floor: 0.6
exceptions:                      # 自然語言，由 LLM 在 SOP pass 裁量，但必須引用 evidence
  - "若具壟斷性市占（>40%）且 ROE > 20%，P/E 規則可被推翻——說明引用哪些 evidence"
```

**為何 DSL 而非自然語言**：(1) 條件直接對 EvidenceStore 欄位求值，**程式判定、
零幻覺**——這是 Cyber-Sages 哲學的直接延伸；(2) Nüwa 蒸餾變成「文本 → 結構化
規則」的抽取任務，產出可被驗證；(3) 彈性由 `exceptions`（自然語言、LLM 裁量但
強制引用 evidence）補回。`if` 支援 `all` / `any` 巢狀與 `field/op/value` 三元組，
僅此而已——刻意保持極簡，欄位名對齊 Spec A 的 canonical 命名。

規則引用的欄位缺資料時：該條規則記為 `not_evaluable`（不觸發、不假裝通過），
清單注入 SOP pass 讓大師知道「我平常會看的東西這次看不到」，並在 signal 上
列出——這本身就是有意義的訊號（Buffett 看不到 owner earnings 該說出來）。

### sop.yaml — 大師的決策工作流

```yaml
sop:
  - step: circle-of-competence
    ask: "這家公司的生意模式我是否真的懂？"
    look_at: [profile, fundamentals.revenue_*]
    on_fail: "stance=neutral, neutral_reason=out_of_circle, 提前收尾"
  - step: moat
    ask: "護城河在哪？正在變寬還是變窄？"
    look_at: [fundamentals.gross_margin_*, fundamentals.roe_*, news]
  - step: owner-earnings
    ask: "真實的 owner earnings 是多少？"
    use_skill: owner_earnings          # ← skills.py 的確定性輸出
  - step: margin-of-safety
    ask: "以 owner earnings 看，現價給了多少安全邊際？"
    use_skill: margin_of_safety
  - step: verdict
    ask: "綜合以上，按我的哲學下結論。"
```

SOP pass 的 prompt 按步驟展開，**要求 LLM 逐步作答、每步引用 evidence id 或
skill 輸出**，輸出 schema 加 `sop_trace: list[SopStep]`（步驟結論 + 引用），
與既有 cite-check 對接。這讓每位大師「針對性地處理前面階段的資料」——Buffett
走能力圈→護城河→owner earnings，Burry 走共識盲點→債務結構→下檔不對稱，
**流程本身就是視角**。

### skills.py — 獨特輕量技能（確定性，絕不讓 LLM 算數）

```python
# personas/buffett-2008/skills.py
from cyber_sages.personas.skill import skill, SkillResult

@skill(requires=["net_income_annual", "depreciation_amortization", "capex"])
def owner_earnings(ev) -> SkillResult:
    val = ev["net_income_annual"] + ev["depreciation_amortization"] - ev["capex"]
    return SkillResult(value=val, formula="NI + D&A - CapEx",
                       inputs=["E012", "E015", "E016"])
```

- Runtime 在 LLM 之前執行 skills，輸出登錄為 **sage-private derived evidence**
  （帶公式與輸入 evidence ids，完全可溯源，可被 cite-check 驗證）
- `requires` 欄位缺 → skill 記 `not_evaluable`，同 rules 處理
- 典型 skills：Buffett `owner_earnings`/`margin_of_safety`、Graham
  `net_net_value`/`graham_number`、Lynch `peg`、Damodaran `mini_dcf`（用 Spec A
  的 WACC inputs）、Burry `debt_maturity_pressure`、Taleb `tail_exposure`
  （波動率分布偏度）
- 這正是 Spec A「欄位優先序由 persona skill 需求反推」的出處——**skills 的
  `requires` 清單就是 Spec A 的 P0 欄位清單**

### Runtime 三段執行（取代 council.py 的單發 prompt）

```
per sage:
  1. skill pass   （程式）skills.py 對 EvidenceStore 求值 → private derived evidence
  2. rule pass    （程式）rules.yaml DSL 求值 → triggered / not_evaluable 清單
  3. SOP pass     （LLM） system = persona 身分；prompt = shared digest（cache 前綴）
                          + skill 輸出 + 觸發規則 + SOP 步驟 → SageSignal + sop_trace
  4. clamp        （程式）confidence 受 triggered rules 的 floor/ceiling 約束
                          ——不信 LLM 自律，程式收口
```

向後相容：舊單檔 yaml 載入為「無 skills / 無 rules / 無 SOP」的 degraded
persona，走原本單發 prompt。`load_personas` 同時認得目錄與單檔，漸進遷移。

### E1 驗收條件

- [ ] Pack loader：目錄格式與舊單檔共存，`--sages N` 截斷邏輯不變
- [ ] rules DSL evaluator 純函式 + 完整測試（all/any 巢狀、not_evaluable、clamp）
- [ ] skill 框架：`@skill(requires=...)` 註冊、缺欄位降級、輸出登錄為可溯源 evidence
- [ ] SOP pass 輸出 `sop_trace`，trace 內數字過 cite-check
- [ ] Buffett + Munger 手工 Pack 完成（含 skills），跑 NVDA/2330 各一 run，
      signal 的 sop_trace 每步都有 evidence 錨點
- [ ] 手工打造過程寫成 `docs/specs/` 附錄（= Nüwa 自動化規格的輸入）
- [ ] confidence clamp 生效測試：構造觸發 ceiling 的 evidence，LLM 給 0.9 也被壓到 ceiling

## E1 實作決議（2026-06-14 DK 定案）

設計層 spec（上）定方向；下列七項定**實作叉路**，由 DK 逐項拍板，作為 Phase 3
三條 PR 的施工依據。

| # | 決議 | 拍板 | 理由 |
|---|---|---|---|
| 1 | rules directional floor 語意 | **只夾 confidence、不翻 stance** | `cap_confidence`（ceiling）一律程式硬收；`bullish_floor`/`bearish_floor` 只在 LLM 已同向表態時套 confidence floor，反向時保留 LLM 的 stance、把「規則 vs LLM 衝突」記入 signal 揭露。維持「LLM 推理為主、程式收口為輔」，避免程式硬翻 stance 製造假確定性。 |
| 2 | skill private evidence 儲存 | **獨立命名空間掛 sage 名下**（`S-<key>-NNN`） | 不混入共享 `E001…` 序列；每位 sage 只看自己的 private + 共享 store。避免 10 位各算各的污染 digest、破壞 E-id 穩定性與 cache 前綴不變性。cite-check 驗證時組「共享 items + 該 sage private items」暫時 store view 複用既有 `check_claims`，零改動 citation_check.py。 |
| 3 | sop_trace cite-check 強制度 | **軟揭露**（比照 PR #27 chief brief） | sop_trace 數字過 cite-check，失敗步驟標 `unverified` 並揭露，retry 1 次、不 refuse。sage 推理貴，逐 sage 硬重跑會爆 token。 |
| 4 | epoch 機制 | **目錄帶 epoch、loader 多版本選取延後 E2** | 目錄名即 `<key>-<epoch>/`、`persona.yaml` 帶 `epoch`，示範 Nüwa 最終格式；但「同 key 取最新 epoch」的選取邏輯 E1 不做（單一 epoch）。 |
| 5 | 多年衍生欄位 | **E1 補齊 pilot 規則所需多年欄位** | Buffett/Munger 招牌規則需 `roe_5y_avg`/`gross_margin_trend_5y`/`earnings_stability_5y` 等多年訊號（SEC companyfacts 多年原料已在、TW 對齊）。Pack 規則才有「護城河變寬變窄」的真實內涵。 |
| 6 | PR 拆分 | **三條：框架 → 多年欄位資料 → pilot Pack** | 三個關注點各自乾淨可獨立回溯；資料層擴充屬純 Spec A 性質可先合。 |
| 7 | epoch 年代 | **Buffett-2019 / Munger-2019** | Apple 時期的 Buffett（對科技更開放、已調整「不懂科技」立場），最貼近當代 NVDA 類標的；lookahead bias 由未來 `replay` 報告固定揭露（見 E2 Q2）。 |

兩項由實作端預設、可隨時推翻：(a) `skills.py` 每 Pack 各一份（符合 spec 目錄結構），共用計算（`owner_earnings`/`margin_of_safety`）放框架當 importable helper；(b) private evidence id 採 `S-<key>-<skill_name>`（如 `S-buffett-owner_earnings`）。

> **PR1 review 後修訂（2026-06-14，B4）**：private evidence id 由原訂流水號 `S-<key>-NNN`
> 改為 **`S-<key>-<skill_name>`**。流水號會在某 skill `not_evaluable` 被跳過時產生 gap、
> 並隨「哪些 skill 算得出」重排，使 sop_trace 引用的 id 跨 run/配置漂移；以 skill 名為 id
> 則穩定、自我說明（單一 Pack 內 skill 名天生唯一）。其餘 PR1 review 修正（B2 HardRule
> action fail-loud 驗證、B6/B7 清理）見 PR #38；B1/B3/B9 開 issue 追到 E2 開工前。

**三條 PR 範圍**：

- **PR1 — Sage Runtime 框架（data-agnostic 純機制）**：Pack loader（目錄 `<key>-<epoch>/` 與舊單檔共存、`--sages N` 截斷不變）；rules DSL evaluator（純函式 + 完整測試，`field/op/value` + `all`/`any` 巢狀、`not_evaluable`、clamp）；skill 框架（`@skill(requires=...)`、`SkillResult(value, formula, inputs)`、缺欄位降級、輸出登錄為可溯源 private evidence）；`SageSignal` 加 `sop_trace`/`not_evaluable`/規則衝突揭露；三段執行 + clamp 取代 council 單發 prompt；degraded 8 位舊 yaml 走原路徑；sop_trace 軟揭露 cite-check 接線。
- **PR2 — 多年衍生欄位**：SEC 多年 annual → `roe_5y_avg`/`gross_margin_trend_5y`/`earnings_stability_5y`（最終清單由 pilot 的 rules+skills `requires` 聯集反推）；TW FinMind 對齊；確定性計算、canonical 命名；audit 不列必需。

> **PR2 落地（2026-06-14）**：共享純模組 `data/longterm.py` 算三個多年指標，
> US（SEC companyfacts 多年 10-K，dedup by fiscal-year-end）與 TW（FinMind 季度聚合成
> 年度，抓取窗加寬至 `days_ago(2000)`、同一 request 不增配額）各自抽逐年序列後呼叫。
> **單位口徑**（PR3 寫 rules 必讀）：
> - `roe_5y_avg` 單位 **`%`**（如 `28.5` = 28.5%，與單期 `roe_pct` 一致）——故 Buffett
>   moat 規則應寫 `roe_5y_avg > 20`，**非** spec 草圖的 `> 0.20`。
> - `gross_margin_trend_5y` 單位 **`%/yr`**（OLS 線性斜率，%點/年；`>= 0` = 毛利未走弱）。
> - `earnings_stability_5y` 無單位、值域 **[0,1]**（`max(0, 1 − σ/μ)`，1=最穩；μ≤0 記 0）。
>
> 寧缺勿錯：序列 < 3 年該指標不發 → 下游 rule/skill 記 `not_evaluable`（2330 早年資料
> 不足時 Buffett 會誠實說「看不到台積電的 5 年趨勢」）。多年欄位 `as_of` = 最新會計年度
> 底，與既有 `*_annual` 同 as_of，受同一 500 天 stale 門檻、不引入新降級風險。
- **PR3 — Buffett-2019 + Munger-2019 手工 Pack**：各 `persona.yaml`（含 `weight_rationale` + `sources` manifest）/`rules.yaml`/`sop.yaml`/`skills.py`；跑 NVDA + 2330 各一 run 驗 sop_trace 錨點 + clamp 生效測試；手工過程寫成 `docs/specs/` 附錄（= Nüwa 規格輸入）。

> **PR3 落地（2026-06-14）**：兩 Pack 完成於 `personas/buffett-2019/`、`personas/munger-2019/`；
> 舊單檔 `buffett.yaml`/`munger.yaml` 退役（migrate 成目錄 Pack，loader 無重複）。共用計算
> （`owner_earnings`/`owner_earnings_yield`）抽到 `personas/skills_lib.py`、各 Pack 的
> `skills.py` 只宣告 `requires` 並 import（決議 a）。整合測試以**真實 Pack 檔** + mock gateway
> 證明三段機制（skill 真算 owner earnings、rules 觸發、clamp 把低信心抬到 floor、台股式資料
> 降 not_evaluable、sop_trace 軟揭露）。手工打造全程寫成
> `docs/specs/2026-06-14-E1-pilot-pack-handcrafting.md`（= E2 Nüwa 要自動化的規格）。
> **剩 live NVDA/2330 質性驗證**（需 LLM API，`uv run cyber-sages analyze NVDA --sages 2`）。

## E2：Cyber-Nüwa 蒸餾引擎

### 定位（issue #2 命名提案決議）

**兩者都要**：`cyber-sages distil` CLI 子命令（Nüwa 角色的執行入口）+ 蒸餾 SOP
文件（任何 agent 可手動執行同一流程）。Nüwa 不是常駐 pipeline 角色——它是
**離線的 persona 工廠**，產出進 git、由人 review 後才上線。

### Q1 決議：蒸餾來源

每位大師指定**主要來源**（寫進 persona.yaml `sources` manifest），輔以次要來源
做交叉驗證：

| 大師 | 主要來源 | 理由 |
|---|---|---|
| Buffett | 股東信（1977–epoch 年） | 第一手、量大、含決策理由 |
| Munger | Poor Charlie's Almanack + BRK 年會 Q&A | 辯論紀錄訊號密度最高 |
| 其他 8 位 | E2 時逐一定，原則同上：本人第一手文本 > 辯論紀錄 > 他人拆解 | |

**多源交叉驗證為硬 gate**：同一條 hard_rule 候選，至少 2 個獨立出處才能進
rules.yaml；單一出處的進 `exceptions` 或捨棄。「他人對大師的拆解」只用於
查漏，不作為規則出處（易失真）。

### 蒸餾管線（蒸餾本身也要可溯源——與主 pipeline 同一哲學）

```
ingest       文本清洗、分段、編 passage id（P001…）
extract      LLM 抽取候選：hard_rules / SOP steps / exceptions / 哲學要素
             ——每條候選必須附 passage id，無出處者直接丟棄
consolidate  跨源一致性檢查（同主題候選互相矛盾 → 標記給人裁決）
             + 規則去重 + DSL 欄位名對齊 canonical schema
emit         產出 Pack 草案（persona.yaml sources 寫入完整 manifest）
validate     見 Q2
```

### Q2 決議：可驗證性——以 C 路徑為主，重放為輔

1. **同儕共識驗證（主）**：新 Pack 進 council 與既有大師辯論 3 個歷史案例，
   檢查 (a) sop_trace 每步有 evidence 錨點、(b) 立場與該大師已知公開立場不矛盾、
   (c) 辯論中被對手指出的矛盾數。產出 **persona 品質分數** 寫進 PR description。
2. **最小重放（輔）**：`cyber-sages replay TICKER --as-of DATE`——把 evidence
   截斷在歷史時點重跑 pipeline。lookahead bias（model 訓練資料看過事後結果）
   **無法消除，只強制揭露**：replay 報告固定標注此限制。
3. 盲測對照（issue #2 的 B 路徑）成本高、取樣難，**不做**，留待未來。

### Q3 決議：版本管理——時點鎖定 + git

- 目錄名 = `<key>-<epoch>`（如 `buffett-2008/`），`persona.yaml` 有 `epoch` 欄位
- **第一輪全部用單一「成熟期」epoch**（來源文本截至該年），不做多時點並存
- 演化 = 重大事件（持倉重大翻轉 / 公開立場翻轉）觸發**新目錄**（如
  `buffett-2023/`），舊 Pack 保留可叫用——版本機制就是 git + 命名慣例，
  不另造輪子
- 同 key 多 epoch 並存時，`load_personas` 預設取最新 epoch，CLI 可指定

### E2 驗收條件

- [ ] `cyber-sages distil --persona buffett --sources <manifest>` 跑通全管線
- [ ] 蒸餾產出的每條 rule / SOP step 都有 passage id 出處（抽查 100% 通過）
- [ ] Nüwa 蒸餾版 Buffett vs E1 手工版 Buffett 對照（同 3 案例辯論，品質分數不顯著低於手工版）
- [ ] 其餘 8 位完成遷移，舊單檔 yaml 全數退役
- [ ] `cyber-sages replay` 最小重放可用（同時補 Spec D 的回測驗收）
- [ ] 蒸餾 SOP 文件化（非 CLI 路徑也能人工執行）

## Out of scope

- 常駐線上演化（自動監聽大師新聞觸發 re-distil）——未來
- 跨資產 persona（crypto 大師等）——未來
- 完整回測器（勝率統計）——只做最小重放

## 未來點子（記錄，不在 E1/E2 範圍）

- **跨 epoch 同人對決**（DK 2026-06-14）：`buffett-2008/` vs `buffett-2019/` 同席合議
  或辯論——同一位大師、兩個成熟期，看金融海嘯前後的判準差異如何裁同一支股。
  這是 Q3「同 key 多 epoch 並存 + git 版本機制」的殺手級應用，等 E2 Nüwa 能量產
  多 epoch Pack、且 loader 的多版本選取/指定邏輯落地後才開。

## 相關檔案

- `src/cyber_sages/agents/council.py:23-58`（Persona model + SAGE_SYSTEM，E1 重構點）
- `src/cyber_sages/personas/*.yaml`（10 個舊格式檔）
- `src/cyber_sages/data/evidence.py`（derived evidence 登錄）
- `src/cyber_sages/verify/citation_check.py`（sop_trace 對接）
- `config.yaml`（E2 加 `distiller` role）

## 參考

- Issue #2（本 spec 為其三問的定案）
- Spec A（P0 欄位清單 = skills 的 requires 聯集）
- Spec C（council 結構在 Runtime 之上實作）
- 2026-06-13 DK 定調對話
