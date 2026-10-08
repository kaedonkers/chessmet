# ---
# created: 08 October 2026
# author: kaedonkers, Claude Sonnet 5.5
# modified: 08 October 2026
# ---

"""Test the --start/--end period handling: YYYY / YYYYMM / YYYYMMDD parsing, how omitted
values are filled in, and how the CLI and library apply them. No network access."""
from unittest.mock import patch

import pytest

from chessmet.cli import cli
from chessmet.download import month_range, parse_period, resolve_months


# ── parsing and defaults (pure functions) ──────────────────────

@pytest.mark.parametrize("text, end, expected", [
    ("2000", False, (2000, 1)),
    ("2000", True, (2000, 12)),
    ("200003", False, (2000, 3)),
    ("200003", True, (2000, 3)),
    ("20000315", False, (2000, 3)),
    ("20000315", True, (2000, 3)),
    (2000, True, (2000, 12)),
])
def test_parse_period(text, end, expected):
    assert parse_period(text, end=end) == expected


@pytest.mark.parametrize("text", ["20", "20000", "2000-03", "abc", "200013", "200000", "20000230", "20000300"])
def test_parse_period_rejects(text):
    with pytest.raises(ValueError):
        parse_period(text)


@pytest.mark.parametrize("args, expected", [
    ((2005, None, None, None), (2005, 2005, 1, 12)),
    ((2005, 2006, None, None), (2005, 2006, 1, 12)),
    ((2005, None, 3, None), (2005, 2005, 3, 3)),
    ((2005, None, 3, 7), (2005, 2005, 3, 7)),
    ((2005, 2006, 3, None), (2005, 2006, 3, 12)),
    ((2005, 2006, 3, 7), (2005, 2006, 3, 7)),
])
def test_resolve_months(args, expected):
    assert resolve_months(*args) == expected


def test_month_range_crosses_year_boundary():
    assert list(month_range((2000, 11), (2001, 2))) == [(2000, 11), (2000, 12), (2001, 1), (2001, 2)]


# ── CLI: download and status ───────────────────────────────────

@pytest.mark.parametrize("start, end, expected", [
    ("2005", None, ((2005, 1), (2005, 12))),
    ("200503", None, ((2005, 3), (2005, 3))),
    ("20050315", "20050720", ((2005, 3), (2005, 7))),
    ("200511", "2006", ((2005, 11), (2006, 12))),
])
def test_download_start_end_formats(runner, tmp_path, monkeypatch, start, end, expected):
    monkeypatch.setenv("EIDC_TOKEN", "pat_abc")
    args = ["download", "--var", "tas", "-s", start, "-o", str(tmp_path)] + (["-e", end] if end else [])
    with patch("chessmet.cli.ChessMetDownloader.download_all_vars", return_value={}) as mock_dl:
        res = runner.invoke(cli, args)
    assert res.exit_code == 0, res.output
    kw = mock_dl.call_args.kwargs
    assert ((kw["start_year"], kw["start_month"]), (kw["end_year"], kw["end_month"])) == expected


@pytest.mark.parametrize("bad", ["20", "2005-03", "200513", "20050231"])
def test_bad_start_is_a_usage_error(runner, tmp_path, bad):
    res = runner.invoke(cli, ["download", "--var", "tas", "-s", bad, "-o", str(tmp_path), "--dry-run"])
    assert res.exit_code == 2
    assert "Invalid value for '-s'" in res.output


def test_start_after_end_is_a_usage_error(runner, tmp_path):
    res = runner.invoke(cli, ["download", "--var", "tas", "-s", "200607", "-e", "200506", "-o", str(tmp_path), "--dry-run"])
    assert res.exit_code == 2
    assert "after --end" in res.output


def test_dry_run_lists_only_requested_months(runner, tmp_path):
    res = runner.invoke(cli, ["download", "--var", "tas", "-s", "20000315", "-e", "200005", "-o", str(tmp_path), "--dry-run"])
    assert res.exit_code == 0
    assert res.output.count("DOWNLOAD") == 3
    assert "20000301-20000331" in res.output and "20000501-20000531" in res.output
    assert "20000201" not in res.output and "20000601" not in res.output


def test_status_accepts_month_formats(runner, tmp_path):
    res = runner.invoke(cli, ["status", "--var", "tas", "-s", "20000315", "-e", "200005", "-o", str(tmp_path)])
    assert res.exit_code == 0
    assert "Period: 2000-03 – 2000-05" in res.output
    assert "3 missing" in res.output


# ── library ────────────────────────────────────────────────────

@patch("requests.Session.get")
def test_start_month_alone_downloads_one_month(mock_get, downloader, temp_dir, mock_response):
    mock_get.return_value = mock_response
    results = downloader.download_all_vars(vars_=["tas"], start_year=1991, start_month=3, outdir=temp_dir, parallel=False)
    assert [r.filepath.name for r in results["tas"]] == ["chess-met_tas_gb_1km_daily_19910301-19910331.nc"]


@patch("requests.Session.get")
def test_year_only_downloads_twelve_months(mock_get, downloader, temp_dir, mock_response):
    mock_get.return_value = mock_response
    results = downloader.download_all_vars(vars_=["tas"], start_year=1991, outdir=temp_dir, parallel=False)
    assert len(results["tas"]) == 12
