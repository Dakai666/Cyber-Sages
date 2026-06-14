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

STANCE_ZH = {"bullish": "看多 🐂", "bearish": "看空 🐻", "neutral": "中性 ⚖️"}
ACTION_ZH = {
    "buy_now": "立即買進", "buy_dip": "回檔買進", "hold": "持有續抱",
    "reduce": "減碼", "avoid": "觀望勿進", "sell": "出清",
}
HORIZON_ZH = {"short": "短期 1-4週", "mid": "中期 1-6月", "long": "長期 6月+"}


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

def render_brief(result: AnalysisResult) -> str:
    v = result.verdict
    plan = v.action_plan
    c = result.council
    price, price_id = _current_price(result)
    price_str = f"現價 **{price:g}** `[{price_id}]`" if price else "現價不可得"

    lines = [
        f"# {result.ticker}（{result.store.market}）決策簡報 · "
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
    if c.outliers:
        lines.append(f"離群者：{'、'.join(c.outliers)}（意見已強制進入辯論）")
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

    dq = "⚠️ 降級（信心已封頂 0.5）" if result.audit.degraded else \
        f"✅ 通過（{len(result.audit.findings)} 項提示）" if result.audit.findings else "✅ 乾淨"
    flat = [(r.analyst, u) for r in result.reports for u in r.unverified]
    flat += [("首席 brief", u) for u in v.unverified]  # W7：chief 主體未過驗證的數字
    uv = f" · {len(flat)} 條 claim 未過引用驗證" if flat else ""
    lines += [
        "",
        f"資料品質：{dq}{uv} — 深挖請看 `details/`",
    ]

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
    if result.bull:
        lines += ["## 多方陳詞", result.bull.argument, ""]
    if result.bear:
        lines += ["## 空方陳詞", result.bear.argument, ""]
    lines += [
        f"## 裁判判定：{d.winner}",
        d.rationale, "",
        f"- 最強多方論點：{d.strongest_bull_point}",
        f"- 最強空方論點：{d.strongest_bear_point}",
    ]
    if d.unresolved_risks:
        lines.append("- 未解風險：" + "；".join(d.unresolved_risks))
    if d.outlier_rebuttals:
        lines += ["", "## 對敗方離群者的論點級反駁"]
        for rb in d.outlier_rebuttals:
            ids = f" `[{', '.join(rb.evidence_ids)}]`" if rb.evidence_ids else ""
            lines += [f"### {rb.sage}", f"- 核心論點：{rb.thesis_point}",
                      f"- 反駁：{rb.rebuttal}{ids}"]
    if d.unrebutted_outliers:
        lines += ["", f"> ⚠️ 裁判未對 {len(d.unrebutted_outliers)} 位敗方離群者完成論點級"
                  f"反駁：{'、'.join(d.unrebutted_outliers)}（其核心論點尚未被正面回應）"]
    return "\n".join(lines)


def render_data_quality(result: AnalysisResult) -> str:
    # 產出時間 + commit 直接進檔頭：review 開到舊 run 時一眼可判斷是基於哪版程式
    stamp = result.generated_at.strftime("%Y-%m-%d %H:%M:%S %Z")
    commit = f" · commit `{result.git_commit}`" if result.git_commit else ""
    lines = [f"# 資料品質 · {result.ticker}", f"產出於 {stamp}{commit}"]
    if result.audit.degraded:
        lines.append("\n**⚠️ 降級模式**：審核有 error，最終信心已封頂 0.5。")
    if result.audit.findings:
        lines.append("")
        for f in result.audit.findings:
            icon = "🔴" if f.severity == "error" else "🟡"
            ids = f" `[{', '.join(f.evidence_ids)}]`" if f.evidence_ids else ""
            lines.append(f"- {icon} [{f.check}] {f.message}{ids}")
    else:
        lines.append("\n- ✅ 所有確定性檢查通過，稽核員無異常發現")
    if result.audit.auditor_summary:
        lines += ["", f"稽核員總結：{result.audit.auditor_summary}"]
    if result.risk.concerns:
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
    c = result.council
    return {
        "ticker": result.ticker,
        "market": result.store.market,
        "generated_at": result.generated_at.isoformat(),
        "commit": result.git_commit,
        "current_price": {"value": price, "evidence_id": price_id},
        "verdict": result.verdict.model_dump(mode="json"),
        "council": {
            "bullish": c.bullish, "neutral": c.neutral, "bearish": c.bearish,
            "weighted_score": c.weighted_score, "consensus": c.consensus,
            "outliers": c.outliers,
            "signals": [
                {"sage": s.sage, "stance": s.stance, "confidence": s.confidence,
                 "thesis": s.thesis,
                 "what_would_change_my_mind": s.what_would_change_my_mind}
                for s in c.signals
            ],
        },
        "debate": result.debate.model_dump(mode="json") if result.debate else None,
        "data_quality": {
            "degraded": result.audit.degraded,
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

    (details / "analysts.md").write_text(render_analysts(result), encoding="utf-8")
    (details / "council.md").write_text(render_council(result), encoding="utf-8")
    (details / "debate.md").write_text(render_debate(result), encoding="utf-8")
    (details / "data_quality.md").write_text(render_data_quality(result), encoding="utf-8")
    (details / "evidence.md").write_text(render_evidence(result), encoding="utf-8")

    with open(out / "full_trace.json", "w", encoding="utf-8") as f:
        json.dump(result.model_dump(mode="json"), f, ensure_ascii=False, indent=2)
    result.store.to_json_file(out / "evidence.json")
    return out
