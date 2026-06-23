---
name: forge-sage
description: 打造或遷移一位投資大師 Persona Pack（persona/rules/sop/skills）。當要新增一位大師、把舊單檔 `personas/*.yaml` 升級成完整 Pack、補齊半 Pack（缺 rules/skills）、或檢視一個 Pack 是否合格時使用。這是 Cyber-Nüwa 蒸餾流程的「人/agent 可執行版」——把 12 個手工 Pack 累積的方法論固化成可重複的 SOP。
---

# Forge-Sage — Persona Pack 打造 SOP

> **本 skill 的定位**：Roadmap 原本規劃一個 `cyber-sages distil` 蒸餾「引擎」（Cyber-Nüwa）來自動量產 Pack。後來想清楚：這個專案一半是給 AI agent 用的，**過往每一位大師都是 agent（Claude Code）讀文本、手工打造的——蒸餾者本質上就是讀這份 SOP 的 agent（你）**。所以不蓋引擎，把方法論寫成這份 agent 原生的 skill：任何 agent 讀完就知道怎麼 forge 一位大師。「機器消費 SOP」是多餘的一層，因為消費者已經會讀 SOP。

一個 **Persona Pack ＝ 可執行的專家**，不是語氣檔。差異化不靠「獨特工具」，而靠**規則門檻 + 流程順序 + 裁量風格**——Buffett 與 Munger 用同一把尺（owner earnings），量出不同結論。

---

## 0. 先決定：這位大師「值不值得」做成 Pack，以及做到哪一層

不是每位大師都要四件套。先判斷他的招牌判準**有沒有對得上的 canonical evidence 欄位**：

| 情境 | 該做的 Pack 形態 | 範例 |
|---|---|---|
| 招牌原則可量化、且欄位存在 | 完整 Pack（persona+rules+sop+skills） | Buffett, Munger, PTJ, Minervini |
| 招牌原則可量化、但需衍生欄位（DSL 表達不了） | 先寫 skill 算衍生欄位，rules 再對它下判定 | PTJ（見 §5 rule↔skill pattern） |
| 核心變數目前**不是** evidence 欄位（如分部加總、董事會結構） | **純 SOP Pack**（不附 rules/skills），並在 `weight_rationale` 寫明「為何不硬寫 DSL」 | Icahn（治理/SOP 資料缺）, Son, Soros, Trump |
| 還沒打算手工投入 | 留舊單檔 `<key>.yaml`（degraded，走 council 原單發 prompt） | burry, graham, lynch, taleb, wood… |

**鐵律**：欄位不存在就**不要硬寫 hard rule**——那只會製造永遠 `not_evaluable` 的死規則，或更糟，無依據的判斷。寧可純 SOP，把判準留給 LLM 在 SOP pass 裡引用 evidence 裁量。Icahn 的 `weight_rationale` 是這個取捨的範本。

---

## 1. 輸入：第一手文本（sources manifest）

定主要來源，寫進 `persona.yaml` 的 `sources`。優先序：**本人第一手文本 > 辯論/訪談紀錄 > 他人拆解**。「他人對大師的拆解」只用於查漏，**不**作為硬規則的出處（易失真）。

**多源驗證（硬 gate）**：一條 `hard_rule` 候選，心裡要有**至少 2 個獨立出處**才放進 `rules.yaml`；單一出處的、或有彈性的，降級進 `exceptions`（自然語言，SOP pass 由 LLM 裁量但強制引用 evidence）。手工階段這個判斷由你（讀文本的 agent）代行——**請在 `weight_rationale` 或 rule `note` 裡留下出處痕跡**，這就是 distil 的 provenance。

---

## 2. epoch：時點鎖定 or epoch-less

目錄命名兩種，差別只在 `persona.yaml` 有無 `epoch`：

- **時點鎖定 `<key>-<epoch>/`**：立場會隨時代漂移的大師（Buffett 對科技股 1990s≠2019）。釘住「哪個年代的他」，sources 截至該年。選 epoch 的原則：**取最貼近當代標的的成熟期**（Buffett 選 2019＝Apple 時期，對科技龍頭已開放）。lookahead bias（模型訓練看過事後結果）無法消除、只能由未來的 replay 揭露——這不是選 epoch 時要解的問題。
- **epoch-less `<key>/`**：行為模式不隨年代漂移的大師——多為交易型（趨勢/動能/節奏的方法論本就與年代無關，如 Livermore/Minervini/Raschke/PTJ）。`epoch` 留空。

---

## 3. persona.yaml — 身分

```yaml
key: ptj                       # 全小寫；目錄名 = key（epoch-less）或 key-epoch
name: Paul Tudor Jones
weight: 1.0                    # 見下方「weight 怎麼定」
horizons: [trading]           # 時間軸分席：[trading] / [value] / 兩者皆列
aggression: [conservative]    # 性格軸分席：[conservative] / [aggressive] / 兩者皆列（中庸者）
epoch:                        # 時點鎖定型才填（如 2019）；epoch-less 省略
weight_rationale: >           # 為什麼是這個 weight + 這位補了 roster 什麼空缺 + 取捨留痕
  ...
philosophy: >                 # 第一人稱、他的思想體系（英文 OK，這是給 LLM 的 persona prompt）
  ...
focus: >                      # 他**只**盯著什麼（繁中）——聚焦讓大師不被無關訊號帶歪
  ...
voice: ...                    # 語氣關鍵詞（一行）
sources:                      # 第一手來源 manifest
  - ...
```

**weight 怎麼定**（[0.7, 1.2] 常見區間）：第一手文本量、公開實證強度、與既有 roster 的互補性。論點押在「不確定催化劑」上的（Icahn 0.95）、政策催化劑型的（Trump 0.7）給低一點；核心價值/重量級交易者給 1.0~1.1。**weight 高者在 `--sages N` 截斷時優先**，所以低權重大師在「大師會堂」（不截斷）才保證出席。

**兩條分席軸（決定這位出現在哪種陪審團）**：
- `horizons`：`trading`（數天~數週）/ `value`（數年）。決定 `--horizon` run 誰出席，越圍者 `abstain`。
- `aggression`：`conservative` / `aggressive`。決定 `--aggression` 象限 run 誰座位。中庸者兩者皆列、兩種陪審團都出席。
- 不給 `--aggression` ＝大師會堂，該 horizon 全員出席。**刻意補齊各性格原型**避免 roster 偏斜——但「roster 平衡 ≠ verdict 平衡」（PLTR 實證），平衡的 verdict 靠使用者自選象限，不靠硬調 weight。

`focus` 寫窄一點。大師的價值在「只看自己擅長的、對其餘誠實沉默」——這是對抗 mode collapse 的第一道防線。

---

## 4. rules.yaml — 硬規則 DSL（程式判定、零幻覺）

把大師「反覆強調、可量化」的原則翻成 `{field, op, value}`。DSL **刻意極簡**（evaluator 見 `src/cyber_sages/personas/rules.py`）：

**條件語法**：
```yaml
if: {field: roe_5y_avg, op: ">", value: 20}        # 單欄對常數
if:                                                  # 巢狀 all/any
  all:
    - {field: roe_5y_avg, op: ">", value: 20}
    - {field: gross_margin_trend_5y, op: ">=", value: 0}
```
- `op` ∈ `> >= < <= == !=`。**只能比「單欄 vs 常數」**——表達不了「欄位 A vs 欄位 B」（那要先用 skill 算成衍生單欄，見 §5）。
- `all` / `any` 可巢狀；條件樹內**任一**被引用欄位缺值 → 整條 `not_evaluable`（不觸發、不假裝通過）。

**三種 action**（typo 會在載入時 fail-loud）：
| action | 必帶欄位 | 語意 | clamp 行為 |
|---|---|---|---|
| `cap_confidence` | `confidence_ceiling` | 紅線：踩到就封頂信心 | **一律硬收**，與 stance 無關 |
| `bullish_floor` | `confidence_floor` | 加分（看多向）：墊高信心下限 | **只在 LLM 已 bullish 時**套 floor；反向則記 `rule_conflicts` 揭露、**不翻 stance** |
| `bearish_floor` | `confidence_floor` | 加分（看空向） | 對稱於上 |

```yaml
hard_rules:
  - id: no-leverage
    if: {field: debt_to_equity, op: ">", value: 1.0}
    action: cap_confidence
    confidence_ceiling: 0.5
    note: 過度槓桿——我不碰
  - id: wide-moat
    if:
      all:
        - {field: roe_5y_avg, op: ">", value: 20}
        - {field: gross_margin_trend_5y, op: ">=", value: 0}
    action: bullish_floor
    confidence_floor: 0.6
    note: 寬護城河＋毛利穩升

exceptions:                    # 自然語言，SOP pass 裡 LLM 裁量但強制引用 evidence
  - 壟斷市占可推翻 P/E 保守看法
  - owner earnings 看不到時改用 FCF 近似
```

**設計要點**：
- 硬規則只**收口信心（cap/floor）、不翻立場**。立場永遠是 LLM 在 SOP pass 的判斷；規則與立場衝突時**揭露**（rule_conflict）而非強壓。這是「LLM 推理為主、程式收口為輔」的核心。
- 彈性全部放 `exceptions`（自然語言）。hard_rules 只放**可量化、可程式判定**的紅線/加分。

---

## 5. rule↔skill 依賴 pattern（DSL 表達不了雙欄比較時）

DSL 只能「單欄 vs 常數」。當招牌原則是**雙欄比較**（如「現價跌破 200 日均線」＝ `last_price < sma_200`），先用 skill 把它**確定性算成一個衍生單欄**，rules 再對常數判定。

PTJ 是這個 pattern 的範本：
```python
# personas/ptj/skills.py
@skill(requires=["last_price", "sma_200"])
def price_vs_sma_200_pct(ev) -> SkillResult:
    last, sma = ev["last_price"], ev["sma_200"]
    return SkillResult(value=(last / sma - 1) * 100,
                       formula="(last_price / sma_200 - 1) * 100", unit="%")
```
```yaml
# personas/ptj/rules.yaml — 對衍生欄位的 0 比較
- id: below-200dma
  if: {field: price_vs_sma_200_pct, op: "<", value: 0}
  action: bearish_floor
  confidence_floor: 0.55
```
skill 輸出登錄為 sage-private evidence `S-ptj-price_vs_sma_200_pct`，rules 求值時與一般 canonical 欄位同樣可見（`rule_values` 攤平 store 含 private derived）。

---

## 6. sop.yaml — 決策流程（流程本身就是視角）

寫下這位大師**實際**怎麼一步步看一支股——**順序就是他的思維結構**。每步綁 `look_at`（要看的 evidence 類別/欄位）或 `use_skill`（提示這步依賴哪個 skill 輸出）。

```yaml
sop:
  - step: hidden-value
    ask: >
      拆開看——有沒有被低估的資產、過剩現金、被拖累的賺錢部門？分部加總是否遠高於市值？
    look_at: [fundamentals.total_assets, fundamentals.free_cash_flow_annual, profile]
  - step: catalyst-and-resistance
    ask: 誰能逼出價值？有沒有行動主義施力點？
    look_at: [news, profile]
    on_fail: 找不到催化劑就誠實說 neutral——便宜但動不了的東西不是我的菜
  - step: verdict
    ask: 綜合——站多 / neutral / 觀望？方向要明確，並指名靠什麼。
```

**流程要刻意「不同調」**：Munger 把「invert（什麼會摧毀它）」與「避免愚蠢」獨立成步、且**先於估值**；Buffett 走「能力圈→護城河→owner earnings→安全邊際」。兩人即使用同一份資料、同一個 owner earnings，也走出**不同推理路徑**——這是對抗 P1（mode collapse）的核心。最後一步固定是 `verdict`，要求方向明確。`on_fail` 給 LLM 一條誠實退場的話術。

---

## 7. skills.py — 確定性技能（絕不讓 LLM 算數）

找出大師依賴、且**能由現有 evidence 確定性算出**的計算。宣告 `requires`（canonical 欄位）+ 套 `@skill`：

```python
from cyber_sages.personas.skill import SkillResult, skill

@skill(requires=["net_income_annual", "depreciation_amortization_annual", "capex_annual"])
def owner_earnings(ev) -> SkillResult:
    return SkillResult(
        value=ev["net_income_annual"] + ev["depreciation_amortization_annual"] - ev["capex_annual"],
        formula="net_income + D&A - capex", unit="USD")
```
- **共用計算放 `personas/skills_lib.py`**，各 Pack 的 `skills.py` 只宣告 `requires` 並 import——避免重複（Buffett/Munger 共用 owner earnings 框架）。
- 用 `ev["field"]` 取值，accessor **自動記錄碰到的 evidence id**（省得手寫不穩定的 E-id）；輸出登錄為 `S-<key>-<skill_name>`、category `derived`、note 帶公式與 input ids，**可被 cite-check 驗證**。
- `requires` 缺欄位、或算出非有限值 → Runtime 記 `not_evaluable`，大師在 SOP pass 誠實說「這次看不到」。**這是誠實、不是 bug**（台股無 `capex`/`market_cap` → owner earnings 在 2330 必 not_evaluable）。

---

## 8. 欄位紀律（手工最容易踩的雷）

rules / skills 引用的欄位名**必須**對上 provider 真實 emit 的 canonical 名，否則規則永遠 `not_evaluable`、skill 永遠降級——**靜默失效**。動手前先確認欄位真的存在（grep provider 或看 Spec A 欄位清單）：

- **年度欄位帶 `_annual` 後綴**：`net_income_annual` / `depreciation_amortization_annual` / `capex_annual`（**不是** `capex` / `depreciation_amortization`）。
- **多年欄位單位是百分比的整數位**：`roe_5y_avg = 28.5`（＝28.5%）→ 門檻寫 `> 20` **不是** `> 0.20`。`gross_margin_trend_5y` 單位 `%/yr`、`earnings_stability_5y` 值域 [0,1]。
- **台股欄位差異**：TW 無 `capex`/`D&A`/`market_cap`（owner earnings 系列降級），但多年 `roe_5y_avg` 等已支援台股（moat 類規則對 2330 仍可評）。⚠️ 非曆年制（非 12/31 財年）公司的多年欄位覆蓋率會下降——見 issue #43。
- 字串證據（新聞）**不參與 DSL**；`rule_values` 只收數值型欄位，同名取最新（最後加入者）。

---

## 9. 品質 checklist（forge 完一位後自檢）

- [ ] 每條 `hard_rule` 引用的欄位**真的存在**於對應市場的 EvidenceStore（不會永遠 not_evaluable）。
- [ ] 每條 `hard_rule` 心裡有 **≥2 個獨立出處**；單源/有彈性的改放 `exceptions`。
- [ ] `cap_confidence` 帶 `confidence_ceiling`、`*_floor` 帶 `confidence_floor`（否則載入即 fail）。
- [ ] 雙欄比較全部走 §5 的 skill→衍生單欄 pattern，沒有硬塞進 DSL。
- [ ] SOP 順序反映**這位大師獨有的思維結構**，與既有大師不同調；末步是 `verdict`、方向明確；關鍵步有 `on_fail` 誠實退場話術。
- [ ] skill 是**確定性計算**、宣告了 `requires`、共用邏輯走 `skills_lib`。
- [ ] `horizons` / `aggression` 分席正確；`weight_rationale` 寫明補了 roster 什麼空缺 + rule 出處痕跡。
- [ ] 跑一次 live 驗收：`uv run cyber-sages analyze <US標的> --sages N --depth quick` 確認本 Pack 上場，sop_trace **每步有 evidence 錨點**、rule_conflict 不翻 stance、缺欄位的 skill/rule 正確 not_evaluable。台股標的再跑一次驗 not_evaluable 與跨市場。
- [ ] 加進 `tests/test_pilot_packs.py` 風格的整合測試（真實 Pack 檔 + mock gateway，觸發 ceiling/floor 的 evidence 下 clamp 生效）。

---

## 10. 遷移舊單檔 yaml → 完整 Pack

舊 `personas/<key>.yaml`（burry/damodaran/druckenmiller/graham/lynch/taleb/wood）只有 philosophy/focus/voice，走 degraded 單發 prompt。升級步驟：
1. 建 `personas/<key>/`（或 `<key>-<epoch>/`）目錄，把舊 yaml 內容搬進 `persona.yaml`，補 `horizons`/`aggression`/`weight_rationale`/`sources`。
2. 照 §0 判斷該做到哪一層（純 SOP？帶 rules？帶 skills？）。
3. 刪掉舊單檔 `<key>.yaml`（loader 認目錄即可；留著會重複載入）。
4. 跑 §9 checklist。

半 Pack（chanos/icahn/roaringkitty/son/soros/trump 目前缺 skills/部分缺 rules）：先確認缺的是「刻意不做」（純 SOP 取捨，如 Icahn）還是「待補」——前者在 `weight_rationale` 留痕即可，後者照 §4/§5/§7 補。

---

## 參考 Pack（讀現成的最快）

- **完整含 skills**：`personas/buffett-2019/`（共用 skills_lib）、`personas/ptj/`（rule↔skill pattern）、`personas/minervini/`、`personas/raschke/`。
- **純 SOP（刻意不附 rules/skills）**：`personas/icahn/`、`personas/son/`、`personas/soros/`、`personas/trump-2025/`。
- **方法論源頭**：`docs/specs/2026-06-14-E1-pilot-pack-handcrafting.md`（Buffett/Munger 逐步打造紀錄）、`docs/specs/2026-06-13-E-sage-runtime-and-nuwa.md`（Spec E 全文）、`docs/ROADMAP.md` Phase 6。
- **Runtime（求值真相）**：`src/cyber_sages/personas/{pack,rules,skill}.py`、`skills_lib.py`。
