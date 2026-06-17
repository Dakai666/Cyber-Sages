# Spec F — 資料源頭強固（Data Source Robustness）

**Status**: draft（2026-06-17 起草，待 DK 定案）
**Date**: 2026-06-17
**Dependencies**: Spec A（資料層）、Spec B（管線硬化，W9 失敗降級）——本 spec 是 Pillar 1 的回訪
**觸發**: SPCX live run（`runs/SPCX-2026-06-17_141040/`）暴露的真實資料污染

## 背景

兩條鐵律的第一條是「根基先行——資料來源得正確，後續燒 tokens 才有意義」。Spec A/B 把 Pillar 1 收口時，相信「失敗會觸發降級、降級會封頂信心」已足夠。SPCX 這次 live run 證明這個假設有三個致命破口：

1. **髒資料不是「缺資料」，是「假裝有資料」。** yfinance 對 SPCX 回傳 2026-06-16 的日線是**幽靈 bar**：OHLC 全 = 前一日收盤 192.5、Volume = 0；但同日 1 分線真實收 $202、成交 5.4 億股。系統直接取 `history().iloc[-1]` 當 `latest_close`/`latest_volume`，把幽靈值當第一手證據發出（E003/E004）。
2. **跨源檢查名存實亡。** 反幻覺設計靠 `fast_info` vs `daily history` 跨源比對，但兩者**同源 Yahoo**、一起 stale、數值一致（0% 背離），`max_price_divergence_pct=2%` 完全沒觸發。相關性失效（correlated failure）讓「兩條獨立來源」的前提破產。
3. **重大錯誤只降級、不中止。** audit 命中核心類別 error 後，pipeline 照跑完整 7 階段、產出帶 entry/stop/target 的行動計畫。在錯的價格上演完整場大師團，就是 DK 說的「在錯的數字上開心地哈哈」。

衍生的第四個問題（DK 點出）：**「資料品質降級」是全域標籤**——一個 macro 缺和一個 quote 錯封同一個 0.5、同一句話。讀者無從判斷壞的是不是自己在乎的維度，於是整份報告一律不信。這讓「降級報告還有沒有參考價值」變成無解的問題。

本 spec 專注把資料閘門從「降級式」升級為「分級 + 中止 + 分維度」，並補上幾條最關鍵的獨立性與完整性缺口。

## 涵蓋的 Gaps

### 穩定性（S 類，會直接污染結論）

| # | Gap | 位置 | SPCX |
|---|---|---|---|
| **S1** | 幽靈/退化尾 bar 無防呆（取最後一根不檢查 Volume=0 / OHLC 平移） | `us_stocks.py:110,198`、`tw_stocks.py:101` | ✅ 元凶 |
| **S2** | 跨源價格不獨立：US 兩路徑同源 Yahoo；TW 第二路徑失敗即無人比對 | `us_stocks.py:95-121` | ✅ |
| **S3** | freshness 只看 `as_of` 日期、不看 bar 內容真實性 | `data_audit.py:108-115` | ✅ |
| **S4** | audit 只降級不中止，無 fatal tier / halt | `pipeline.py:167-173`、`data_audit.py:21` | ✅ |
| **S5** | 歷史過短 → 技術指標靜默全缺，brief 仍照出無錨點行動計畫 | `indicators.py:27`、各 provider history | ✅（僅 3 交易日） |
| **S6** | 無「內部矛盾到不合理」sanity 上限（forward P/E −2242x 直接發出） | `us_stocks.py:129-135` | ✅ |
| **S7** | 降級是全域、不分維度 → 讀者無法判斷壞在核心或周邊 | `data_audit.py:48-55`、synthesis 封頂 | — |

### 完整性（C 類，資料不夠齊）

| # | Gap | 影響 |
|---|---|---|
| **C1** | US 無量能趨勢、無盤中真實價當第三獨立路徑 | 短線判讀薄弱、S2 無解 |
| **C2** | US fundamentals 唯一靠 SEC EDGAR，無 CIK 即整類空、無二手 fallback | 動輒整類 error → 降級 |
| **C3** | TW 現金流量表不發（FinMind YTD 累計跨期會錯）→ 無 FCF/利息保障 | TW value 大師缺料 |
| **C4** | TW 分析師目標價走 yfinance，覆蓋率低、常空 | TW 缺前瞻錨點 |
| **C5** | macro 全美國 FRED，台股套美國總經，無台灣 CPI/利率/TWD 匯率 | TW 宏觀失真 |
| **C6** | news 無情緒量化、無去重、覆蓋淺 | 情緒面薄 |
| **C7** | 缺 ATR / 量價配合 / 相對強弱 RS | 短線大師缺料 |
| **C8** | 無資料健康度結構化留痕，每次哪源成功/失敗/多舊不可回溯 | 降級不可追溯 |

## 核心設計

### 1. 三級資料完整性（取代現行二元「error → degrade」）

`AuditFinding.severity` 由 `Literal["error","warning"]` 擴為 `Literal["fatal","error","warning"]`：

- **`fatal`** — 分析前提本身壞了，後續一切無意義 → **stage 2 halt**，不跑 analyst/council/debate/synthesis。
- **`error`** — 某核心維度受損但分析仍可進行 → 該維度降級 + 揭露（見設計 3）。
- **`warning`** — 周邊瑕疵，記錄並通過。

**fatal 判準（deterministic only，LLM 永遠不能升級到 fatal）**：

| 判準 | 規則 |
|---|---|
| 幽靈價格 bar | 最新日線 `Volume==0` 且為美股/台股交易日（非假日）→ fatal（S1/S3） |
| 跨源價格背離 | 真正獨立的兩源價差 > `max_price_divergence_pct` → fatal（S2，現為 error，升級） |
| 無可用價格 | quote 類別完全空 → fatal（現為 error） |
| 報價過期 | `latest_close` 早於 today − `max_quote_age_days` → fatal（現為 error） |
| 內部矛盾 | 市值/EPS/PE 等出現物理不可能組合（見設計 4 sanity 上限）→ fatal（S6） |
| 歷史不足以判讀 | history < `min_history_days_fatal`（暫定 30，= 技術指標下限）→ fatal（S5；新標的如實告知「資料不足無法分析」而非硬出報告） |

> ⚠ 設計張力：S5 把「歷史不足」設 fatal 會讓所有新上市股無法分析。**決議待定（見文末 D1）**：是一律 fatal，還是「短線 horizon fatal、長線 horizon 降級」，或「fatal 但 brief 改成『資料不足，這是我們僅有的 N 天觀察』的誠實短報告」。

halt 後產出 **「無法分析」短報告**（`brief.md` 仍寫，但只含：裁定＝`無法分析`、fatal 清單、缺/壞了什麼、補齊後可重跑的提示），不含陪審團/辯論/行動計畫。

### 2. 真正獨立的第二價格源（S2 + C1）

US 端 cross_source 比對需要**不同 feed**。方案：取盤中最後成交價（`history(period="1d", interval="1m")` 末筆，或第二 provider）作為 `last_price` 的獨立來源，與 daily-history `latest_close` 比對。SPCX 案例下 intraday=$202 vs daily=$192.5 → 5% 背離 > 2% → fatal 攔下。順帶把當日真實量、均量/量比（C1）發為 evidence。

> 工程注意：盤中 1m 在盤前/收盤後/週末會回最近交易日，需以 bar 時間戳判斷新鮮度，避免拿到陳舊 intraday 反而誤判。**已落地**：`_intraday_evidence` 以 bar 自身時區計年齡，逾 16h 視為非當前 → 不發（保 SPCX 的跨日 ~11.8h 通過），交給 cross_source「跳過」warning 揭露。
>
> **實作差異（PR #60，與草案不同）**：cross_source 改比兩條**當前價**（`last_price_intraday` vs `last_price`/fast_info），**非**草案的 intraday-vs-daily（後者是當前-vs-昨收、會把正常日內波動誤判）。
>
> **Evidence 量欄位語意**（避免讀者混淆兩個成交量）：
> - `latest_volume` = 最近**已結算**日線的當日量（`drop_phantom_bars` 過後，SPCX 即 06-15 的 256M）。
> - `intraday_volume` = 盤中 1m 累計量——**盤後跑＝當日總量，盤中跑＝部分累計**（note 已據實標明）。目前無 analyst/sage 引用，預留 P1+ 量價/flow 分析（C7）。
> - `last_price_intraday` 沿用 `last_price_*` 命名與 `last_price`（fast_info）成對，標示兩者皆為「當前價」讀數、供跨源比對。

### 3. 分維度資料健康度評分卡（S7 + C8）

新增結構化 `DataHealthCard`，取代「全域封頂 0.5」這個粗魯訊號：

```
DataHealthCard:
  dimensions: {
    technical:    {status: healthy|degraded|missing|fatal, reason, evidence_count, oldest_as_of},
    fundamentals: {...},
    macro:        {...},
    sentiment:    {...},   # news/chips
    price:        {...},   # quote 跨源一致性
  }
  overall: ok | degraded | blocked
  confidence_cap: float | None   # 由最壞維度推導，不再固定 0.5
```

- brief 揭露由「資料品質：⚠ 降級」改為逐維度：「price 健康／fundamentals 降級（財報 130 天舊）／technical 缺（僅 3 交易日）」。讀者（人或 AI judge）即可對自己關心的 thesis 精準折價。
- 評分卡同時寫入 `verdict.json` 與 `details/data_quality.md`，成為 C8 要的可回溯留痕。
- `confidence_cap` 改為「最壞維度決定」而非一律 0.5——例如只有 macro 降級時，technical 短線裁定不該被腰斬。

### 4. 內部矛盾 sanity 上限（S6）

deterministic_checks 加一組「物理不可能」檢查：forward/trailing P/E 絕對值超上限（暫定 |PE| > 1000）、市值與 shares×price 嚴重背離、EPS 與 PE 反推價格與報價矛盾等。命中 → fatal（資料源錯配，如 ticker 重用）或至少 error。閾值寫進 `AuditConfig`。

## 範圍

### In scope（本 spec）

- **P0**（先做）：S4 fatal tier + halt、S1 幽靈 bar 防呆、S2/C1 獨立第二價格源、S7 分維度評分卡。
- **P1**（接著）：S5/S6 納入 fatal 判準、C2 US fundamentals fallback、C8 評分卡留痕（與 S7 同載體）。
- 對應測試：`test_data_audit.py` fatal/halt 路徑、`test_us_stocks.py`/`test_tw_stocks.py` 幽靈 bar 過濾、跨源背離 fatal、評分卡 schema。

### Out of scope（留後續或 P2 follow-up）

- C3-C7（TW 現金流量、TW 目標價、台灣總經/匯率、news 情緒量化、ATR/RS）——完整性補強，工程量大，分批另開。
- 跨資產（crypto/A 股/港股）。
- 第二家商業 provider 接入（若 intraday 路徑足夠則不需要）。

## 驗收條件

P0（2026-06-17 完成，commit 9e7a5ac/0c4b52e/15f147f/c38621d）：

- [x] SPCX 跑 `analyze` → stage 2 halt，輸出「無法分析」短報告，**不**產出大師團/行動計畫。
      （live 驗證：跨源背離 4.7% → BLOCKED，`runs/SPCX-2026-06-17_155048`）
- [x] `AuditFinding.severity` 支援 `fatal`；`AuditReport.blocked`；LLM 稽核員任何發現一律壓到 warning。
- [x] US/TW quote/history 取最後一根前過濾 Volume=0 退化 bar（`drop_phantom_bars`）；退回真實 bar 並標其 as_of。
- [x] cross_source 改比兩條當前價（intraday vs fast_info）→ 背離 fatal；無 intraday 跳過（不退回當前-vs-昨收誤判）。
- [x] 全域 `confidence_cap=0.5` 改為 `DataHealthCard` 分維度推導（核心 0.5／周邊 0.7）；brief + verdict.json 逐維度揭露。
- [x] 既有測試全綠 + 新增 fatal/halt/評分卡/幽靈 bar/跨源測試（149→278 期間基線；本批 +16，全綠）。

P1（待續）：

- [ ] S5：歷史不足（<30 交易日）→ 偵測為 IPO/新上市特例，評分卡 technical=missing/limited + brief 明示「新上市、僅 N 交易日、技術面有限」，**不 fatal、不阻斷**（依 D1）。
- [ ] S6：內部矛盾 sanity（|PE| 上限、市值 vs shares×price）命中 → fatal/error（目前僅 LLM 稽核員標 warning）。
- [ ] C2：US fundamentals 無 CIK 時的 yfinance 二手 fallback。
- [ ] ROADMAP 更新：新增 Phase（Spec F）與 W11+ 弱點編號。

## 待定決議

> **2026-06-17 定案**：D1 由 DK 拍板（見下）；D2/D3/D4 DK ack「按 Loom 偏好、大方向如規劃」採用。

- **D1（DK 2026-06-17 定案）**：歷史過短**屬 IPO/新上市特例，不視為資料錯誤、不 fatal halt**——明說承認即可。S5 的處理：`drop_phantom_bars` 過後若 < 30 交易日 → 評分卡 technical 標 missing/limited，brief 明示「本標的為新上市，僅 N 個交易日，技術面有限」，**仍依基本面/新聞/情緒繼續分析**（不阻斷、不硬出技術錨點）。
- **D2（採用）**：fatal halt **仍寫 `runs/<TICKER>-.../`** 留痕、可重跑，verdict 標 `無法分析`。（S4 已實作）
- **D3（採用）**：分維度 `confidence_cap` **由最壞維度決定**（核心 0.5／周邊 0.7）。（S7 已實作）
- **D4（採用，含已知限制）**：獨立第二價格源 P0 用 yfinance intraday（零新依賴）；同源 Yahoo、不同 endpoint＝非完全異源，correlated-failure 風險仍存，真正異源第二 provider 為 **P2 必要 follow-up**。（S2 已實作 + 新鮮度守衛）
