# ---
# created: 08 October 2026
# author: kaedonkers, Claude Sonnet 5.5
# modified: 08 October 2026
# ---

"""Test the `download` CLI command (token handling, year clamping, dry run, summary) without network access."""
from types import SimpleNamespace
from unittest.mock import patch

from chessmet.cli import cli
from chessmet.download import DownloadResult


# ── token handling ─────────────────────────────────────────────

def _download_args(tmp_path):
    return ["download", "--var", "tas", "-s", "2000", "-e", "2000", "-o", str(tmp_path)]


@patch("chessmet.cli.ChessMetDownloader.download_all_vars", return_value={})
def test_env_token_is_used_without_prompting(mock_dl, runner, tmp_path, monkeypatch):
    monkeypatch.setenv("EIDC_TOKEN", "pat_abc")
    res = runner.invoke(cli, _download_args(tmp_path))
    assert res.exit_code == 0
    mock_dl.assert_called_once()
    assert "WARNING" not in res.output


@patch("chessmet.cli.ChessMetDownloader.download_all_vars", return_value={})
def test_non_pat_token_warns(mock_dl, runner, tmp_path, monkeypatch):
    monkeypatch.setenv("EIDC_TOKEN", "something_else")
    res = runner.invoke(cli, _download_args(tmp_path))
    assert "pat_" in res.output and "WARNING" in res.output


@patch("chessmet.cli.ChessMetDownloader.download_all_vars", return_value={})
def test_missing_token_without_terminal_exits(mock_dl, runner, tmp_path):
    res = runner.invoke(cli, _download_args(tmp_path))  # CliRunner stdin is not a tty
    assert res.exit_code == 1
    assert "EIDC_TOKEN" in res.output
    mock_dl.assert_not_called()


@patch("chessmet.cli.ChessMetDownloader.download_all_vars", return_value={})
def test_missing_token_prompts_on_terminal(mock_dl, runner, tmp_path):
    # CliRunner swaps sys.stdin at invoke time, so fake a terminal via the cli module's sys
    fake_sys = SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True))
    with patch("chessmet.cli.sys", fake_sys):
        res = runner.invoke(cli, _download_args(tmp_path), input="pat_typed\n")
    assert res.exit_code == 0
    mock_dl.assert_called_once()
    assert "pat_typed" not in res.output  # hidden input is not echoed


def test_dry_run_needs_no_token(runner, tmp_path):
    res = runner.invoke(cli, _download_args(tmp_path) + ["--dry-run"])
    assert res.exit_code == 0
    assert "DRY RUN" in res.output


def test_empty_token_at_prompt_exits(runner, tmp_path):
    fake_sys = SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True))
    with patch("chessmet.cli.ChessMetDownloader.download_all_vars") as mock_dl, \
         patch("chessmet.cli.sys", fake_sys):
        res = runner.invoke(cli, _download_args(tmp_path), input="   \n")
    assert res.exit_code == 1
    assert "Empty token" in res.output
    mock_dl.assert_not_called()


# ── year clamping ──────────────────────────────────────────────

def test_download_clamps_years_with_warning(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("EIDC_TOKEN", "pat_abc")
    with patch("chessmet.cli.ChessMetDownloader.download_all_vars", return_value={}) as mock_dl:
        res = runner.invoke(cli, ["download", "--var", "tas", "-s", "1900", "-e", "2100", "-o", str(tmp_path)])
    assert res.exit_code == 0
    assert "start=1961" in res.output and "end=2019" in res.output
    assert mock_dl.call_args.kwargs["start_year"] == 1961
    assert mock_dl.call_args.kwargs["end_year"] == 2019


# ── download --dry-run ─────────────────────────────────────────

def test_dry_run_lists_download_and_skip(runner, tmp_path, make_hdf5_file):
    done = tmp_path / "tas" / "chess-met_tas_gb_1km_daily_20000101-20000131.nc"
    make_hdf5_file(done)
    res = runner.invoke(cli, _download_args(tmp_path) + ["--dry-run"])
    assert res.exit_code == 0
    assert res.output.count("DOWNLOAD") == 11
    assert res.output.count("SKIP") == 1
    assert "20000101-20000131" in res.output

# ── failed-downloads summary ───────────────────────────────────

def test_failed_downloads_are_summarised(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("EIDC_TOKEN", "pat_abc")
    results = {"tas": [
        DownloadResult(url="u1", filepath=tmp_path / "ok.nc", success=True, size_bytes=1),
        DownloadResult(url="u2", filepath=tmp_path / "bad.nc", success=False, error="HTTP 500"),
    ]}
    with patch("chessmet.cli.ChessMetDownloader.download_all_vars", return_value=results):
        res = runner.invoke(cli, _download_args(tmp_path))
    assert res.exit_code == 0
    assert "1 succeeded, 1 failed" in res.output
    assert "tas/bad.nc" in res.output and "HTTP 500" in res.output
    assert "ok.nc" not in res.output
