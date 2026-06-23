# Cyber-Sages ⚔

**AI agent 的投資智囊團** — 你（或你的 AI agent）是法官，Cyber-Sages 給你一整套經過驗證、可溯源、立場互相攻防過的決策材料。

當你問「NVDA 今天可以買嗎？多少可以買？多少該賣？」，你需要的不是一篇免責聲明開頭的研究報告，而是：

- 經過審核的第一手資料（SEC 財報、雙路徑報價、確定性計算的技術指標）——**分析師只供中性數據、不給方向**
- **大師才是分析主體**：投資大師 persona 各自帶硬規則 + 決策 SOP + 確定性技能（Persona Pack），獨立出訊號、加權投票——方向性判斷全由大師負責
- **時間框架分流**：`--horizon value`（數年長期價值）或 `trading`（數天~數週短線），各自由適用該 horizon 的大師出席，越圍者誠實退場——當沖與長期價值不再混為一談
- **攻防**：多空辯論**雙盲對稱**（雙方先盲打、再互餵反駁），敗方核心論點被逐點反駁，離群少數派的意見強制進場
- **幕僚長**：首席合議產出可操作的行動計畫——進場區間、停損、目標價，每個價位錨定證據；口徑隨 horizon（短線緊停損 vs 長期寬停損分批）
- **法官是你**：所有材料、票數、異議（含中性三類細分）、資料品質警示全部攤開，最終決策權在讀報告的人（或 agent）

## 設計鐵律

1. **反幻覺** — 第一手資料的完整性與可驗證性是第一條件。資料層回傳的不是裸數字而是 `Evidence`（含來源/URL/時間戳）；分析師每個 claim 必須引用 evidence id 並通過程式驗證；技術指標與大師的專屬計算（如 owner earnings、趨勢排列分數）都在程式裡確定性算，不讓 LLM 算數；大師的硬規則以受限 DSL 程式判定、信心收口（clamp），LLM 只做語意推理；行動計畫的每個價位必須有真實存在的證據錨點。
2. **反單一視角** — analyst 降級為中性數據源，**大師才是方向性判斷的唯一來源**：多大師平行出訊號 → 加權投票（中性分三類：能力圈外/訊號不足/多空平衡）→ 多空辯論**雙盲對稱**（敗方核心論點逐點反駁、少數派意見強制餵給弱勢方）→ 首席合議誠實記錄異議。

## Pipeline

```
[1] Collect      yfinance 雙路徑報價 + SEC EDGAR 第一手財報 + 新聞
[2] Audit  🚧    資料審核閘門：確定性檢查（新鮮度/跨源一致性/逐欄位過期）
                 + LLM 稽核員（語意異常，如 P/E 與 EPS 對不上）
                 → 有 error 即降級，最終信心封頂 0.5 並強制揭露
[3] Analyze      4 分析師（基本面/技術面多時間軸/情緒/估值）產出帶引用的**中性數據**報告
                 （不給 outlook/方向，方向由大師決定）
[4] Cite-Check 🚧 引用驗證閘門：claim 數字必須對得上引用的 evidence
                 （含衍生比率/中文量級/負數），失敗退回重寫，仍失敗則標記揭露
[5] Council      適用本 horizon 的大師 persona 平行出訊號（Persona Pack：skill→rule→SOP→
                 信心 clamp）→ 加權投票 + 離群者 + 中性三類細分；越圍大師誠實退場（abstain）
[6] Debate       多空辯論雙盲對稱（雙方盲打→互餵反駁），敗方核心論點逐點反駁，少數派原文進場 + 裁判
[7] Synthesize   幕僚長決策簡報（行動計畫+三時間軸，口徑隨 horizon）+ 風控官覆核
```

實測中兩道閘門各抓過真問題：稽核員發現過「換 XBRL 標籤導致營收是 4 年前舊值」、「yfinance 的 P/E 用分析師共識而非 GAAP EPS」；引用驗證攔下過數字與證據不符的 claim。

## 安裝與設定

```bash
git clone https://github.com/Dakai666/Cyber-Sages.git && cd Cyber-Sages
uv sync --extra dev
cp .env.example .env        # 填 API keys
uv run cyber-sages doctor   # 驗證 provider 連線
```

**LLM 供應商與模型完全自選**：只要是 Anthropic 相容 API 都能接（Anthropic 官方、MiniMax 等）。`config.yaml` 的 `roles` 區塊為 7 個角色各自指定 provider + model，例如日常角色走 MiniMax-M2.7、深度推理（辯論/裁判/幕僚長/風控）走 MiniMax-M3 或 Claude：

```yaml
roles:
  analyst: { provider: minimax,   model: MiniMax-M2.7 }
  chief:   { provider: anthropic, model: claude-opus-4-8 }
```

## 使用

```bash
uv run cyber-sages analyze NVDA                    # 大師會堂：該 horizon 全員合議 + 辯論（預設 --horizon value）
uv run cyber-sages analyze NVDA --horizon trading  # 短線（數天~數週）：交易型大師出席、長期價值大師退場
uv run cyber-sages analyze NVDA --aggression aggressive    # 激進陪審團（長期）：只座位進攻型大師
uv run cyber-sages analyze NVDA --horizon trading --aggression conservative  # 保守短期象限
uv run cyber-sages analyze 2330                    # 台股（FinMind + yfinance .TW，自動偵測）
uv run cyber-sages analyze NVDA --sages 5 --depth quick   # 省 token
uv run cyber-sages analyze NVDA --no-debate        # 跳過辯論
uv run cyber-sages analyze NVDA --no-macro         # 不帶入 FRED 總經背景
uv run cyber-sages analyze NVDA --json             # stdout 輸出 verdict.json（管線串接用）
```

**時間框架（`--horizon`）**：`value`（預設，數年長期價值，看多年基本面/護城河/估值）或
`trading`（數天~數週短線，看價格趨勢/動能/籌碼）。每位大師在 persona 宣告適用 horizon，
不適用本次 horizon 者**誠實退場（abstain）、不投票但揭露**——長期持有者不答當沖、純短線
交易者不答長期估值。Action plan 口徑也隨 horizon（短線緊停損明確進出 vs 長期分批寬停損）。

**大師 = Persona Pack**：每位大師是「可執行的專家」——`personas/<key>/` 目錄帶身分、硬規則
（DSL，程式判定信心 clamp）、決策 SOP（LLM 逐步推理、每步引用 evidence）、確定性技能
（如 owner earnings / 趨勢排列分數，程式算不讓 LLM 算）。缺欄位時誠實記 `not_evaluable`，
不假裝算得出。新增大師：放一個 Pack 目錄，零程式碼——完整打造/遷移方法論見 **forge-sage skill**
（`.claude/skills/forge-sage/SKILL.md`）：任何 AI agent 讀完就知道怎麼 forge 一位大師或把舊單檔
yaml 升級成 Pack（Pack 解剖、DSL 參考、rule↔skill pattern、欄位紀律、品質 checklist）。

**Roster（19 人）**：value/quality（Buffett, Munger, Graham, Damodaran, Lynch, Burry, Wood,
Chanos 鑑識空頭, Icahn 行動派, Son power-law 賭徒）、trading（Livermore, Minervini, Raschke,
Roaring Kitty 散戶情緒, PTJ 防禦型短線）、tail-risk（Taleb）、policy/reflexive（Trump 政策催化劑, Soros 反身性宏觀）。

**五種模式（兩條分席軸）**：每位大師宣告 `horizons`（時間軸）與 `aggression`（保守/激進性格軸，
中庸者兩者皆列）。組合出 5 種陪審團——**大師會堂**（預設，不給 `--aggression`：該 horizon 全員出席、
不截斷）+ 四象限（`--horizon {value,trading}` × `--aggression {conservative,aggressive}`）。
`--sages N` 僅作手動省 token 上限。brief 會標示本次是哪種陪審團（激進陪審團偏多是「組成使然」、
非標的訊號）。完整 seating 機制見 `CLAUDE.md`，原型 roadmap 見 `docs/ROADMAP.md`。

> **升級注意（行為變更）**：預設改為「大師會堂全員出席、不截斷」（舊版預設截斷 10 席）——value run
> 從 10 席 → 14 席，token 用量約 +40%。這是刻意取捨（品質優先於 token）；要省 token 請用 `--sages N`
> 限制席數，或挑單一象限（`--aggression` 縮小陪審團）。

**市場自動偵測**：純數字代號（`2330`、`0050`）或 `.TW`/`.TWO` 字尾走台股管線——
FinMind 為第一手來源（綜合損益表/資產負債表/月營收 + 三大法人買賣超 + 融資融券），
yfinance `.TW` 報價當跨源比對的第二路徑；技術指標一樣在程式裡確定性計算。
**總經**（FRED：聯邦資金利率、2y/10y、殖利率曲線、CPI 年增、失業率、非農）是獨立的
`macro` 證據類別，不綁個股——總經分析師據此產報告，同一份證據也餵給陪審團做跨域視角；
缺 `FRED_API_KEY` 時自動跳過總經分析師。

終端機顯示 Rich 流式畫面（階段進度樹、agent 串流輸出、即時投票表），結束後印出決策簡報。

### 產出結構

```
runs/NVDA-2026-06-11_143052/   # 標的-日期_時間（同日重跑不互相覆蓋）
├── brief.md          ★ 一頁決策簡報：裁定/進場/停損/目標價位表（每個價位帶
│                       evidence 錨點）、倉位建議、計畫作廢條件、短中長三時間軸、
│                       陪審團票數、翻盤條件、少數派觀點
├── verdict.json      結構化決策 payload（裁定+票數+辯論+資料品質）
├── details/          有興趣才深挖
│   ├── analysts.md   分析師完整報告與 claim 引用
│   ├── council.md    每位大師的完整論點與「什麼會讓他改變看法」
│   ├── debate.md     多空攻防全文 + 裁判判詞
│   ├── data_quality.md  審核發現 + 風控官意見
│   └── evidence.md   證據總表
├── evidence.json     原始證據（每筆含來源/URL/時間戳）
└── full_trace.json   全管線產物，完整可審計
```

### 給 AI agent 當智囊團用

讓你的 agent（Claude Code、任何 coding agent）跑完 `analyze` 後**直接讀檔自行解讀**：先讀 `brief.md` 拿裁定與價位，存疑處再進 `details/` 與 `evidence.json` 溯源——每個數字都有 evidence id 可查回原始來源。agent 是法官：陪審團票數、辯論勝負、資料品質降級警示都是給它的量刑材料，不是替它做的決定。

## 驗證機制（為什麼能信）

| 機制 | 作用 |
|---|---|
| Evidence Store | 每筆資料含來源/URL/資料日期/抓取時間，全程可溯源 |
| 雙路徑報價 | fast_info 與日線收盤互相比對，價差超容差即 error |
| 逐欄位過期檢查 | 單一欄位嚴重過時（如標籤切換撈到舊值）不會被同類新鮮欄位掩護 |
| LLM 稽核員 | 抓語意級異常：跨欄位數學不一致、量級不合理、單位混淆 |
| 引用驗證 | claim 數字必須對得上引用的 evidence（含衍生比率），竄改即攔下；大師 SOP 每步同樣過驗證 |
| 大師硬規則 clamp | 規則以受限 DSL 程式判定，只收口信心（cap/floor），**不讓 LLM 翻轉立場**；規則與立場衝突時揭露而非強壓 |
| not_evaluable | 大師平常會看、這次缺資料的規則/技能誠實標記（不假裝算得出），注入推理並揭露 |
| Horizon abstain | 不適用本次 horizon 的大師誠實退場、不投票，brief 揭露（與「硬失敗缺席」嚴格區分） |
| 雙盲辯論 | 多空第一輪互不可見對手論點，消除先後手不公；敗方核心論點被逐點反駁 |
| 價位錨點稽核 | 行動計畫的進場/停損/目標若無有效 evidence 錨點，強制標警示 |
| 降級模式 | 資料審核有 error 時信心封頂 0.5，簡報強制揭露 |

## Roadmap

- [x] **台股接入**：FinMind（基本面/三大法人/融資券）+ yfinance `.TW`，同一 `MarketDataProvider` 介面
- [x] **總經資料**：FRED（利率/通膨/殖利率曲線/就業），總經分析師歸隊、給大師跨域視角
- [x] **大師為主體 + Persona Pack**：analyst 降級為中性數據源；大師帶硬規則/SOP/確定性技能
- [x] **時間框架分流**：`--horizon value|trading`，大師按 horizon 出席/退場
- [x] **陪審團結構**：雙盲對稱辯論、敗方核心論點雙邊反駁、中性三類獨立訊號
- [ ] 手工擴充大師（按類型/原型 curate）+ 全員遷移為 Pack，方法論固化成 forge-sage skill
      （原規劃的 `cyber-sages distil` 蒸餾引擎判定為過度工程——蒸餾者本就是讀 SOP 的 agent，改寫成 skill）
- [ ] Crypto（CoinGecko / 鏈上數據）
- [ ] 回測器（Phase 7）：歷史時點重放管線 + persona 品質分數，補 Spec D 回測驗收、驗證陪審團勝率
- [ ] 兩階段 council 成本優化（scout 瘦身 + 便宜小模型，N 夠大才划算；見 issue #55）

## 致敬

架構靈感來自 [TradingAgents](https://github.com/TauricResearch/TradingAgents)（流水線對抗結構）與 [ai-hedge-fund](https://github.com/virattt/ai-hedge-fund)（多大師訊號），Cyber-Sages 補上了兩者缺的可驗證資料管線與「法官在外」的決策架構。
