# Cyber-Sages ⚔

**AI agent 的投資智囊團** — 你（或你的 AI agent）是法官，Cyber-Sages 給你一整套經過驗證、可溯源、立場互相攻防過的決策材料。

當你問「NVDA 今天可以買嗎？多少可以買？多少該賣？」，你需要的不是一篇免責聲明開頭的研究報告，而是：

- 經過審核的第一手資料（SEC 財報、雙路徑報價、確定性計算的技術指標）
- 多視角的完整分析（基本面 / 技術面多時間軸 / 市場情緒 / 估值）
- **陪審團**：十位投資大師 persona 各自獨立出訊號、加權投票——數量夠多，幻覺與偏誤在統計上被稀釋
- **攻防**：多空辯論互相拆解對方最弱的假設，離群少數派的意見強制進場
- **幕僚長**：首席合議產出可操作的行動計畫——進場區間、停損、目標價，每個價位錨定證據
- **法官是你**：所有材料、票數、異議、資料品質警示全部攤開，最終決策權在讀報告的人（或 agent）

## 設計鐵律

1. **反幻覺** — 第一手資料的完整性與可驗證性是第一條件。資料層回傳的不是裸數字而是 `Evidence`（含來源/URL/時間戳）；分析師每個 claim 必須引用 evidence id 並通過程式驗證；技術指標在程式裡確定性計算，不讓 LLM 算數；行動計畫的每個價位必須有真實存在的證據錨點。
2. **反單一視角** — 多大師平行出訊號 → 加權投票 → 多空辯論（少數派意見強制餵給弱勢方）→ 首席合議誠實記錄異議。

## Pipeline

```
[1] Collect      yfinance 雙路徑報價 + SEC EDGAR 第一手財報 + 新聞
[2] Audit  🚧    資料審核閘門：確定性檢查（新鮮度/跨源一致性/逐欄位過期）
                 + LLM 稽核員（語意異常，如 P/E 與 EPS 對不上）
                 → 有 error 即降級，最終信心封頂 0.5 並強制揭露
[3] Analyze      4 分析師（基本面/技術面多時間軸/情緒/估值）產出帶引用的報告
[4] Cite-Check 🚧 引用驗證閘門：claim 數字必須對得上引用的 evidence
                 （含衍生比率/中文量級/負數），失敗退回重寫，仍失敗則標記揭露
[5] Council      N 位大師 persona 平行出訊號 → 加權投票 + 離群者名單
[6] Debate       多空辯論（吃投票結果，少數派原文進場）+ 裁判
[7] Synthesize   幕僚長決策簡報（行動計畫+三時間軸）+ 風控官覆核
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
uv run cyber-sages analyze NVDA                    # 完整：10 位大師 + 辯論
uv run cyber-sages analyze NVDA --sages 5 --depth quick   # 省 token
uv run cyber-sages analyze NVDA --no-debate        # 跳過辯論
uv run cyber-sages analyze NVDA --json             # stdout 輸出 verdict.json（管線串接用）
```

終端機顯示 Rich 流式畫面（階段進度樹、agent 串流輸出、即時投票表），結束後印出決策簡報。

### 產出結構

```
runs/NVDA-2026-06-11/
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
| 引用驗證 | claim 數字必須對得上引用的 evidence（含衍生比率），竄改即攔下 |
| 價位錨點稽核 | 行動計畫的進場/停損/目標若無有效 evidence 錨點，強制標警示 |
| 降級模式 | 資料審核有 error 時信心封頂 0.5，簡報強制揭露 |

## Roadmap

- [ ] **台股接入**：FinMind（基本面/三大法人/融資券）+ yfinance `.TW`，同一 `MarketDataProvider` 介面
- [ ] **總經資料**：FRED（利率/通膨/就業），讓總經分析師歸隊、給大師跨域視角
- [ ] Crypto（CoinGecko / 鏈上數據）
- [ ] 回測器：歷史時點重放管線，驗證陪審團勝率
- [ ] 更多大師 persona（`personas/*.yaml` 純資料檔，新增零程式碼）

## 致敬

架構靈感來自 [TradingAgents](https://github.com/TauricResearch/TradingAgents)（流水線對抗結構）與 [ai-hedge-fund](https://github.com/virattt/ai-hedge-fund)（多大師訊號），Cyber-Sages 補上了兩者缺的可驗證資料管線與「法官在外」的決策架構。
