"""Run 輸出的程式版本標記（issue #7）：brief / data_quality / verdict.json 都帶 commit。"""

import re
import subprocess

from cyber_sages.config import load_settings
from cyber_sages.pipeline import _git_commit, run_pipeline
from cyber_sages.report import build_agent_payload, render_brief, render_data_quality
from tests.test_pipeline_dry import FakeGateway


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


def _patch_git(monkeypatch, responses: dict[str, str]) -> None:
    """以旗標關鍵字罐頭化 git 輸出；缺項 → returncode 1（模擬該指令失敗）。"""

    def fake_run(cmd, **kwargs):
        for key, out in responses.items():
            if key in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)


def test_git_commit_marks_dirty(monkeypatch):
    _patch_git(monkeypatch, {"--short": "abc1234\n", "status": " M x.py\n",
                             "--abbrev-ref": "main\n"})
    assert _git_commit() == "abc1234+dirty (main)"


def test_git_commit_clean_with_branch(monkeypatch):
    _patch_git(monkeypatch, {"--short": "abc1234\n", "status": "",
                             "--abbrev-ref": "main\n"})
    assert _git_commit() == "abc1234 (main)"


def test_git_commit_detached_head(monkeypatch):
    # detached HEAD：rev-parse --abbrev-ref 回 "HEAD"，不帶括號尾註
    _patch_git(monkeypatch, {"--short": "abc1234\n", "status": "",
                             "--abbrev-ref": "HEAD\n"})
    assert _git_commit() == "abc1234"


async def _dry_result(patch_tw_provider):
    patch_tw_provider(etf=True)
    return await run_pipeline("0050", load_settings(), FakeGateway(),  # type: ignore[arg-type]
                              n_sages=2, skip_debate=True, include_macro=False)


async def test_commit_stamped_into_outputs(patch_tw_provider):
    result = await _dry_result(patch_tw_provider)
    result.git_commit = "abc1234 (main)"  # 固定值：斷言不依賴當前 repo 狀態

    assert "commit `abc1234 (main)`" in render_brief(result)
    assert "commit `abc1234 (main)`" in render_data_quality(result)
    assert build_agent_payload(result)["commit"] == "abc1234 (main)"


async def test_outputs_omit_commit_when_absent(patch_tw_provider):
    result = await _dry_result(patch_tw_provider)
    result.git_commit = None

    assert "commit" not in render_brief(result)
    assert "commit" not in render_data_quality(result)
    assert build_agent_payload(result)["commit"] is None
