# Cyber-Sages

多大師 AI agent 金融分析團隊 — 結合 [TradingAgents](https://github.com/TauricResearch/TradingAgents) 的流水線對抗結構與 [ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) 的多大師投票，並補上兩者缺的東西：**可驗證的第一手資料管線**。

## 兩條鐵律

1. **反幻覺** — 每個數字可溯源。資料層回傳的是 `Evidence`（含 source / url / timestamp），不是裸數字；分析報告的每個 claim 都要引用 evidence id，並由程式驗證。
2. **反單一視角** — 多大師平行出訊號 → 加權投票 → 多空辯論（離群者意見強制進場）→ 首席合議。

## Pipeline

```
Collect → Audit(閘門) → Analyze → Cite-Check(閘門) → Council投票 → Debate → Synthesize
```

## 使用

```bash
cp .env.example .env   # 填入 API keys
uv sync --extra dev
uv run cyber-sages analyze AAPL
uv run cyber-sages analyze NVDA --sages 5 --depth quick
```

LLM 供應商與每個角色用的 model 都在 `config.yaml` 自選（Anthropic 官方 API 或任何 Anthropic 相容端點，如 MiniMax）。

輸出落在 `runs/<TICKER>-<date>/`：`report.md`、`full_trace.json`、`evidence.json`。
