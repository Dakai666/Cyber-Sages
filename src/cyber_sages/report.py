"""最終報告輸出：report.md + full_trace.json + evidence.json → runs/<TICKER>-<date>/"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from cyber_sages.pipeline import AnalysisResult

STANCE_ZH = {"bullish": "看多 🐂", "bearish": "看空 🐻", "neutral": "中性 ⚖️"}


def render_markdown(result: AnalysisResult) -> str:
    v = result.verdict
    c = result.council
    lines = [
        f"# Cyber-Sages 分析報告：{result.ticker}",
        f"*{date.today().isoformat()} · 本報告為研究產出，非投資建議*",
        "",
        f"## 最終裁定：{STANCE_ZH[v.stance]}（信心 {v.conviction:.2f}）",
        "",
        v.thesis,
        "",
    ]
    if v.supporting_points:
        lines += ["**支撐論點**", *[f"- {p}" for p in v.supporting_points], ""]
    if v.key_risks:
        lines += ["**關鍵風險**", *[f"- {r}" for r in v.key_risks], ""]
    if v.what_would_change_my_mind:
        lines += [f"**什麼會改變這個看法**：{v.what_would_change_my_mind}", ""]
    if v.dissent_summary:
        lines += [f"**少數派觀點**：{v.dissent_summary}", ""]

    lines += [
        "## 大師合議",
        f"投票：{c.bullish} 多 / {c.neutral} 中性 / {c.bearish} 空 "
        f"（加權分數 {c.weighted_score:+.2f}）",
        "",
        "| 大師 | 立場 | 信心 | 論點 |",
        "|---|---|---|---|",
    ]
    for s in c.signals:
        thesis = s.thesis.replace("\n", " ").replace("|", "／")
        lines.append(f"| {s.sage} | {STANCE_ZH[s.stance]} | {s.confidence:.1f} | {thesis} |")
    lines.append("")

    if result.debate:
        d = result.debate
        lines += [
            "## 多空辯論",
            f"裁判判定：**{d.winner}** — {d.rationale}",
            "",
            f"- 最強多方論點：{d.strongest_bull_point}",
            f"- 最強空方論點：{d.strongest_bear_point}",
        ]
        if d.unresolved_risks:
            lines += ["- 未解風險：" + "；".join(d.unresolved_risks)]
        lines.append("")

    lines += ["## 分析師報告"]
    for r in result.reports:
        lines += [f"### {r.analyst}（{STANCE_ZH[r.outlook]}）", r.summary, ""]
        for cl in r.claims:
            lines.append(f"- {cl.text} `[{', '.join(cl.evidence_ids)}]`")
        if r.unverified_claims:
            lines += ["", "> ⚠️ 以下 claim 未通過引用驗證，僅供參考："]
            lines += [f"> - {u}" for u in r.unverified_claims]
        lines.append("")

    lines += ["## 資料品質"]
    if result.audit.degraded:
        lines.append("**⚠️ 降級模式**：資料審核有 error，最終信心已封頂 0.5。")
    if result.audit.findings:
        for f in result.audit.findings:
            icon = "🔴" if f.severity == "error" else "🟡"
            lines.append(f"- {icon} [{f.check}] {f.message}")
    else:
        lines.append("- ✅ 所有確定性檢查通過，無異常發現")
    if result.risk.concerns:
        lines += ["", "**風控官意見**", *[f"- {x}" for x in result.risk.concerns]]

    lines += [
        "",
        "## 證據附錄",
        "所有結論可溯源至以下第一手資料：",
        "",
        "| ID | 欄位 | 值 | 來源 | 日期 |",
        "|---|---|---|---|---|",
    ]
    for e in result.store.items:
        val = str(e.value)
        if len(val) > 60:
            val = val[:57] + "…"
        val = val.replace("|", "／")
        lines.append(f"| {e.id} | {e.field} | {val} | {e.source} | {e.as_of or ''} |")
    return "\n".join(lines)


def save_run(result: AnalysisResult, base_dir: Path | None = None) -> Path:
    out = (base_dir or Path.cwd() / "runs") / f"{result.ticker}-{date.today().isoformat()}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(render_markdown(result), encoding="utf-8")
    with open(out / "full_trace.json", "w", encoding="utf-8") as f:
        json.dump(result.model_dump(mode="json"), f, ensure_ascii=False, indent=2)
    result.store.to_json_file(out / "evidence.json")
    return out
