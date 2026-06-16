# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync --extra dev            # install (runtime + pytest)
uv run pytest -q               # full test suite
uv run pytest tests/test_tw_macro.py::test_detect_market   # single test
uv run cyber-sages doctor      # ping每個 provider + FinMind/FRED 資料源連線
uv run cyber-sages analyze AAPL    # 美股 / analyze 2330 → 台股（自動偵測）
uv run cyber-sages analyze 2330 --json --no-debate --no-macro --sages 5 --depth quick
```

No build/lint step; pure Python ≥3.12. Tests use `asyncio_mode = "auto"` (async tests need no decorator). Most tests are network-free — they patch providers or test pure parsing/computation helpers directly. A few helpers in this repo hit live FinMind/FRED only when run by hand; the suite itself does not.

## Architecture

Cyber-Sages turns "can I buy X?" into a **verifiable, multi-perspective decision brief**. The reader (a human or an AI agent) is the judge; the system supplies audited evidence, jury votes, adversarial debate, and a chief-of-staff action plan. Two design laws drive everything: **anti-hallucination** (every number is first-hand and traceable) and **anti-single-perspective** (many independent voices, then adversarial stress-testing).

### The 7-stage pipeline (`pipeline.py`)

`run_pipeline()` orchestrates, emitting stage events to the CLI dashboard. The stages and their gates:

1. **Collect** — route by market → fetch `quote/history/fundamentals/news` (+`chips` for TW, +`macro` globally) into an `EvidenceStore`.
2. **Audit gate** (`verify/data_audit.py`) — deterministic checks + an LLM auditor. **Errors degrade the run** (confidence capped at 0.5, forced disclosure).
3. **Analyze** (`agents/analysts.py`) — analysts produce reports; each claim cites evidence ids.
4. **Cite-check gate** (`verify/citation_check.py`) — every number in a claim must be derivable from its cited evidence, else the analyst rewrites; persistent failures are flagged `unverified`.
5. **Council** (`agents/council.py`) — N sage personas vote in parallel; weighted tally + outlier list.
6. **Debate** (`agents/debate.py`) — bull vs bear (minority opinions forced in) + a judge.
7. **Synthesize** (`agents/synthesis.py`) — chief sage action plan (entry/stop/targets, each anchored to an evidence id) + risk officer review.

`report.py` renders the result to `runs/<TICKER>-<date>_<time>/` (`brief.md` + `verdict.json` + `details/` + `evidence.json`). A one-shot `AnalysisResult.generated_at` timestamps the folder, brief title, and payload consistently.

### Evidence is the spine (`data/evidence.py`)

Data sources never return bare numbers — they return `Evidence` (value + source + url + `as_of` date + retrieved_at). `EvidenceStore` assigns ids (`E001…`), carries the `market`, and produces the `digest()` text that every downstream prompt consumes. Categories: `quote / fundamentals / history / news / profile / chips / macro`. **Anything an analyst or sage claims must trace back to an evidence id** — this is enforced programmatically at stage 4, not by trust.

### Provider abstraction & market routing (`data/base.py`)

`detect_market(ticker)` (`.TW`/`.TWO` or 4–6-digit number → `"TW"`, else `"US"`) + `make_provider(market)` select the source. Providers implement the `MarketDataProvider` protocol (`get_quote/history/fundamentals/news`); TW additionally exposes `get_chips` (pipeline detects via `hasattr`). To add a market: write a provider, register it in `make_provider`, and add its fundamentals field names to the market branch in `deterministic_checks`.

- **US** (`data/us_stocks.py`): yfinance dual-path quote + SEC EDGAR companyfacts (first-hand XBRL) + Finnhub/yfinance news.
- **TW** (`data/tw_stocks.py`): FinMind first-hand (income/balance/月營收 + 三大法人 + 融資券) with yfinance `.TW` as the cross-source second price path. `data/finmind.py` is the REST helper.
- **Macro** (`data/macro.py`): FRED series (rates / yield curve / CPI YoY / employment). Market-independent; fetched once and merged into the store. Degrades to empty if `FRED_API_KEY` is unset.

### Cross-cutting conventions (the non-obvious rules)

- **Never let the LLM do arithmetic.** Technical indicators (`data/indicators.py`, shared US+TW) and TW **TTM** sums (`eps_ttm`/`revenue_ttm`) are computed deterministically and emitted as evidence. FinMind financial statements are *single-quarter*; using a quarter EPS as if annual mis-prices P/E by ~4×, which is exactly why `eps_ttm` exists.
- **Canonical field names cross markets.** Emit `revenue_*`, `net_income_*`, `eps_*`, `total_assets`, etc. with the same names so analysts and audit work unchanged. The audit's *required* field list branches on `store.market` (US = `*_annual`, TW = `*_latest_quarter`).
- **Degradation comes only from the deterministic gate.** The LLM auditor is a semantic advisor — its findings are clamped to `warning` in `run_audit` so a non-deterministic model can't randomly halve conviction. Its prompt is injected with today's date and forbidden from judging data fake based on its training cutoff.
- **Citation verification accepts derived values.** `_candidate_values` in `citation_check.py` treats a claim's numbers as valid if they match a cited evidence value *or* a sensible derivation: ratios, percentages, change-%, plain differences, unit-scale conversions (`172 thousands` ↔ `172K`), and sign-agnostic magnitudes (`賣超490萬` ↔ `-4,904,284`). Bare integers (years, SMA windows) are not verified.
- **Analysts are evidence-gated.** `run_all_analysts` only runs an analyst if the store has at least one item in its categories — the Macro analyst is silently absent when no `macro` evidence exists. Add an analyst by appending a tuple to `ANALYSTS` (key, title, focus, visible categories).
- **Personas are pure data.** Drop a `personas/*.yaml` (philosophy/focus/voice/weight) to add a sage — zero code. Higher `weight` sages are seated first when `--sages N` truncates. Current roster (15): value/quality (Buffett, Munger, Graham, Damodaran, Lynch, Burry, Wood), trading (Livermore, Minervini, Raschke, Druckenmiller), tail-risk (Taleb), and Phase 4.5 archetype additions — **Chanos** (forensic short-seller, `value`, the only persona actively hunting fudged numbers; carries 2 conservative hard rules), **Icahn** (activist, `value`, corporate-action/sum-of-parts lens, pure SOP), **Trump** (policy-catalyst, `trading`+`value`, `weight=0.7` so it informs the debate without dominating the evidence-grounded vote).
- **Seating is weight-truncated *after* horizon filtering** (`council.py`: `applicable[:n_sages]`). With default `--sages 10` and 12 value-eligible sages, the two lowest-weight (currently Wood 0.9, Trump 0.7) are **dropped from value runs** — Trump's intended habitat is `trading` runs (6 trading-eligible, all seat). To force a low-weight catalyst voice into a value council, raise `--sages` (≥12 seats Trump). This is the deliberate tension of a low-weight persona: light tally influence ⇄ not guaranteed a seat.

### LLM gateway (`llm/gateway.py`, `config.yaml`)

One `AsyncAnthropic` interface routes by **role** (`data_auditor / analyst / sage / debater / judge / chief / risk`) → a `{provider, model}` from `config.yaml`. Models are never hard-coded; any Anthropic-compatible endpoint works (Anthropic official, MiniMax, …). Provider `features` gate request shape: `cache_control` / `adaptive_thinking` / `json_schema_output` are only sent to providers that declare them, so compatible endpoints receive clean standard requests. Streaming reads raw events (not the SDK accumulator) to tolerate MiniMax's forced thinking blocks. `structured()` retries on validation failure with the error fed back into the prompt.

## Working in this repo

- `.env` (gitignored) holds keys: `ANTHROPIC_API_KEY` / `MINIMAX_API_KEY` (per `config.yaml` roles), plus optional `FINMIND_TOKEN`, `FRED_API_KEY`, `FINNHUB_API_KEY`, `EDGAR_USER_AGENT`. Run `cyber-sages doctor` to verify them.
- Report prose (summaries, theses, claim text) is written in 繁體中文 with tickers/technical terms kept in English — match this when editing prompts.
