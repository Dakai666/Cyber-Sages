"""七階段管線編排。發 stage event 給 CLI，全程產物可序列化。"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from cyber_sages.agents.analysts import run_all_analysts
from cyber_sages.agents.council import run_council
from cyber_sages.agents.debate import run_debate
from cyber_sages.agents.schemas import (
    AnalystReport,
    CouncilVerdict,
    DebateArgument,
    DebateVerdict,
    FinalVerdict,
    RiskNote,
)
from cyber_sages.agents.synthesis import run_synthesis
from cyber_sages.config import Settings
from cyber_sages.data.base import (
    ChipsProvider,
    EstimateProvider,
    detect_instrument,
    detect_market,
    make_macro_provider,
    make_provider,
)
from cyber_sages.data.evidence import EvidenceStore
from cyber_sages.llm.gateway import LLMGateway
from cyber_sages.verify.data_audit import AuditReport, run_audit

StageStatus = Literal["running", "done", "warn", "fail"]
StageCallback = Callable[[str, StageStatus, str], None | Awaitable[None]]

STAGES = [
    ("collect", "1 採集 Collect"),
    ("audit", "2 資料審核 Audit"),
    ("analyze", "3 分析師 Analyze"),
    ("cite", "4 引用驗證 Cite-Check"),
    ("council", "5 大師合議 Council"),
    ("debate", "6 多空辯論 Debate"),
    ("synthesize", "7 首席合議 Synthesize"),
]


def _git_commit() -> str | None:
    """產出本次 run 的程式版本標記：短 hash（+dirty）+ 分支，如 `5a38ec9 (main)`。

    以套件原始碼目錄（而非使用者 cwd）為基準——pip 安裝在 site-packages 或
    release tarball 等非 git 環境下回 None，呼叫端略過此欄即可。
    """
    src_dir = Path(__file__).resolve().parent
    try:
        def run(*args: str) -> str | None:
            r = subprocess.run(["git", *args], capture_output=True, text=True,
                               cwd=src_dir, timeout=5)
            return r.stdout.strip() if r.returncode == 0 else None

        commit = run("rev-parse", "--short", "HEAD")
        if not commit:
            return None
        # 帶未 commit 改動的 run 標 +dirty——issue #7 的誤判場景正是「程式碼
        # 與產出對不上」，dirty run 必須能被一眼識別
        if run("status", "--porcelain", "--untracked-files=no"):
            commit += "+dirty"
        branch = run("rev-parse", "--abbrev-ref", "HEAD")
        return f"{commit} ({branch})" if branch and branch != "HEAD" else commit
    except Exception:
        return None


class AnalysisResult(BaseModel):
    ticker: str
    # 一次性時間戳（本地時區）：資料夾名 / brief 標題 / payload 全部共用，避免各自 now() 漂移
    generated_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    # 程式版本標記（短 hash + 分支；非 git 環境為 None）：brief / data_quality /
    # verdict.json 都會帶，review 舊 run 時不再誤判「修補沒生效」。
    # default_factory 於 run 結尾建構本物件時求值；秒級 run 下與開始時等價，
    # 若未來 run 長到中途可能出現新 commit，改為 run_pipeline 開頭快照
    git_commit: str | None = Field(default_factory=_git_commit)
    store: EvidenceStore
    audit: AuditReport
    reports: list[AnalystReport]
    council: CouncilVerdict
    bull: DebateArgument | None = None
    bear: DebateArgument | None = None
    debate: DebateVerdict | None = None
    verdict: FinalVerdict
    risk: RiskNote


async def _emit(cb: StageCallback | None, stage: str, status: StageStatus, detail: str = "") -> None:
    if cb is None:
        return
    result = cb(stage, status, detail)
    if asyncio.iscoroutine(result):
        await result


async def run_pipeline(
    ticker: str,
    settings: Settings,
    gateway: LLMGateway,
    *,
    n_sages: int | None = None,
    skip_debate: bool = False,
    include_macro: bool = True,
    on_stage: StageCallback | None = None,
    on_signal=None,
) -> AnalysisResult:
    ticker = ticker.upper()
    market = detect_market(ticker)
    provider = make_provider(market)

    # [1] Collect — 個股四路 + 台股籌碼（若 provider 支援）+ 總經（全市場共用）
    src_label = "FinMind / yfinance .TW" if market == "TW" else "yfinance / SEC EDGAR"
    macro_provider = make_macro_provider() if include_macro else None
    macro_note = " + FRED 總經" if macro_provider is not None else ""
    await _emit(on_stage, "collect", "running",
                f"[{market}] {src_label} for {ticker}{macro_note}")
    store = EvidenceStore(ticker=ticker, market=market,
                          instrument=detect_instrument(ticker))

    collectors = [
        provider.get_quote(ticker), provider.get_history(ticker),
        provider.get_fundamentals(ticker), provider.get_news(ticker),
    ]
    labels = ["quote", "history", "fundamentals", "news"]
    if isinstance(provider, ChipsProvider):
        collectors.append(provider.get_chips(ticker)); labels.append("chips")
    if isinstance(provider, EstimateProvider):
        collectors.append(provider.get_estimates(ticker)); labels.append("estimate")
    if macro_provider is not None:
        collectors.append(macro_provider.get_macro()); labels.append("macro")

    results = await asyncio.gather(*collectors, return_exceptions=True)
    fetch_errors = []
    fetch_failures: dict[str, str] = {}  # W9：類別→錯誤訊息，交給 audit 顯式入帳/分級
    for label, r in zip(labels, results):
        if isinstance(r, BaseException):
            fetch_errors.append(f"{label}: {r}")
            fetch_failures[label] = str(r)
        else:
            store.add_all(r)
    if not store.items:
        await _emit(on_stage, "collect", "fail", f"no data: {'; '.join(fetch_errors)}")
        raise RuntimeError(f"No data collected for {ticker}: {fetch_errors}")
    detail = f"{len(store.items)} evidence items"
    if fetch_errors:
        detail += f"（部分來源失敗: {len(fetch_errors)}）"
    await _emit(on_stage, "collect", "warn" if fetch_errors else "done", detail)

    # [2] Audit gate
    await _emit(on_stage, "audit", "running", "deterministic checks + LLM auditor")
    audit = await run_audit(store, settings, gateway, fetch_failures=fetch_failures)
    if audit.degraded:
        await _emit(on_stage, "audit", "warn",
                    f"DEGRADED — {len(audit.errors)} error(s)，信心將封頂 0.5")
    else:
        warns = len(audit.findings)
        await _emit(on_stage, "audit", "done",
                    f"clean ({warns} warning(s))" if warns else "clean")

    # [3] Analysts（內含 cite-check 重寫迴圈）
    await _emit(on_stage, "analyze", "running", "analysts surfacing evidence in parallel")
    reports = await run_all_analysts(store, settings, gateway)
    await _emit(on_stage, "analyze", "done",
                ", ".join(f"{r.analyst.split()[0]}({len(r.claims)})" for r in reports))

    # [4] Cite-check 結果彙報
    total_claims = sum(len(r.claims) for r in reports)
    unverified = sum(len(r.unverified) for r in reports)
    if unverified:
        await _emit(on_stage, "cite", "warn",
                    f"{total_claims - unverified}/{total_claims} claims verified，"
                    f"{unverified} 條標記為 unverified")
    else:
        await _emit(on_stage, "cite", "done", f"{total_claims}/{total_claims} claims verified")

    # [5] Council
    n = n_sages or settings.defaults.sages
    await _emit(on_stage, "council", "running", f"{n} sages voting")
    council = await run_council(store, reports, settings, gateway,
                                n_sages=n, on_signal=on_signal)
    await _emit(on_stage, "council", "done",
                f"{council.bullish}多/{council.neutral}中/{council.bearish}空 "
                f"weighted {council.weighted_score:+.2f}")

    # [6] Debate
    bull = bear = debate = None
    if skip_debate:
        await _emit(on_stage, "debate", "done", "skipped (--no-debate)")
    else:
        await _emit(on_stage, "debate", "running", "bull vs bear, then judge")
        bull, bear, debate = await run_debate(store, reports, council, settings, gateway)
        await _emit(on_stage, "debate", "done", f"judge: {debate.winner}")

    # [7] Synthesize + risk
    await _emit(on_stage, "synthesize", "running", "chief sage + risk officer")
    verdict, risk = await run_synthesis(store, reports, council, debate, audit,
                                        settings, gateway)
    await _emit(on_stage, "synthesize", "done",
                f"{verdict.stance} (conviction {verdict.conviction:.2f})")

    return AnalysisResult(
        ticker=ticker, store=store, audit=audit, reports=reports, council=council,
        bull=bull, bear=bear, debate=debate, verdict=verdict, risk=risk,
    )
