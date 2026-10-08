# ---
# created: 08 October 2026
# author: kaedonkers, Claude Sonnet 5.5
# modified: 08 October 2026
# ---

"""Test the CLI (clean, status, token handling) without network access."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from chessmet.cli import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def populated(tmp_path, make_hdf5_file):
    """tas: 1 complete, 1 truncated, 1 .part, plus a stray file; precip: 1 complete."""
    tas = tmp_path / "tas"
    precip = tmp_path / "precip"
    make_hdf5_file(tas / "a.nc")
    make_hdf5_file(tas / "b.nc", size=4000)
    (tas / "c.nc.part").write_bytes(b"partial")
    (tas / ".DS_Store").write_bytes(b"x")
    make_hdf5_file(precip / "d.nc")
    return tmp_path


def names(path):
    return sorted(p.name for p in path.iterdir())


# ── clean ──────────────────────────────────────────────────────

def test_clean_dry_run_deletes_nothing(runner, populated):
    res = runner.invoke(cli, ["clean", "-o", str(populated), "--dry-run"])
    assert res.exit_code == 0
    assert "Dry run" in res.output and "4 files" in res.output
    assert len(names(populated / "tas")) == 4
    assert len(names(populated / "precip")) == 1


def test_clean_removes_data_files_but_keeps_folders_by_default(runner, populated):
    res = runner.invoke(cli, ["clean", "-o", str(populated), "--yes"])
    assert res.exit_code == 0
    assert names(populated / "tas") == [".DS_Store"]
    assert (populated / "precip").is_dir() and names(populated / "precip") == []


def test_clean_prompt_can_be_declined(runner, populated):
    res = runner.invoke(cli, ["clean", "-o", str(populated)], input="n\n")
    assert res.exit_code == 0
    assert len(names(populated / "tas")) == 4


def test_clean_incomplete_only_keeps_complete_files(runner, populated):
    res = runner.invoke(cli, ["clean", "-o", str(populated), "--incomplete-only", "--yes"])
    assert res.exit_code == 0
    assert names(populated / "tas") == [".DS_Store", "a.nc"]
    assert names(populated / "precip") == ["d.nc"]


def test_clean_var_limits_scope(runner, populated):
    runner.invoke(cli, ["clean", "-o", str(populated), "--var", "precip", "--yes"])
    assert names(populated / "precip") == []
    assert len(names(populated / "tas")) == 4


def test_clean_remove_dirs_keeps_folder_with_other_files(runner, populated):
    res = runner.invoke(cli, ["clean", "-o", str(populated), "--remove-dirs", "--yes"])
    assert res.exit_code == 0
    assert not (populated / "precip").exists()
    assert names(populated / "tas") == [".DS_Store"]
    assert "Kept" in res.output


def test_clean_force_removes_folders_with_other_files(runner, populated):
    res = runner.invoke(cli, ["clean", "-o", str(populated), "--force", "--yes"])
    assert res.exit_code == 0
    assert not (populated / "tas").exists()
    assert not (populated / "precip").exists()


def test_clean_force_with_incomplete_only_is_rejected(runner, populated):
    res = runner.invoke(cli, ["clean", "-o", str(populated), "--force", "--incomplete-only", "--yes"])
    assert res.exit_code != 0
    assert len(names(populated / "tas")) == 4


def test_clean_never_touches_unknown_folders(runner, tmp_path, make_hdf5_file):
    other = tmp_path / "documents"
    make_hdf5_file(other / "keep.nc")
    res = runner.invoke(cli, ["clean", "-o", str(tmp_path), "--force", "--yes"])
    assert "Nothing to clean" in res.output
    assert (other / "keep.nc").exists()


# ── status ─────────────────────────────────────────────────────

def test_status_counts_present_incomplete_part_and_missing(runner, tmp_path, make_hdf5_file):
    tas = tmp_path / "tas"
    base = "chess-met_tas_gb_1km_daily_2000{m}01-2000{m}{d}.nc"
    make_hdf5_file(tas / base.format(m="01", d="31"))
    make_hdf5_file(tas / base.format(m="02", d="29"), size=4000)
    (tas / (base.format(m="03", d="31") + ".part")).write_bytes(b"partial")

    res = runner.invoke(cli, ["status", "-o", str(tmp_path), "--var", "tas", "-s", "2000", "-e", "2000"])
    assert res.exit_code == 0
    assert "1 present" in res.output
    assert "2 incomplete" in res.output
    assert "9 missing" in res.output


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
