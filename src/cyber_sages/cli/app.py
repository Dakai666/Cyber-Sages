"""Cyber-Sages CLI — Rich Live 流式呈現。

左欄：七階段進度樹；右欄：當前 agent 串流輸出 + Council 即時投票表。
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from pathlib import Path

import typer
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from cyber_sages.agents.schemas import SageSignal
from cyber_sages.config import load_settings
from cyber_sages.llm.gateway import LLMGateway
from cyber_sages.pipeline import STAGES, run_pipeline
from cyber_sages.report import build_agent_payload, render_brief, save_run

app = typer.Typer(add_completion=False, rich_markup_mode="rich")
console = Console()

STATUS_ICON = {"pending": "[dim]○[/]", "running": "[yellow]◉[/]",
               "done": "[green]✓[/]", "warn": "[yellow]⚠[/]", "fail": "[red]✗[/]"}
STANCE_STYLE = {"bullish": "[green]bull[/]", "bearish": "[red]bear[/]",
                "neutral": "[dim]neut[/]"}


class Dashboard:
    def __init__(self, ticker: str):
        self.ticker = ticker
        self.stage_status: dict[str, tuple[str, str]] = {
            key: ("pending", "") for key, _ in STAGES
        }
        self.stream_lines: deque[str] = deque(maxlen=18)
        self.stream_buf = ""
        self.current_role = ""
        self.signals: list[SageSignal] = []

    def on_stage(self, stage: str, status: str, detail: str) -> None:
        self.stage_status[stage] = (status, detail)

    def on_text(self, role: str, delta: str) -> None:
        if role != self.current_role:
            self.current_role = role
            if self.stream_buf:
                self.stream_lines.append(self.stream_buf)
                self.stream_buf = ""
            self.stream_lines.append(f"── {role} ──")
        self.stream_buf += delta
        while "\n" in self.stream_buf:
            line, self.stream_buf = self.stream_buf.split("\n", 1)
            self.stream_lines.append(line)

    def on_signal(self, signal: SageSignal) -> None:
        self.signals.append(signal)

    # ---- rendering ----

    def _stages_panel(self) -> Panel:
        table = Table.grid(padding=(0, 1))
        table.add_column(width=2)
        table.add_column()
        for key, label in STAGES:
            status, detail = self.stage_status[key]
            text = Text.from_markup(f"{label}")
            if detail:
                text.append(f"\n  {detail}", style="dim")
            table.add_row(Text.from_markup(STATUS_ICON[status]), text)
        return Panel(table, title=f"⚔ Cyber-Sages · {self.ticker}", border_style="cyan")

    def _stream_panel(self) -> Panel:
        lines = list(self.stream_lines)
        if self.stream_buf:
            lines.append(self.stream_buf)
        body = Text("\n".join(lines[-16:]), overflow="ellipsis", no_wrap=False)
        return Panel(body, title=f"streaming · {self.current_role or 'idle'}",
                     border_style="magenta")

    def _votes_panel(self) -> Panel | None:
        if not self.signals:
            return None
        table = Table(box=None, pad_edge=False)
        table.add_column("sage", style="bold", max_width=16)
        table.add_column("stance", width=6)
        table.add_column("conf", width=4, justify="right")
        for s in self.signals:
            table.add_row(s.sage, Text.from_markup(STANCE_STYLE[s.stance]),
                          f"{s.confidence:.1f}")
        bulls = sum(1 for s in self.signals if s.stance == "bullish")
        bears = sum(1 for s in self.signals if s.stance == "bearish")
        return Panel(table, border_style="blue",
                     title=f"council {bulls}🐂 {len(self.signals) - bulls - bears}⚖ {bears}🐻")

    def render(self) -> Layout:
        layout = Layout()
        layout.split_row(Layout(name="left", ratio=2), Layout(name="right", ratio=3))
        layout["left"].update(self._stages_panel())
        votes = self._votes_panel()
        right = Group(self._stream_panel(), votes) if votes else self._stream_panel()
        layout["right"].update(right)
        return layout


@app.command()
def analyze(
    ticker: str = typer.Argument(..., help="股票代號：AAPL（美股）/ 2330（台股）"),
    sages: int = typer.Option(None, "--sages", help="出席大師數（預設 config）"),
    depth: str = typer.Option("full", "--depth", help="full | quick（較省 token）"),
    no_debate: bool = typer.Option(False, "--no-debate", help="跳過多空辯論"),
    no_macro: bool = typer.Option(False, "--no-macro", help="不帶入 FRED 總經背景"),
    as_json: bool = typer.Option(False, "--json",
                                 help="agent 模式：stdout 輸出 verdict.json，進度走 stderr"),
    config: Path = typer.Option(None, "--config", help="自訂 config.yaml 路徑"),
):
    """跑完整七階段分析管線，產出可操作的決策簡報。"""
    settings = load_settings(config)
    if depth == "quick":
        settings.defaults.max_tokens = min(settings.defaults.max_tokens, 4096)
        if sages is None:
            sages = min(settings.defaults.sages, 5)
    include_macro = settings.defaults.include_macro and not no_macro

    if as_json:
        result = _run_plain(ticker, settings, sages, no_debate, include_macro)
        out_dir = save_run(result)
        print(json.dumps(build_agent_payload(result), ensure_ascii=False, indent=2))
        Console(stderr=True).print(f"[dim]saved: {out_dir}[/]")
        return

    dash = Dashboard(ticker.upper())
    gateway = LLMGateway(settings, on_text=dash.on_text)

    async def main():
        with Live(dash.render(), console=console, refresh_per_second=8,
                  vertical_overflow="crop") as live:
            ticker_task = asyncio.create_task(run_pipeline(
                ticker, settings, gateway,
                n_sages=sages, skip_debate=no_debate, include_macro=include_macro,
                on_stage=dash.on_stage, on_signal=dash.on_signal,
            ))
            while not ticker_task.done():
                live.update(dash.render())
                await asyncio.sleep(0.12)
            live.update(dash.render())
            return await ticker_task

    try:
        result = asyncio.run(main())
    except RuntimeError as e:
        console.print(f"[red]分析失敗：{e}[/]")
        raise typer.Exit(1)

    out_dir = save_run(result)
    console.print()
    console.rule("[bold cyan]決策簡報")
    console.print(Markdown(render_brief(result)))
    console.print(f"\n[dim]已儲存：{out_dir}/ → brief.md · verdict.json · details/ · evidence.json[/]")


def _run_plain(ticker: str, settings, sages: int | None, no_debate: bool,
               include_macro: bool = True):
    """agent 模式：無 Live 畫面，stage 進度印到 stderr。"""
    err = Console(stderr=True)
    gateway = LLMGateway(settings)  # 不串流文字，省 stderr 噪音

    def on_stage(stage: str, status: str, detail: str) -> None:
        err.print(f"[dim][{status:7s}] {stage}: {detail}[/]")

    try:
        return asyncio.run(run_pipeline(
            ticker, settings, gateway,
            n_sages=sages, skip_debate=no_debate, include_macro=include_macro,
            on_stage=on_stage,
        ))
    except RuntimeError as e:
        err.print(f"[red]分析失敗：{e}[/]")
        raise typer.Exit(1)


@app.command()
def doctor(config: Path = typer.Option(None, "--config")):
    """檢查 API keys 與兩家 provider 連線（各打一個最小請求）。"""
    settings = load_settings(config)
    gateway = LLMGateway(settings)

    async def ping(role: str):
        result = await gateway.complete(
            role, system="Reply with exactly: ok", prompt="ping", max_tokens=32,
        )
        return result.model, result.text.strip()[:40]

    roles = {cfg.provider: name for name, cfg in settings.roles.items()}  # 每 provider 取一角色
    for provider, role in roles.items():
        try:
            model, text = asyncio.run(ping(role))
            console.print(f"[green]✓[/] {provider:10s} role={role:12s} model={model} → {text!r}")
        except Exception as e:
            console.print(f"[red]✗[/] {provider:10s} role={role:12s} → {e}")

    # 資料源連線檢查
    console.rule("[dim]data sources")
    _check_data_sources()


def _check_data_sources() -> None:
    import os

    import httpx

    from cyber_sages.data.finmind import finmind_get
    from cyber_sages.data.macro import FredMacroProvider

    async def finmind_probe() -> int:
        async with httpx.AsyncClient(timeout=30) as client:
            rows = await finmind_get(client, "TaiwanStockPrice", "2330",
                                     start_date="2026-01-01")
            return len(rows)

    tok = "set" if os.environ.get("FINMIND_TOKEN") else "missing"
    try:
        n = asyncio.run(finmind_probe())
        console.print(f"[green]✓[/] FinMind   token={tok:7s} → TaiwanStockPrice 2330 {n} rows")
    except Exception as e:
        console.print(f"[red]✗[/] FinMind   token={tok:7s} → {e}")

    if FredMacroProvider.available():
        try:
            evs = asyncio.run(FredMacroProvider().get_macro())
            console.print(f"[green]✓[/] FRED      key=set     → {len(evs)} macro series")
        except Exception as e:
            console.print(f"[red]✗[/] FRED      key=set     → {e}")
    else:
        console.print("[yellow]○[/] FRED      key=missing → 總經停用（analyze 會自動跳過總經分析師）")


if __name__ == "__main__":
    app()
