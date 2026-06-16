"""CLI 入口參數驗證——壞值在打網路/載 settings 前就攔下（typer.BadParameter）。"""

from __future__ import annotations

from typer.testing import CliRunner

from cyber_sages.cli.app import app

runner = CliRunner()


def test_cli_aggression_bogus_rejected():
    # --aggression 只接受 conservative / aggressive（不給＝大師會堂）；壞值須非零退出並提示合法值。
    result = runner.invoke(app, ["analyze", "NVDA", "--aggression", "bogus"])
    assert result.exit_code != 0
    assert "aggressive" in result.output


def test_cli_horizon_bogus_rejected():
    result = runner.invoke(app, ["analyze", "NVDA", "--horizon", "bogus"])
    assert result.exit_code != 0
    assert "value" in result.output
