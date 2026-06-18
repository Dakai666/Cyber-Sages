"""輸出層：

runs/<TICKER>-<date>_<time>/
├── brief.md          一頁式決策簡報（主產出，回答「現在怎麼做」）
├── verdict.json      machine-readable 決策 payload（給呼叫端 AI agent 當法官用）
├── details/          深挖用子檔案
│   ├── analysts.md   分析師完整報告 + claim 引用
│   ├── council.md    每位大師完整論點
│   ├── debate.md     多空攻防全文 + 裁判
│   ├── data_quality.md  審核發現 + 風控官意見
│   └── evidence.md   證據附錄總表
├── full_trace.json   全管線產物
└── evidence.json     原始證據
"""

from __future__ import annotations

import json
from pathlib import Path

from cyber_sages.agents.schemas import PriceLevel
from cyber_sages.pipeline import AnalysisResult
from cyber_sages.verify.data_audit import build_health_card

# 維度中文標籤（評分卡揭露用）
_DIM_ZH = {"price": "當前價", "technical": "技術面", "fundamentals": "基本面",
           "sentiment": "情緒/籌碼", "macro": "總經"}
_STATUS_ZH = {"healthy": "健康", "degraded": "降級", "missing": "缺", "fatal": "致命"}

STANCE_ZH = {"bullish": "看多 🐂", "bearish": "看空 🐻", "neutral": "中性 ⚖️"}
ACTION_ZH = {
    "buy_now": "立即買進", "buy_dip": "回檔買進", "hold": "持有續抱",
    "reduce": "減碼", "avoid": "觀望勿進", "sell": "出清",
}
HORIZON_ZH = {"short": "短期 1-4週", "mid": "中期 1-6月", "long": "長期 6月+"}
# P7：neutral 三類的中文標籤（brief / payload 共用）
NEUTRAL_REASON_ZH = {
    "out_of_circle": "能力圈外", "insufficient_signal": "訊號不足",
    "balanced_forces": "多空平衡",
}


def _current_price(result: AnalysisResult) -> tuple[float | None, str]:
    for field in ("last_price", "latest_close"):
        for e in result.store.items:
            if e.field == field and isinstance(e.value, (int, float)):
                return float(e.value), e.id
    return None, ""


def _level_row(label: str, lv: PriceLevel) -> str:
    ids = " ".join(lv.evidence_ids)
    return f"| {label} | {lv.price:g} | {lv.basis} {ids} |"


# ---------- brief.md ----------

def _render_blocked_brief(result: AnalysisResult) -> str:
    """資料閘門 fatal → 「無法分析」短報告：只說壞在哪、缺什麼、可否重跑，
    不演大師團/行動計畫（在錯的資料上產出帶錨點的裁定，比沒有裁定更危險）。"""
    a = result.audit
    commit = f" · commit `{result.git_commit}`" if result.git_commit else ""
    lines = [
        f"# {result.ticker}（{result.store.market}）決策簡報 · "
        f"{result.generated_at.strftime('%Y-%m-%d %H:%M:%S %Z')}{commit}",
        "",
        "## 裁定：⛔ 無法分析（資料閘門中止）",
        "**本次分析的資料前提無法成立——下列致命問題若不修正，任何裁定、陪審團投票與"
        "行動計畫都是建立在錯誤數字上，故管線在資料審核階段即中止，不產出大師團與行動計畫。**",
        "",
        "## 致命問題（fatal）",
        *[f"- 🛑 **[{f.check}]** {f.message}"
          + (f"（{' '.join(f.evidence_ids)}）" if f.evidence_ids else "")
          for f in a.fatals],
        "",
    ]
    others = [f for f in a.findings if f.severity != "fatal"]
    if others:
        lines += ["## 其他資料品質提示",
                  *[f"- {'🔴' if f.severity == 'error' else '🟡'} [{f.check}] {f.message}"
                    for f in others], ""]
    lines += [
        "## 如何重跑",
        "- 確認資料源（行情/財報）已回補真實值後重跑 `analyze`；",
        "- 若為新上市/識別錯配的標的，請確認 ticker 與市場路由正確。",
        "",
        f"已收集 {len(result.store.items)} 筆證據（見 `evidence.json`），"
        "但因致命問題未進入分析階段。",
    ]
    # #7：列出涉事的壞值 evidence id，方便對著 evidence.json debug
    bad_ids = sorted({eid for f in a.fatals for eid in f.evidence_ids})
    if bad_ids:
        lines.append(f"涉事證據：{' '.join(bad_ids)}（對照 `evidence.json` 查原值與來源）")
    return "\n".join(lines)


def render_brief(result: AnalysisResult) -> str:
    if result.blocked:
        return _render_blocked_brief(result)
    v = result.verdict
    plan = v.action_plan
    c = result.council
    price, price_id = _current_price(result)
    price_str = f"現價 **{price:g}** `[{price_id}]`" if price else "現價不可得"

    horizon_zh = {"value": "長期價值（數年）", "trading": "短線交易（數天~數週）"}
    aggression_zh = {"conservative": "保守陪審團", "aggressive": "激進陪審團", None: "大師會堂（全員）"}
    lines = [
        f"# {result.ticker}（{result.store.market}）決策簡報 · "
        f"{horizon_zh.get(result.horizon, result.horizon)} · "
        f"{aggression_zh.get(result.aggression, result.aggression)} · "
        f"{result.generated_at.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"{price_str} · 陪審團 {len(c.signals)} 席"
        + (f" ⚠️ {len(c.absent)} 席缺席（{', '.join(c.absent)}）" if c.absent else "")
        + (f" · commit `{result.git_commit}`" if result.git_commit else ""),
        "",
        f"## 裁定：{ACTION_ZH[plan.action]} · {STANCE_ZH[v.stance]} · 信心 {v.conviction:.2f}",
        f"**{plan.directive}**",
        "",
    ]

    rows = []
    for lv in plan.entry_zone:
        rows.append(_level_row(f"進場（{lv.label}）", lv))
    if plan.stop_loss:
        rows.append(_level_row(f"停損（{plan.stop_loss.label}）", plan.stop_loss))
    for lv in plan.targets:
        rows.append(_level_row(f"目標（{lv.label}）", lv))
    if rows:
        lines += ["| 動作 | 價位 | 錨點 |", "|---|---|---|", *rows, ""]
    if plan.position_hint:
        lines.append(f"**倉位**：{plan.position_hint}")
    if plan.invalidation:
        lines.append(f"**計畫作廢條件**：{plan.invalidation}")
    lines.append("")

    if v.horizons:
        lines.append("## 三時間軸")
        for h in v.horizons:
            lines.append(f"- **{HORIZON_ZH.get(h.horizon, h.horizon)}** "
                         f"{STANCE_ZH[h.stance]}：{h.summary}")
        lines.append("")

    debate_line = ""
    if result.debate:
        debate_line = f" · 辯論裁判：**{result.debate.winner}** 勝"
    lines += [
        f"## 陪審團 {c.bullish}🐂 {c.neutral}⚖ {c.bearish}🐻 · "
        f"加權 {c.weighted_score:+.2f}{debate_line}",
    ]
    # review C：明確標示這是「與多數不同調者」的概念——與 debate 的「敗方代表反駁」清單
    # （持敗方 stance 者，不限離群者）是兩個不同集合，可能無交集，分開呈現避免讀者誤併。
    if c.outliers:
        lines.append(f"與多數不同調者：{'、'.join(c.outliers)}（少數意見已強制進入辯論；"
                     "辯論段另列敗方代表的逐點反駁）")
    # P7：中性細分——讓判讀者區分「沒人懂（能力圈外）」「資料不足」「真的勢均力敵」三種 neutral。
    if c.neutral and c.neutral_by_reason:
        parts = [f"{NEUTRAL_REASON_ZH.get(k, k)} {v}"
                 for k, v in c.neutral_by_reason.items()]
        lines.append(f"中性細分：{'、'.join(parts)}")
    if c.abstained:
        lines.append(f"未出席（非本 horizon，誠實退場）：{'、'.join(c.abstained)}")
    # P2：兩階段時揭露深入/速覽分布——讓判讀者知道哪些是深度推理、哪些僅 scout 粗判。
    if c.scouted_only:
        deep_n = len(c.signals) - len(c.scouted_only)
        lines.append(f"兩階段合議：{deep_n} 位深入推理 · {len(c.scouted_only)} 位 scout 速覽"
                     f"（{'、'.join(c.scouted_only)}）；scout 票信心已打折、未經 deep 規則校準，"
                     "且不進辯論的論點級攻防")
    if result.debate and result.debate.unrebutted_outliers:
        lines.append(f"⚠️ 裁判未完成論點級反駁：{'、'.join(result.debate.unrebutted_outliers)}"
                     "（其核心論點尚未被正面回應，閱讀時請自行加權）")
    lines += ["", v.thesis, ""]

    if v.key_risks:
        lines += ["## 最大風險", *[f"- {r}" for r in v.key_risks[:4]], ""]
    if v.what_would_change_my_mind:
        lines.append(f"**翻盤條件**：{v.what_would_change_my_mind}")
    if v.dissent_summary:
        lines.append(f"**少數派**：{v.dissent_summary}")

    # S7：分維度健康度揭露——讓讀者一眼看出壞在哪一維度，對自己關心的 thesis 精準折價，
    # 不再只有「降級 0.5」一句全域標籤。
    card = build_health_card(result.audit, result.store)
    dim_str = "／".join(f"{_DIM_ZH[d]} {_STATUS_ZH[h.status]}"
                        for d, h in card.dimensions.items())
    if card.overall == "degraded":
        dq = f"⚠️ 降級（信心上限 {card.confidence_cap}）· {dim_str}"
    elif result.audit.findings:
        dq = f"✅ 通過（{len(result.audit.findings)} 項提示）· {dim_str}"
    else:
        dq = f"✅ 乾淨 · {dim_str}"
    flat = [(r.analyst, u) for r in result.reports for u in r.unverified]
    flat += [("首席 brief", u) for u in v.unverified]  # W7：chief 主體未過驗證的數字
    uv = f" · {len(flat)} 條 claim 未過引用驗證" if flat else ""
    lines += [
        "",
        f"資料品質：{dq}{uv} — 深挖請看 `details/`",
    ]
    # D1：IPO/新上市明說承認——讓讀者知道技術面有限是「標的太新」而非「資料壞了」。
    tech = card.dimensions.get("technical")
    if tech and tech.status == "missing" and "新上市" in (tech.reason or ""):
        # [:120] 截斷：reason 未來若混入其他 error 訊息，不讓 ℹ️ 行擠爆 brief（#61-5）。
        lines.append(f"ℹ️ {(tech.reason or '')[:120]}——技術面分析有限，請偏重基本面/新聞/情緒。")

    # #61-2：幽靈 bar 剔除揭露——只揭露、不進 health card、不影響 cap（資料源未結算佔位，
    # 剔除是正確處置而非降級）。讓讀者知道「為何 latest_close 不是日曆最新一天」。
    phantom = next((e for e in result.store.items
                    if e.category == "quote" and e.field == "phantom_bars_dropped"), None)
    if phantom is not None and phantom.value:
        lines.append(f"ℹ️ 已剔除 {int(phantom.value)} 根 0 量幽靈 bar（資料源未結算佔位，未污染指標）。")

    # 引用驗證未過清單：brief 一頁可判斷會不會動搖裁定，不必每次再深挖 details/
    if flat:
        lines += ["", "### 引用驗證未過清單"]
        for analyst, u in flat[:5]:
            ids = " ".join(u.evidence_ids) or "—"
            text = u.text if len(u.text) <= 48 else u.text[:47] + "…"
            lines.append(f"- `[{u.tag}]` {analyst}：{text} ｜ 引用 {ids} ｜ {u.reason}")
        if len(flat) > 5:
            lines.append(f"- …其餘 {len(flat) - 5} 條請看 `details/analysts.md`")
    return "\n".join(lines)


# ---------- details/ ----------

def render_analysts(result: AnalysisResult) -> str:
    # P8：analyst 是證據層（核實 + 標註數據），不下方向性立場——故無 stance 標示。
    lines = [f"# 分析師核實證據 · {result.ticker}",
             "> 分析師只提供核實過的數據與事實；方向性判斷見大師合議。"]
    for r in result.reports:
        lines += ["", f"## {r.analyst}", r.summary, ""]
        for cl in r.claims:
            lines.append(f"- {cl.text} `[{', '.join(cl.evidence_ids)}]`")
        if r.unverified:
            lines += ["", "> ⚠️ 以下 claim 未通過引用驗證："]
            lines += [f"> - `[{u.tag}]` {u.text} ｜ 引用 {' '.join(u.evidence_ids) or '—'}"
                      f" ｜ {u.reason}" for u in r.unverified]
    return "\n".join(lines)


def render_council(result: AnalysisResult) -> str:
    c = result.council
    lines = [
        f"# 大師合議 · {result.ticker}",
        f"投票：{c.bullish} 多 / {c.neutral} 中性 / {c.bearish} 空"
        f"（加權 {c.weighted_score:+.2f}，共識 {c.consensus}）",
    ]
    for s in c.signals:
        lines += [
            "", f"## {s.sage} — {STANCE_ZH[s.stance]}（信心 {s.confidence:.1f}）",
            s.thesis,
            f"- 關鍵證據：{', '.join(s.key_evidence_ids) or '—'}",
            f"- 什麼會讓他改變看法：{s.what_would_change_my_mind}",
        ]
    return "\n".join(lines)


def render_debate(result: AnalysisResult) -> str:
    if not result.debate:
        return "# 多空辯論\n\n（本次執行跳過辯論）"
    d = result.debate
    lines = [f"# 多空辯論 · {result.ticker}", ""]
    # P3 雙盲：開場為第一輪盲打、反駁為第二輪見對手後的回應，分開呈現讓判讀者看見對稱攻防。
    if result.bull:
        lines += ["## 多方陳詞", "### 開場（盲打）", result.bull.argument]
        if result.bull.rebuttal:
            lines += ["### 反駁（見空方開場後）", result.bull.rebuttal]
        lines.append("")
    if result.bear:
        lines += ["## 空方陳詞", "### 開場（盲打）", result.bear.argument]
        if result.bear.rebuttal:
            lines += ["### 反駁（見多方開場後）", result.bear.rebuttal]
        lines.append("")
    lines += [
        f"## 裁判判定：{d.winner}",
        d.rationale, "",
        f"- 最強多方論點：{d.strongest_bull_point}",
        f"- 最強空方論點：{d.strongest_bear_point}",
    ]
    if d.unresolved_risks:
        lines.append("- 未解風險：" + "；".join(d.unresolved_risks))
    if d.outlier_rebuttals:
        lines += ["", "## 對敗方核心論點的逐點反駁"]
        for rb in d.outlier_rebuttals:
            ids = f" `[{', '.join(rb.evidence_ids)}]`" if rb.evidence_ids else ""
            lines += [f"### {rb.sage}", f"- 核心論點：{rb.thesis_point}",
                      f"- 反駁：{rb.rebuttal}{ids}"]
    if d.unrebutted_outliers:
        lines += ["", f"> ⚠️ 裁判未對 {len(d.unrebutted_outliers)} 位敗方代表完成論點級"
                  f"反駁：{'、'.join(d.unrebutted_outliers)}（其核心論點尚未被正面回應）"]
    return "\n".join(lines)


def render_data_quality(result: AnalysisResult) -> str:
    # 產出時間 + commit 直接進檔頭：review 開到舊 run 時一眼可判斷是基於哪版程式
    stamp = result.generated_at.strftime("%Y-%m-%d %H:%M:%S %Z")
    commit = f" · commit `{result.git_commit}`" if result.git_commit else ""
    lines = [f"# 資料品質 · {result.ticker}", f"產出於 {stamp}{commit}"]
    # S7：分維度評分卡——逐維度狀態 + 受損原因 + 證據筆數/最舊日期，留痕可回溯（C8）。
    card = build_health_card(result.audit, result.store)
    lines += ["", f"## 健康度評分卡（overall: {card.overall}"
              + (f"，信心上限 {card.confidence_cap}" if card.confidence_cap is not None else "")
              + ")", "", "| 維度 | 狀態 | 證據數 | 最舊日期 | 受損原因 |", "|---|---|---|---|---|"]
    for d, h in card.dimensions.items():
        # #63-5：primary_finding（最嚴重單一 finding）保證進表——reason 多 finding 串接被 240
        # 截斷時，關鍵矛盾（fatal/市值失調）不會被擠掉。primary 已是 reason 前綴時不重複顯示。
        cell = h.reason or "—"
        if h.primary_finding and not cell.startswith(h.primary_finding[:30]):
            cell = f"⚠ {h.primary_finding}｜{cell}"
        lines.append(f"| {_DIM_ZH[d]} | {_STATUS_ZH[h.status]} | {h.evidence_count} | "
                     f"{h.oldest_as_of or '—'} | {cell} |")
    lines.append("")
    if result.audit.blocked:
        lines.append("\n**⛔ 中止模式**：審核命中 fatal，管線在分析前中止，未產出大師團/裁定。")
    elif result.audit.degraded:
        lines.append("\n**⚠️ 降級模式**：審核有 error，最終信心已封頂 0.5。")
    if result.audit.findings:
        lines.append("")
        icons = {"fatal": "🛑", "error": "🔴", "warning": "🟡"}
        for f in result.audit.findings:
            ids = f" `[{', '.join(f.evidence_ids)}]`" if f.evidence_ids else ""
            lines.append(f"- {icons.get(f.severity, '🟡')} [{f.check}] {f.message}{ids}")
    else:
        lines.append("\n- ✅ 所有確定性檢查通過，稽核員無異常發現")
    if result.audit.auditor_summary:
        lines += ["", f"稽核員總結：{result.audit.auditor_summary}"]
    # risk 在 blocked 時為 None（未跑 synthesis）——僅非中止時呈現風控官意見。
    if result.risk and result.risk.concerns:
        lines += ["", "## 風控官意見", *[f"- {x}" for x in result.risk.concerns]]
    return "\n".join(lines)


def render_evidence(result: AnalysisResult) -> str:
    lines = [
        f"# 證據附錄 · {result.ticker}",
        "所有結論可溯源至以下 evidence（多數為第一手；source 標 (estimate) 者為分析師前瞻共識，二手）：", "",
        "| ID | 欄位 | 值 | 來源 | 日期 |", "|---|---|---|---|---|",
    ]
    for e in result.store.items:
        val = str(e.value)
        if len(val) > 60:
            val = val[:57] + "…"
        val = val.replace("|", "／")
        lines.append(f"| {e.id} | {e.field} | {val} | {e.source} | {e.as_of or ''} |")
    return "\n".join(lines)


# ---------- verdict.json（給 AI agent 法官的 payload） ----------

def build_agent_payload(result: AnalysisResult) -> dict:
    price, price_id = _current_price(result)
    if result.blocked:
        # 資料閘門 fatal → 無大師團/裁定。機器可讀 payload 只報「無法分析」與致命原因，
        # 讓 AI judge 一眼知道這不是低信心結論、而是根本沒有結論。
        return {
            "ticker": result.ticker,
            "market": result.store.market,
            "horizon": result.horizon,
            "aggression": result.aggression,
            "generated_at": result.generated_at.isoformat(),
            "commit": result.git_commit,
            "current_price": {"value": price, "evidence_id": price_id},
            "verdict": None,
            "blocked": True,
            "data_quality": {
                "blocked": True,
                "degraded": True,
                "health_card": build_health_card(result.audit, result.store).model_dump(mode="json"),
                "fatals": [{"check": f.check, "message": f.message,
                            "evidence_ids": f.evidence_ids} for f in result.audit.fatals],
                "errors": [f.message for f in result.audit.errors],
                "warnings": [f.message for f in result.audit.findings
                             if f.severity == "warning"],
            },
            "note_to_judge": (
                "資料審核閘門命中 fatal，管線在分析前中止：本次沒有裁定、沒有陪審團、"
                "沒有行動計畫。這不是低信心結論，而是資料前提無法成立。"
                "請勿據此 payload 做任何買賣判斷；修正 data_quality.fatals 後重跑。"
            ),
        }
    c = result.council
    return {
        "ticker": result.ticker,
        "market": result.store.market,
        # 本次分析時間框架（value/trading）：agent judge 須據此理解票數的語境——同一檔在
        # trading 與 value horizon 的結論可正當地相反，漏標會讓判讀者誤比。brief 已揭露，
        # 機器可讀 payload 也補上（實機 NVDA --horizon trading 驗證時發現此落地缺口）。
        "horizon": result.horizon,
        # 四象限激進軸（conservative/aggressive/null＝大師會堂全員）：與 horizon 同理，judge 須據此
        # 理解這是哪種性格的陪審團——激進陪審團偏多、保守陪審團偏空是組成使然，非標的本身的訊號。
        "aggression": result.aggression,
        "generated_at": result.generated_at.isoformat(),
        "commit": result.git_commit,
        "current_price": {"value": price, "evidence_id": price_id},
        "verdict": result.verdict.model_dump(mode="json"),
        "council": {
            "bullish": c.bullish, "neutral": c.neutral, "bearish": c.bearish,
            "weighted_score": c.weighted_score, "consensus": c.consensus,
            "outliers": c.outliers,
            # P7：neutral 三類細分（能力圈外/訊號不足/多空平衡）——agent 判讀「為何中性」
            "neutral_by_reason": c.neutral_by_reason,
            # 非本 horizon 而誠實退場的大師——票數是「出席者」的，abstained 揭露分母真相
            "abstained": c.abstained,
            # P2：僅 scout 粗判（未深入）的大師——其票計入但無深度推理，揭露給判讀者
            "scouted_only": c.scouted_only,
            "signals": [
                {"sage": s.sage, "stance": s.stance, "confidence": s.confidence,
                 "thesis": s.thesis, "neutral_reason": s.neutral_reason,
                 "what_would_change_my_mind": s.what_would_change_my_mind}
                for s in c.signals
            ],
        },
        "debate": result.debate.model_dump(mode="json") if result.debate else None,
        "data_quality": {
            "blocked": False,
            "degraded": result.audit.degraded,
            # S7：分維度健康度評分卡——judge 可據此判斷壞的是不是自己在乎的維度
            "health_card": build_health_card(result.audit, result.store).model_dump(mode="json"),
            "errors": [f.message for f in result.audit.errors],
            "warnings": [f.message for f in result.audit.findings
                         if f.severity == "warning"],
            "unverified_claims": [
                {"analyst": r.analyst, "text": u.text, "tag": u.tag,
                 "evidence_ids": u.evidence_ids, "reason": u.reason}
                for r in result.reports for u in r.unverified
            ] + [
                {"analyst": "首席 brief", "text": u.text, "tag": u.tag,
                 "evidence_ids": u.evidence_ids, "reason": u.reason}
                for u in result.verdict.unverified  # W7
            ],
        },
        "note_to_judge": (
            "verdict 是幕僚長建議，council 是陪審團票數與個別論點，"
            "debate 是攻防檢驗結果。最終決策權在你；所有數字可用 evidence_id "
            "對 evidence.json 溯源。data_quality.degraded=true 時請自行降低信任。"
        ),
    }


# ---------- save ----------

def save_run(result: AnalysisResult, base_dir: Path | None = None) -> Path:
    stamp = result.generated_at.strftime("%Y-%m-%d_%H%M%S")
    out = (base_dir or Path.cwd() / "runs") / f"{result.ticker}-{stamp}"
    details = out / "details"
    details.mkdir(parents=True, exist_ok=True)

    (out / "brief.md").write_text(render_brief(result), encoding="utf-8")
    with open(out / "verdict.json", "w", encoding="utf-8") as f:
        json.dump(build_agent_payload(result), f, ensure_ascii=False, indent=2)

    # blocked（fatal 中止）時沒有 analyst/council/debate 產物——只寫資料品質與證據留痕，
    # 跳過會在 None council 上炸的明細檔。
    if not result.blocked:
        (details / "analysts.md").write_text(render_analysts(result), encoding="utf-8")
        (details / "council.md").write_text(render_council(result), encoding="utf-8")
        (details / "debate.md").write_text(render_debate(result), encoding="utf-8")
    (details / "data_quality.md").write_text(render_data_quality(result), encoding="utf-8")
    (details / "evidence.md").write_text(render_evidence(result), encoding="utf-8")

    with open(out / "full_trace.json", "w", encoding="utf-8") as f:
        json.dump(result.model_dump(mode="json"), f, ensure_ascii=False, indent=2)
    result.store.to_json_file(out / "evidence.json")
    return out
