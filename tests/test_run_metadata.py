"""Run 輸出的程式版本標記（issue #7）：brief / data_quality / verdict.json 都帶 commit。"""

import re
import subprocess

from cyber_sages.config import load_settings
from cyber_sages.pipeline import _git_commit, run_pipeline
from cyber_sages.report import build_agent_payload, render_brief, render_data_quality
from tests.test_pipeline_dry import FakeGateway
from tests.test_pipeline_dry_tw import _patch_tw_provider


def test_git_commit_in_live_repo():
    # 本 repo 是 git 環境：應拿到短 hash 開頭（可能帶 +dirty 與分支尾註）
    out = _git_commit()
    assert out and re.match(r"^[0-9a-f]{7,}", out)


def test_git_commit_none_without_git(monkeypatch):
    # 非 git 環境（release tarball / 無 git 可執行檔）：回 None 而非炸
    def boom(*args, **kwargs):
        raise FileNotFoundError("git not installed")

    monkeypatch.setattr(subprocess, "run", boom)
    assert _git_commit() is None


async def _dry_result(monkeypatch):
    _patch_tw_provider(monkeypatch, etf=True)
    return await run_pipeline("0050", load_settings(), FakeGateway(),  # type: ignore[arg-type]
                              n_sages=2, skip_debate=True, include_macro=False)


async def test_commit_stamped_into_outputs(monkeypatch):
    result = await _dry_result(monkeypatch)
    result.git_commit = "abc1234 (main)"  # 固定值：斷言不依賴當前 repo 狀態

    assert "commit `abc1234 (main)`" in render_brief(result)
    assert "commit `abc1234 (main)`" in render_data_quality(result)
    assert build_agent_payload(result)["commit"] == "abc1234 (main)"


async def test_outputs_omit_commit_when_absent(monkeypatch):
    result = await _dry_result(monkeypatch)
    result.git_commit = None

    assert "commit" not in render_brief(result)
    assert "commit" not in render_data_quality(result)
    assert build_agent_payload(result)["commit"] is None
